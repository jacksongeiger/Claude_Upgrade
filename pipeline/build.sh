#!/usr/bin/env bash
# Pipeline build driver — runs milestones of spec.json the way loop/run.sh runs
# nights: one fresh `claude -p` planner per milestone (prompts/build.md), the
# same executors/reviewers/guards/allowlist/cost meter as Nightshift, and a
# script — accept.py — deciding whether the milestone is done.
#
#   build.sh [--project DIR] [--milestone ID|next|all] [--cap USD] [--per-milestone USD] [--kill]
#
# State: <project>/.pipeline/build/state.json (milestone.py), one build branch
# `build/<date>` in the worktree <project>/.pipeline/wt/build, a tag
# `build/<milestone>` per completed milestone. The human merges the branch to
# main; this script never does. Exit 0 when every requested milestone is done,
# 3 when one is blocked (details in its summary.md), 4 on infra, 1 on usage.

set -uo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LOOP_KIT="$(cd "$KIT/../loop" && pwd -P)"
CLAUDE_BIN="${NIGHTSHIFT_CLAUDE:-claude}"

PROJECT="$PWD"; WHICH="next"; CAP=""; PER_MS=""; KILL=0; MAX_MS=0; ACCEPT_ONLY=""
while [ $# -gt 0 ]; do
    case "$1" in
        --project) PROJECT="$2"; shift 2 ;;
        --milestone) WHICH="$2"; shift 2 ;;
        --cap) CAP="$2"; shift 2 ;;
        --per-milestone) PER_MS="$2"; shift 2 ;;
        --max-milestones) MAX_MS="$2"; shift 2 ;;
        --kill) KILL=1; shift ;;
        --accept-only) ACCEPT_ONLY="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

PROJECT=$(cd "$PROJECT" && pwd -P)
PIPE="$PROJECT/.pipeline"; BUILD="$PIPE/build"; RUN="$PIPE/run"; WT="$PIPE/wt/build"
LOOP="$PROJECT/.loop"; STATE="$BUILD/state.json"; EVLOG="$PIPE/events.log"; EVENTS="$PIPE/events.jsonl"
LEDGER="$PIPE/ledger.jsonl"; SPEC="$PROJECT/spec.json"
SLUG="$(basename "$PROJECT")-$(printf '%s' "$PROJECT" | sha256sum | cut -c1-8)"
NSDIR="$HOME/.claude/nightshift/$SLUG"; CONFIG="$NSDIR/config.json"
DATE=$(date -u +%Y-%m-%d); BRANCH="build/$DATE"
STEP="init"; STOP_REASON=""; CHILD_PGID=""; SPENT=0

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
note() { printf '%s [%s] %s\n' "$(ts)" "$STEP" "$*" >> "$RUN/build.log" 2>/dev/null || true; }
event() { local name="$1"; shift; printf '%s %s %s\n' "$(ts)" "$name" "$*" >> "$EVLOG"; jq -cn --arg ts "$(ts)" --arg e "$name" --arg d "$*" '{ts:$ts,event:$e,detail:$d}' >> "$EVENTS" 2>/dev/null || true; }
cfg() { jq -r "$1" "$CONFIG"; }
ledger() { jq -cn --arg ts "$(ts)" --arg s "build" --arg id "$1" --argjson c "$2" '{ts:$ts,stage:$s,id:$id,cost_usd:$c}' >> "$LEDGER"; }
kill_child() { [ -n "${CHILD_PGID:-}" ] || CHILD_PGID=$(cat "$RUN/child.pgid" 2>/dev/null || true); if [ -n "$CHILD_PGID" ]; then kill -TERM -- "-$CHILD_PGID" 2>/dev/null || true; sleep 2; kill -KILL -- "-$CHILD_PGID" 2>/dev/null || true; fi; }
on_exit() { local rc=$?; trap - EXIT INT TERM; [ -n "$STOP_REASON" ] || { STOP_REASON="crashed-at-$STEP"; event STOP "reason=$STOP_REASON rc=$rc"; }; kill_child; rm -f "$RUN/build.pid" "$LOOP/run/loop.pid" 2>/dev/null; exit "$rc"; }
stop() { STOP_REASON="$1"; event STOP "reason=$1 ${2:-}"; note "STOP $1 ${2:-}"; }

if [ "$KILL" = "1" ]; then
    for f in "$RUN/build.pid" "$RUN/child.pgid"; do
        p=$(cat "$f" 2>/dev/null || true); [ -n "$p" ] || continue
        pg=$(ps -o pgid= -p "$p" 2>/dev/null | tr -d ' ' || echo "$p")
        kill -TERM -- "-$pg" 2>/dev/null && echo "sent TERM to $pg" || true
    done
    exit 0
fi

# ---- preflight -------------------------------------------------------------
command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }
[ -f "$SPEC" ] || { echo "no spec.json in $PROJECT — run /jg-spec first" >&2; exit 1; }
mkdir -p "$RUN" "$BUILD" "$LOOP/run" "$PIPE/wt"
python3 "$KIT/spec_check.py" "$SPEC" >/dev/null || { echo "spec.json does not validate — run spec_check.py" >&2; exit 1; }
# the validation gate: a spec that carries a verdict must still be a GO whose budget it has not outgrown
if jq -e '.validation' "$SPEC" >/dev/null 2>&1; then
    python3 "$KIT/spec_check.py" "$SPEC" --gate-validate >"$PROJECT/.pipeline/run/gate-validate.out" 2>&1 || { cat "$PROJECT/.pipeline/run/gate-validate.out" >&2; echo "validation gate refused — see above" >&2; exit 3; }
fi
if [ ! -f "$CONFIG" ]; then
    python3 "$KIT/mkconfig.py" --project "$PROJECT" --spec "$SPEC" --out "$CONFIG"; MKRC=$?
    # 3 = config written, manifest not signed yet (a pinned judge the build has still to create); fine for the build
    [ "$MKRC" = 0 ] || [ "$MKRC" = 3 ] || { echo "could not write a Nightshift config" >&2; exit 4; }
fi
[ -f "$NSDIR/manifest.sha256" ] || python3 "$LOOP_KIT/score.py" --manifest-write "$NSDIR/manifest.sha256" --config "$CONFIG" >/dev/null
[ -f "$NSDIR/goal.md" ] || cp "$PROJECT/GOAL.md" "$NSDIR/goal.md" 2>/dev/null || true
if [ -f "$RUN/build.pid" ] && kill -0 "$(cat "$RUN/build.pid")" 2>/dev/null; then echo "a build is already running" >&2; exit 1; fi
echo $$ > "$RUN/build.pid"; echo $$ > "$LOOP/run/loop.pid"   # hooks (events, budget gate) key on loop.pid
trap on_exit EXIT INT TERM

MAIN=$(cfg '.main_branch // "main"'); SETUP_CMD=$(cfg '.setup_cmd // ""'); TEST_CMD=$(cfg '.test_cmd // ""')
MAX_FANOUT=$(cfg '.max_fanout // 3'); OPUS=$(cfg '.opus_allowed // true'); CHILD_TURNS=$(cfg '.child_max_turns // 120')
CHILD_TIMEOUT=$(cfg '.child_timeout_min // 60')
[ -n "$PER_MS" ] || PER_MS=$(cfg '.per_iter_max_usd // 8')
[ -n "$CAP" ] || CAP=$(jq -r '.budget.build_usd // 40' "$SPEC")
SPENT=$(jq -r '.spent_usd // 0' "$STATE" 2>/dev/null || echo 0); [ -n "$SPENT" ] && [ "$SPENT" != "null" ] || SPENT=0

# ---- branch + worktree -----------------------------------------------------
STEP="worktree"
cd "$PROJECT"
git rev-parse --verify -q "$MAIN" >/dev/null || { echo "branch $MAIN not found" >&2; exit 1; }
EXISTING=$(jq -r '.branch // empty' "$STATE" 2>/dev/null || true)
[ -n "$EXISTING" ] && git rev-parse --verify -q "$EXISTING" >/dev/null && BRANCH="$EXISTING"
git rev-parse --verify -q "$BRANCH" >/dev/null || { git branch "$BRANCH" "$MAIN"; note "created $BRANCH from $MAIN"; }
if [ ! -e "$WT/.git" ]; then
    git worktree add "$WT" "$BRANCH" >>"$RUN/build.log" 2>&1 || { echo "worktree add failed" >&2; exit 4; }
    [ -f "$WT/.gitmodules" ] && git -C "$WT" submodule update --init --recursive >>"$RUN/build.log" 2>&1
    [ -n "$SETUP_CMD" ] && (cd "$WT" && bash -c "$SETUP_CMD") >>"$RUN/build.log" 2>&1 || true
fi
python3 - "$STATE" "$BRANCH" <<'PY'
import json,sys,pathlib
p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text()) if p.exists() else {}
d.setdefault("milestones",{}); d["branch"]=sys.argv[2]; p.write_text(json.dumps(d,indent=2))
PY
# the worktree reads the spec, goal and tokens from the project: the human
# edits those between milestones, so they are refreshed at every start;
# the backlog is the loop's own state and is only seeded once
mkdir -p "$WT/.loop" "$WT/.loop/run"
for f in spec.json GOAL.md design-tokens.json DESIGN.md; do [ -f "$PROJECT/$f" ] && cp "$PROJECT/$f" "$WT/$f"; done
[ -f "$PROJECT/.loop/backlog.yaml" ] && [ ! -f "$WT/.loop/backlog.yaml" ] && cp "$PROJECT/.loop/backlog.yaml" "$WT/.loop/backlog.yaml"
(cd "$WT" && git add -f .loop/backlog.yaml GOAL.md spec.json 2>/dev/null; [ -f design-tokens.json ] && git add -f design-tokens.json; [ -f DESIGN.md ] && git add -f DESIGN.md; git diff --cached --quiet || git commit -q -m "build: refresh spec, goal and backlog from the project") >>"$RUN/build.log" 2>&1 || true

# ---- child settings (same as Nightshift) ------------------------------------
STEP="child-settings"
CHILD_SETTINGS="$RUN/child-settings.json"
ALLOW_JSON=$(python3 "$LOOP_KIT/allowlist.py" --config "$CONFIG" --kit "$LOOP_KIT")
ALLOW_JSON=$(jq -c --arg k "$KIT" '. + ["Bash(python3 " + $k + "/*)", "Bash(node " + $k + "/*)", "Bash(bash " + $k + "/*)"]' <<<"$ALLOW_JSON")
jq -n --arg lk "$LOOP_KIT" --argjson allow "$ALLOW_JSON" '{worktree:{baseRef:"head"},permissions:{allow:$allow},
  hooks:{SubagentStart:[{hooks:[{type:"command",command:("bash "+$lk+"/hooks/events.sh"),async:true,timeout:5}]}],
         SubagentStop:[{hooks:[{type:"command",command:("bash "+$lk+"/hooks/events.sh"),async:true,timeout:5}]}],
         PreToolUse:[{matcher:"Agent",hooks:[{type:"command",command:("bash "+$lk+"/hooks/budget-gate.sh"),timeout:5}]}]}}' > "$CHILD_SETTINGS"
AGENTS_JSON=$(python3 "$LOOP_KIT/agents_json.py" --no-ui)
# AGENT_MODEL_FALLBACK=opus swaps every fable-pinned subagent (the reviewer) when fable is out of quota
[ -n "${AGENT_MODEL_FALLBACK:-}" ] && AGENTS_JSON=$(jq -c --arg m "$AGENT_MODEL_FALLBACK" 'map_values(if .model == "fable" then .model = $m else . end)' <<<"$AGENTS_JSON")

# an accepted milestone closes its spec rows in the loop's backlog (worktree and project)
regress_check() {  # regress_check MS ITER_DIR -> prints " m1(exit 2)" per earlier done milestone that no longer accepts, empty when all hold
    local ms="$1" it="$2" prev rc out=""
    for prev in $(jq -r --arg ms "$ms" '.milestones | to_entries[] | select(.value.status=="done" and .key!=$ms) | .key' "$STATE" 2>/dev/null); do
        python3 "$KIT/accept.py" --spec "$SPEC" --milestone "$prev" --workdir "$WT" --out "$it/acceptance.regress.$prev.json" >>"$RUN/build.log" 2>&1; rc=$?
        [ "$rc" = 0 ] || out="$out $prev(exit $rc)"
    done
    printf '%s' "$out"
}

mark_rows_done() {
    # the worktree's copy only: the project's copy receives it when the human
    # merges the build branch (editing both would conflict at that merge)
    b="$WT/.loop/backlog.yaml"
    [ -f "$b" ] && python3 "$KIT/mark_done.py" --spec "$SPEC" --milestone "$1" --backlog "$b" >>"$RUN/build.log" 2>&1 || true
    (cd "$WT" && git add -f .loop/backlog.yaml 2>/dev/null; git diff --cached --quiet || git commit -q -m "build: $1 accepted, backlog rows closed") >>"$RUN/build.log" 2>&1 || true
}

# ---- accept-only: re-run the script's verdict on the existing worktree -------
if [ -n "$ACCEPT_ONLY" ]; then
    STEP="accept"; MS="$ACCEPT_ONLY"; ITER_DIR="$WT/.pipeline/build/$MS"; mkdir -p "$ITER_DIR"
    python3 "$KIT/accept.py" --spec "$SPEC" --milestone "$MS" --workdir "$WT" --out "$ITER_DIR/acceptance.final.json" 2>>"$RUN/build.log"; ARC=$?
    REGRESSED=""
    if [ "$ARC" = "0" ]; then REGRESSED=$(regress_check "$MS" "$ITER_DIR"); [ -z "$REGRESSED" ] || { ARC=2; echo "regression of an earlier milestone:$REGRESSED"; }; fi
    case "$ARC" in
      0) python3 "$KIT/milestone.py" --spec "$SPEC" --state "$STATE" set "$MS" done --note "accepted (accept-only)" >/dev/null
         mark_rows_done "$MS"
         git -C "$WT" tag -f "build/$MS" >/dev/null 2>&1 || true; event MILESTONE_DONE "id=$MS (accept-only)"; stop complete "accept-only $MS done"; exit 0 ;;
      2) python3 "$KIT/milestone.py" --spec "$SPEC" --state "$STATE" set "$MS" blocked --note "acceptance failed (accept-only)${REGRESSED:+: regression of an earlier milestone:$REGRESSED}" >/dev/null
         event MILESTONE_BLOCKED "id=$MS (accept-only)${REGRESSED:+ regression:$REGRESSED}"; stop complete "accept-only $MS: checks failed"; exit 3 ;;
      *) event MILESTONE_INFRA "id=$MS exit=$ARC (accept-only)"; stop infra "accept-only $MS exit $ARC"; exit 4 ;;
    esac
fi

# ---- milestones -------------------------------------------------------------
DONE_COUNT=0; RC_FINAL=0
while :; do
    STEP="pick"
    if [ "$WHICH" = "next" ] || [ "$WHICH" = "all" ]; then
        MS=$(python3 "$KIT/milestone.py" --spec "$SPEC" --state "$STATE" next)
    else
        MS="$WHICH"
    fi
    [ "$MS" != "none" ] || { stop complete "no milestone left to build"; break; }
    if jq -e --arg c "$CAP" --arg s "$SPENT" '(($s|tonumber) + 1.0) >= ($c|tonumber)' >/dev/null <<<'{}'; then stop cap "spent $SPENT of $CAP"; RC_FINAL=3; break; fi
    ITER=$(( $(jq -r '.iters // 0' "$STATE") + 1 ))
    ITER_DIR="$WT/.pipeline/build/$MS"; mkdir -p "$ITER_DIR/tasks"
    python3 - "$STATE" "$ITER" "$MS" <<'PY'
import json,sys,pathlib
p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text()); d["iters"]=int(sys.argv[2])
# attempts per milestone: the retro's first-try acceptance rate reads this
a=d.setdefault("attempts",{}); a[sys.argv[3]]=int(a.get(sys.argv[3],0))+1
p.write_text(json.dumps(d,indent=2))
PY
    event MILESTONE_START "id=$MS iter=$ITER"
    python3 "$KIT/milestone.py" --spec "$SPEC" target "$MS" --out "$ITER_DIR/target.json" >/dev/null
    MS_TITLE=$(jq -r --arg m "$MS" '.milestones[] | select(.id==$m) | .title' "$SPEC")
    FEATURES_JSON=$(jq -c --arg m "$MS" '[.features[] | select(.milestone==$m)]' "$SPEC")
    PREV=$( [ -f "$ITER_DIR/summary.md" ] && cat "$ITER_DIR/summary.md" || echo "none" )
    [ -f "$ITER_DIR/summary.md" ] && mv "$ITER_DIR/summary.md" "$ITER_DIR/summary.prev.md"
    BUDGET=$(jq -n --argjson p "$PER_MS" --argjson c "$CAP" --argjson s "$SPENT" '[$p, ($c - $s)] | min')
    cp "$PROJECT/GOAL.md" "$ITER_DIR/goal.md" 2>/dev/null || cp "$NSDIR/goal.md" "$ITER_DIR/goal.md"
    TOKENS_PATH=$( [ -f "$WT/design-tokens.json" ] && echo "$WT/design-tokens.json" || echo "none" )
    INSPIRATION_PATH=$( [ -f "$PROJECT/.pipeline/ui/inspire/inspiration.json" ] && echo "$PROJECT/.pipeline/ui/inspire/inspiration.json" || echo "none" )
    ARCH=$( [ -f "$WT/ARCHITECTURE.md" ] && echo "$WT/ARCHITECTURE.md" || echo "none" )

    STEP="prompt"
    python3 - "$KIT/prompts/build.md" "$ITER_DIR/prompt.md" <<PY
import sys,pathlib,json
t=pathlib.Path(sys.argv[1]).read_text()
subs={"MILESTONE":"$MS","MILESTONE_TITLE":$(jq -Rn --arg s "$MS_TITLE" '$s'),"PROJECT_DIR":"$PROJECT","BUILD_WT":"$WT","BUILD_BRANCH":"$BRANCH",
 "KIT":"$KIT","LOOP_KIT":"$LOOP_KIT","CONFIG":"$CONFIG","GOAL":"$ITER_DIR/goal.md","GOAL_TEXT":pathlib.Path("$ITER_DIR/goal.md").read_text(),
 "TARGET_JSON":pathlib.Path("$ITER_DIR/target.json").read_text(),"FEATURES_JSON":$(jq -Rn --arg s "$FEATURES_JSON" '$s'),
 "TOKENS":"$TOKENS_PATH","INSPIRATION":"$INSPIRATION_PATH","ARCH":"$ARCH","PREV_SUMMARY":$(jq -Rn --arg s "$PREV" '$s'),"ITER_DIR":"$ITER_DIR","ITER":"$ITER",
 "SETUP_CMD":$(jq -Rn --arg s "$SETUP_CMD" '$s'),"TEST_CMD":$(jq -Rn --arg s "$TEST_CMD" '$s'),"MAX_FANOUT":"$MAX_FANOUT","OPUS_ALLOWED":"$OPUS",
 "BUDGET":"$BUDGET","QUESTIONS":"$WT/.pipeline/questions.md","ACCEPT_EXTRA":""}
for k,v in subs.items(): t=t.replace("{{"+k+"}}",str(v))
pathlib.Path(sys.argv[2]).write_text(t)
PY

    STEP="child"
    rm -f "$RUN/child.pgid"
    # the live meter resumes from state.json: start this child's count at zero
    [ -f "$LOOP/state.json" ] && jq '.live_spend_usd = 0 | .agents = [] | .phase = "PLAN"' "$LOOP/state.json" > "$LOOP/state.json.tmp" 2>/dev/null && mv "$LOOP/state.json.tmp" "$LOOP/state.json" || echo '{"live_spend_usd":0,"agents":[],"phase":"PLAN"}' > "$LOOP/state.json"
    ( cd "$WT"
      # the build writes the judges the spec asks for (evals corpus, bench); pins are Nightshift's rule
      export NIGHTSHIFT_CONFIG="$CONFIG" NIGHTSHIFT_KIT="$LOOP_KIT" NIGHTSHIFT_ITER_DIR="$ITER_DIR" NIGHTSHIFT_PINS=off PIPELINE_STAGE=build
      CLAUDE_CONFIG_DIR=$(bash "$LOOP_KIT/child_config.sh" "$NSDIR" "$CHILD_SETTINGS"); if [ -n "$CLAUDE_CONFIG_DIR" ]; then export CLAUDE_CONFIG_DIR; else unset CLAUDE_CONFIG_DIR; fi
      python3 "$LOOP_KIT/spawn.py" --pgid-file "$RUN/child.pgid" --timeout-min "$CHILD_TIMEOUT" -- \
        "$CLAUDE_BIN" -p "$(cat "$ITER_DIR/prompt.md")" \
        --model "${PLANNER_MODEL:-fable}" --max-budget-usd "$BUDGET" --max-turns "$CHILD_TURNS" \
        --permission-mode acceptEdits --permission-prompts none --setting-sources project \
        --settings "$CHILD_SETTINGS" --agents "$AGENTS_JSON" \
        --output-format stream-json --include-hook-events --forward-subagent-text --verbose 2>>"$RUN/build.log"
    ) | tee "$RUN/stream-$MS-$ITER.jsonl" \
      | python3 "$LOOP_KIT/tail.py" --state "$LOOP/state.json" --events "$LOOP/events.jsonl" --pricing "$LOOP_KIT/pricing.json" --iter "$ITER" \
          --live-out "$WT/.loop/run/live.json" --budget "$BUDGET" > "$ITER_DIR/tail.json" 2>>"$RUN/build.log" &
    PIPE_PID=$!
    for _ in $(seq 1 20); do [ -s "$RUN/child.pgid" ] && break; sleep 0.5; done
    CHILD_PGID=$(cat "$RUN/child.pgid" 2>/dev/null || true)
    while kill -0 "$PIPE_PID" 2>/dev/null; do
        sleep 5
        LIVE=$(jq -r '.live_spend_usd // 0' "$LOOP/state.json" 2>/dev/null || echo 0)
        if jq -en --argjson c "$CAP" --argjson s "$SPENT" --argjson l "${LIVE:-0}" '($s + $l) >= $c' >/dev/null 2>&1; then note "cap reached mid-milestone — killing child"; kill_child; break; fi
    done
    wait "$PIPE_PID" 2>/dev/null; CHILD_RC=$?
    RESULT_COST=$(jq -r '.result_cost_usd // 0' "$ITER_DIR/tail.json" 2>/dev/null); [ -n "$RESULT_COST" ] || RESULT_COST=0
    LIVE=$(jq -r '.live_spend_usd // 0' "$ITER_DIR/tail.json" 2>/dev/null); [ -n "$LIVE" ] || LIVE=0
    CHARGE=$(jq -n --argjson r "$RESULT_COST" --argjson l "$LIVE" --argjson p "$PER_MS" 'if $r > 0 then $r else ([$l,$p]|max) end')
    SPENT=$(jq -n --argjson s "$SPENT" --argjson c "$CHARGE" '$s + $c')
    python3 - "$STATE" "$SPENT" <<'PY'
import json,sys,pathlib
p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text()); d["spent_usd"]=float(sys.argv[2]); p.write_text(json.dumps(d,indent=2))
PY
    ledger "$MS" "$CHARGE"
    event CHILD_DONE "milestone=$MS rc=$CHILD_RC charge=$CHARGE"
    # executor worktrees are per-milestone scratch: prune, tagging orphans
    git -C "$PROJECT" worktree list --porcelain | awk '/^worktree /{print substr($0,10)}' | while read -r path; do
        case "$path" in */.claude/worktrees/agent-*) ;; *) continue ;; esac
        br=$(git -C "$path" branch --show-current 2>/dev/null || true)
        if [ -n "$br" ] && ! git -C "$PROJECT" merge-base --is-ancestor "$br" "$BRANCH" 2>/dev/null; then git -C "$PROJECT" tag "build/orphan/$MS-${br#worktree-agent-}" "$br" >/dev/null 2>&1 || true; fi
        git -C "$PROJECT" worktree remove --force "$path" >>"$RUN/build.log" 2>&1 || rm -rf "$path"
        [ -n "$br" ] && git -C "$PROJECT" branch -D "$br" >>"$RUN/build.log" 2>&1 || true
    done
    git -C "$PROJECT" worktree prune >/dev/null 2>&1 || true
    (cd "$WT" && for f in .loop/backlog.yaml .pipeline/questions.md; do [ -f "$f" ] && git add -f "$f"; done; git diff --cached --quiet || git commit -q -m "build: notes after $MS") >>"$RUN/build.log" 2>&1 || true

    # ---- the script decides -------------------------------------------------
    STEP="accept"
    python3 "$KIT/accept.py" --spec "$SPEC" --milestone "$MS" --workdir "$WT" --out "$ITER_DIR/acceptance.final.json" >>"$RUN/build.log" 2>&1; ARC=$?
    # A milestone that passes its own checks can still break an earlier one
    # (Inbox Triage m2 added bench/queue.py, which shadowed the stdlib
    # `queue` for m1's bench; the ship check was the first to see it). Every
    # earlier done milestone is re-accepted before this one counts.
    REGRESSED=""
    if [ "$ARC" = "0" ]; then REGRESSED=$(regress_check "$MS" "$ITER_DIR"); [ -z "$REGRESSED" ] || ARC=2; fi

    # ---- the UI check: measured craft, advisory -----------------------------
    # Never changes the milestone's outcome (the human chose "never block,
    # always report"): every screen is measured as it is built and the
    # findings land as `ui` rows in the worktree's backlog. The judge runs
    # while the budget has room for it; its cost is charged like any other.
    if jq -e '(.needs // []) | index("ui")' "$SPEC" >/dev/null 2>&1; then
        STEP="ui-check"
        UI_JUDGE=""; jq -en --argjson c "$CAP" --argjson s "$SPENT" '($c - $s) >= 3' >/dev/null 2>&1 && UI_JUDGE="--judge --judge-budget 0.6"
        python3 "$KIT/ui_check.py" run --workdir "$WT" --label "$MS" --out "$ITER_DIR/ui" --backlog "$WT/.loop/backlog.yaml" \
            --changed-since "$MAIN" --ledger "$LEDGER" $UI_JUDGE >>"$RUN/build.log" 2>&1; UIRC=$?
        UI_COST=$(jq -r '.judge_cost_usd // 0' "$ITER_DIR/ui/check.json" 2>/dev/null); [ -n "$UI_COST" ] || UI_COST=0
        SPENT=$(jq -n --argjson s "$SPENT" --argjson c "$UI_COST" '$s + $c')
        jq --argjson s "$SPENT" '.spent_usd = $s' "$STATE" > "$STATE.tmp" 2>/dev/null && mv "$STATE.tmp" "$STATE"
        event UI_CHECK "id=$MS score=$(jq -r '.score // "none"' "$ITER_DIR/ui/check.json" 2>/dev/null) rc=$UIRC judge=$UI_COST"
        (cd "$WT" && git add -f .loop/backlog.yaml 2>/dev/null; git diff --cached --quiet || git commit -q -m "build: ui check after $MS (advisory)") >>"$RUN/build.log" 2>&1 || true
    fi

    case "$ARC" in
      0) python3 "$KIT/milestone.py" --spec "$SPEC" --state "$STATE" set "$MS" done --note "accepted iter $ITER" >/dev/null
         mark_rows_done "$MS"
         git -C "$WT" tag -f "build/$MS" >/dev/null 2>&1 || true
         event MILESTONE_DONE "id=$MS cost=$CHARGE"; DONE_COUNT=$((DONE_COUNT+1)) ;;
      2) FAILS=$(jq -r '[.features[] | .checks[] | select(.ok==false) | (.type + ":" + (.detail|tostring|.[0:80]))] | join("; ")' "$ITER_DIR/acceptance.final.json" 2>/dev/null || echo "?")
         [ -z "$REGRESSED" ] || FAILS="regression of an earlier milestone:$REGRESSED"
         python3 "$KIT/milestone.py" --spec "$SPEC" --state "$STATE" set "$MS" blocked --note "acceptance failed: $FAILS" >/dev/null
         event MILESTONE_BLOCKED "id=$MS fails=$FAILS"; RC_FINAL=3 ;;
      *) python3 "$KIT/milestone.py" --spec "$SPEC" --state "$STATE" set "$MS" blocked --note "acceptance could not run (exit $ARC)" >/dev/null
         event MILESTONE_INFRA "id=$MS exit=$ARC"; stop infra "accept.py exit $ARC on $MS"; RC_FINAL=4; break ;;
    esac
    [ "$ARC" = "0" ] || { [ "$WHICH" = "all" ] || break; }
    [ "$WHICH" = "next" ] && { stop complete "milestone $MS finished"; break; }
    [ "$MAX_MS" -gt 0 ] && [ "$DONE_COUNT" -ge "$MAX_MS" ] && { stop complete "max milestones"; break; }
    [ "$WHICH" = "all" ] || break
done
[ -n "$STOP_REASON" ] || stop complete "milestone $MS $( [ "$RC_FINAL" = 0 ] && echo done || echo blocked )"
printf 'build branch: %s · spent $%s of $%s · merge with: git merge --no-ff %s\n' "$BRANCH" "$SPENT" "$CAP" "$BRANCH"
exit "$RC_FINAL"
