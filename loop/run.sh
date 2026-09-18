#!/usr/bin/env bash
# Nightshift driver — the Foreman.
#
# Runs the improvement loop for one project: one fresh `claude -p --model fable`
# process per iteration, deterministic scoring after it, arithmetic to decide
# keep / reset / stop. This script never makes a creative decision and never
# reads a model's opinion; it reads files and numbers.
#
#   run.sh --cap USD [--hours N] [--project DIR] [--iters N] [--dryrun]
#   run.sh --kill  [--project DIR]
#
# Every exit path — including a crash — writes state.stop_reason, appends a
# STOP line to events.log (which Monitor and the statusline read), notifies,
# and removes loop.pid. See on_exit.
#
# Contracts: loop/README.md. Test double: set NIGHTSHIFT_CLAUDE to a script that
# accepts the same arguments as `claude` and emits stream-json.

set -Eeuo pipefail

KIT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STEP="args"
ITER=0
STOP_REASON=""
CLAUDE_BIN="${NIGHTSHIFT_CLAUDE:-claude}"
PROJECT="$PWD"
CAP=""
HOURS=""
MAX_ITERS=0
DRYRUN=0
KILL=0

while [ $# -gt 0 ]; do
    case "$1" in
        --cap) CAP="$2"; shift 2 ;;
        --hours) HOURS="$2"; shift 2 ;;
        --project) PROJECT="$2"; shift 2 ;;
        --iters) MAX_ITERS="$2"; shift 2 ;;
        --dryrun) DRYRUN=1; shift ;;
        --kill) KILL=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

PROJECT=$(cd "$PROJECT" && pwd -P)
LOOP="$PROJECT/.loop"
RUN="$LOOP/run"
STATE="$LOOP/state.json"
EVENTS="$LOOP/events.jsonl"
EVLOG="$LOOP/events.log"
HEART="$LOOP/heartbeat"
SLUG="$(basename "$PROJECT")-$(printf '%s' "$PROJECT" | sha256sum | cut -c1-8)"
NSDIR="$HOME/.claude/nightshift/$SLUG"
CONFIG="$NSDIR/config.json"
MANIFEST="$NSDIR/manifest.sha256"
GOAL="$NSDIR/goal.md"

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
note() { printf '%s [%s] %s\n' "$(ts)" "$STEP" "$*" >> "$RUN/loop.log" 2>/dev/null || true; }
beat() { touch "$HEART" 2>/dev/null || true; }

event() {  # event NAME [key=value ...]  — machine + human lines
    local name="$1"; shift
    local ts; ts=$(ts)
    local json; json=$(jq -cn --arg ts "$ts" --arg e "$name" --argjson iter "$ITER" '{ts:$ts,event:$e,iter:$iter}')
    local human="$ts $(printf '%s' "$name" | tr a-z A-Z)"
    for kv in "$@"; do
        local k="${kv%%=*}" v="${kv#*=}"
        json=$(printf '%s' "$json" | jq -c --arg k "$k" --arg v "$v" '. + {($k): $v}')
        human="$human $k=$v"
    done
    printf '%s\n' "$json" >> "$EVENTS"
    printf '%s\n' "$human" >> "$EVLOG"
}

state_set() {  # state_set [--arg k v ...] '<jq filter>' — read-modify-write, atomic
    local tmp; tmp=$(mktemp "$LOOP/.state.XXXXXX")
    if jq "$@" "$STATE" > "$tmp"; then mv "$tmp" "$STATE"; else rm -f "$tmp"; return 1; fi
}

cfg() { jq -r "$1" "$CONFIG"; }

notify() {  # macOS notification; silent elsewhere
    local title="$1" body="$2"
    if command -v osascript >/dev/null 2>&1; then
        osascript -e "display notification \"$body\" with title \"$title\"" >/dev/null 2>&1 || true
    fi
    printf '\a' 2>/dev/null || true
}

stop() {  # stop REASON [detail]
    STOP_REASON="$1"
    local detail="${2:-}"
    STEP="STOP"
    state_set --arg r "$1" --arg d "$detail" '.phase="STOPPED" | .stop_reason=$r | .stop_detail=$d | .step="STOP"'
    event STOP reason="$1" ${detail:+detail="$detail"}
    notify "Nightshift stopped: $1" "$detail"
    note "STOP $1 $detail"
}

on_exit() {
    local rc=$?
    trap - EXIT INT TERM
    if [ -z "$STOP_REASON" ]; then
        # Dying with a child in flight (kill, TERM, crash): the money is spent
        # even though the COST step never ran — charge it pessimistically.
        if [ "$STEP" = "child" ] && [ -f "$STATE" ]; then
            jq --argjson p "${PER_ITER:-0}" '.spent_usd = ((.spent_usd // 0) + ([(.live_spend_usd // 0), $p] | max)) | .live_spend_usd = 0' "$STATE" > "$STATE.tmp" 2>/dev/null && mv "$STATE.tmp" "$STATE" || true
        fi
        local last; last=$(tail -n 1 "$RUN/loop.log" 2>/dev/null || echo "")
        stop "crashed-at-$STEP" "iter $ITER exit $rc — $last" || true
    fi
    # Never leave a child spending after the driver is gone.
    [ -n "${CHILD_PGID:-}" ] || CHILD_PGID=$(cat "$RUN/child.pgid" 2>/dev/null || true)
    kill_child
    rm -f "$RUN/loop.pid" 2>/dev/null || true
    exit 0
}

# Executor worktrees (.claude/worktrees/agent-*, branch worktree-agent-*) are
# per-subtask scratch. After the child exits their work is either merged
# (in the loop branch), rejected (tagged by merge.sh) or abandoned. Anything
# not reachable from the loop branch or a tag is tagged nightshift/orphan/…
# first so nothing is lost, then the worktree and branch go.
prune_executor_worktrees() {
    local line path br
    git -C "$PROJECT" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print substr($0,10)}' | while read -r path; do
        case "$path" in */.claude/worktrees/agent-*) ;; *) continue ;; esac
        br=$(git -C "$path" branch --show-current 2>/dev/null || true)
        if [ -n "$br" ] && ! git -C "$PROJECT" merge-base --is-ancestor "$br" "$BRANCH" 2>/dev/null \
           && [ -z "$(git -C "$PROJECT" tag --points-at "$br" 2>/dev/null)" ]; then
            git -C "$PROJECT" tag "nightshift/orphan/${ITER}-${br#worktree-agent-}" "$br" >/dev/null 2>&1 || true
        fi
        git -C "$PROJECT" worktree remove --force "$path" >>"$RUN/loop.log" 2>&1 || rm -rf "$path"
        [ -n "$br" ] && git -C "$PROJECT" branch -D "$br" >>"$RUN/loop.log" 2>&1 || true
    done
    git -C "$PROJECT" worktree prune >/dev/null 2>&1 || true
}

# The planner's backlog/questions edits are real state: commit them on the
# loop branch so the next fresh child (and the human) sees them, whatever the
# iteration's outcome. (One `git add` per file: a single add with a missing
# pathspec adds nothing.)
commit_backlog() {
    (cd "$WT" && for f in .loop/backlog.yaml .loop/questions.md; do [ -f "$f" ] && git add -f "$f"; done; git diff --cached --quiet || git commit -q -m "nightshift: backlog after iteration $ITER") >>"$RUN/loop.log" 2>&1 || true
}

build_map() {
    mkdir -p "$WT/.loop" "$ITER_DIR" 2>/dev/null || true
    (cd "$WT" && python3 "$KIT/map.py" --repo "$WT" --out "$WT/.loop/map.json" --arch "$LOOP/ARCHITECTURE.md" > "$ITER_DIR/map.out" 2>>"$RUN/loop.log") || note "map.py failed (non-fatal)"
    cp "$WT/.loop/map.json" "$LOOP/map.json" 2>/dev/null || true
}

kill_child() {
    if [ -n "${CHILD_PGID:-}" ]; then
        kill -TERM -- "-$CHILD_PGID" 2>/dev/null || true
        sleep 2
        kill -KILL -- "-$CHILD_PGID" 2>/dev/null || true
    fi
}

# --------------------------------------------------------------------------
# --kill
# --------------------------------------------------------------------------
if [ "$KILL" = "1" ]; then
    if [ -f "$RUN/loop.pid" ]; then
        pid=$(cat "$RUN/loop.pid")
        pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
        [ -n "$pgid" ] && kill -TERM -- "-$pgid" 2>/dev/null || true
        echo "sent TERM to loop process group $pgid"
        # The child runs in its own session (setsid); the driver's trap kills
        # it, but if the driver is already dead this is the only path.
        cpg=$(cat "$RUN/child.pgid" 2>/dev/null || true)
        if [ -n "$cpg" ] && kill -0 -- "-$cpg" 2>/dev/null; then
            kill -TERM -- "-$cpg" 2>/dev/null || true; sleep 2
            kill -0 -- "-$cpg" 2>/dev/null && kill -KILL -- "-$cpg" 2>/dev/null || true
            echo "sent TERM to child process group $cpg"
        fi
    else
        echo "no loop running"
    fi
    exit 0
fi

# --------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------
STEP="preflight"
command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "no config at $CONFIG — run /jg-loop init first" >&2; exit 1; }
[ -f "$MANIFEST" ] || { echo "no manifest at $MANIFEST — run /jg-loop init, or after a /jg-build re-run pipeline/mkconfig.py so the judges it pinned are signed" >&2; exit 1; }
[ -f "$GOAL" ] || { echo "no goal at $GOAL — run /jg-loop init first" >&2; exit 1; }
mkdir -p "$RUN" "$LOOP/iterations"
if [ -f "$RUN/loop.pid" ] && kill -0 "$(cat "$RUN/loop.pid")" 2>/dev/null; then
    echo "a loop is already running (pid $(cat "$RUN/loop.pid"))" >&2; exit 1
fi
rm -f "$RUN/STOP"

MAIN=$(cfg '.main_branch // "main"')
[ -n "$CAP" ] || CAP=$(cfg '.cap_usd // 25')
[ -n "$HOURS" ] || HOURS=$(cfg '.hours // 6')
if [ "$DRYRUN" = "1" ]; then CAP="${CAP:-3}"; HOURS=0.5; MAX_ITERS=1; fi
PER_ITER=$(cfg '.per_iter_max_usd // 6')
MIN_ITER=$(cfg '.min_iter_usd // 1.5')
MAX_FLAT=$(cfg '.max_flat // 3')
CHILD_TURNS=$(cfg '.child_max_turns // 80')
CHILD_TIMEOUT=$(cfg '.child_timeout_min // 45')
REGRESS_EPS=$(cfg '.regress_eps // 1.0')
FLAT_EPS=$(jq -r '[.scorers[] | select(.enabled != false) | .weight * (.eps // 0.5)] as $w | ([.scorers[] | select(.enabled != false) | .weight] | add) as $t | (($w | add) / $t)' "$CONFIG")
SETUP_CMD=$(cfg '.setup_cmd // ""')
TEST_CMD=$(cfg '.test_cmd // ""')
MAX_FANOUT=$(cfg '.max_fanout // 3')
OPUS=$(cfg '.opus_allowed // true')
UI=$(cfg '.ui.enabled // false')

echo $$ > "$RUN/loop.pid"
trap on_exit EXIT INT TERM
exec 2>>"$RUN/loop.log"
note "start cap=$CAP hours=$HOURS dryrun=$DRYRUN project=$PROJECT"

# --------------------------------------------------------------------------
# branch + worktree
# --------------------------------------------------------------------------
STEP="worktree"
DATE=$(date -u +%Y-%m-%d)
BRANCH="loop/$DATE"
WT="$LOOP/wt/loop"
cd "$PROJECT"
git rev-parse --verify -q "$MAIN" >/dev/null || { echo "branch $MAIN not found" >&2; exit 1; }
if ! git rev-parse --verify -q "$BRANCH" >/dev/null; then
    git branch "$BRANCH" "$MAIN"
    note "created $BRANCH from $MAIN"
fi
if [ ! -d "$WT/.git" ] && [ ! -f "$WT/.git" ]; then
    mkdir -p "$LOOP/wt"
    git worktree add "$WT" "$BRANCH" >>"$RUN/loop.log" 2>&1
    note "worktree $WT"
    # `git worktree add` leaves submodules empty; a suite that reads them
    # would shrink silently. Objects are already local after the main
    # checkout initialised them, so this needs no network.
    if [ -f "$WT/.gitmodules" ]; then
        git -C "$WT" submodule update --init --recursive >>"$RUN/loop.log" 2>&1 || note "submodule init failed (continuing)"
    fi
    if [ -n "$SETUP_CMD" ]; then
        STEP="setup"
        (cd "$WT" && bash -c "$SETUP_CMD") >>"$RUN/loop.log" 2>&1 || note "setup_cmd failed (continuing; executors run it again)"
    fi
fi
# A previous run that died mid-child (kill, crash) leaves executor worktrees
# behind; clear them before this run's ITER is stamped (orphans are tagged).
ITER=$(jq -r '.iter // 0' "$STATE" 2>/dev/null || echo 0)
prune_executor_worktrees

# A project whose main branch does not track .loop/backlog.yaml (init wrote it
# but nobody committed it) would give every child an empty backlog: seed the
# loop branch from the main checkout's copy.
if [ ! -f "$WT/.loop/backlog.yaml" ] && [ -f "$LOOP/backlog.yaml" ]; then
    mkdir -p "$WT/.loop"
    cp "$LOOP/backlog.yaml" "$WT/.loop/backlog.yaml"
    (cd "$WT" && git add -f .loop/backlog.yaml && git commit -q -m "nightshift: seed backlog") >>"$RUN/loop.log" 2>&1 || true
    note "seeded .loop/backlog.yaml on $BRANCH from the main checkout"
fi
# The baseline coverage report was produced in the main checkout by init; give
# the first child a copy so the planner does not re-run coverage to learn what
# scores.jsonl already knows.
if [ -f "$LOOP/run/coverage.json" ] && [ ! -f "$WT/.loop/run/coverage.json" ]; then
    mkdir -p "$WT/.loop/run" && cp "$LOOP/run/coverage.json" "$WT/.loop/run/coverage.json"
fi
# The loop worktree belongs to the loop. Anything uncommitted in it is debris
# from a killed child (executors commit in their own worktrees; the driver
# merges); everything of value is on the loop branch. Clean, note, continue.
if [ -n "$(git -C "$WT" status --porcelain -- . ':!.loop')" ]; then
    note "loop worktree dirty at start — resetting: $(git -C "$WT" status --porcelain | head -5 | tr '\n' ' ')"
    git -C "$WT" reset --hard -q >>"$RUN/loop.log" 2>&1 || true
    git -C "$WT" clean -fdq >>"$RUN/loop.log" 2>&1 || true
    git -C "$WT" checkout -q "$BRANCH" >>"$RUN/loop.log" 2>&1 || true
fi

# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------
STEP="state"
START=$(ts)
DEADLINE=$(python3 -c "import datetime,sys; print((datetime.datetime.utcnow()+datetime.timedelta(hours=float(sys.argv[1]))).strftime('%Y-%m-%dT%H:%M:%SZ'))" "$HOURS")
if [ -f "$STATE" ]; then
    state_set --arg run "$BRANCH" --arg s "$START" --arg d "$DEADLINE" --argjson cap "$CAP" --argjson mi "$MIN_ITER" \
        '.run=$run | .started=$s | .deadline=$d | .cap_usd=$cap | .min_iter_usd=$mi | .phase="IDLE" | .stop_reason=null | .stop_detail=null | .spent_usd=(.spent_usd // 0) | .flat=(.flat // 0) | .rung=(.rung // 1) | .failed_iters=0 | .agents=[] | .live_spend_usd=0 | .denies=(.denies // 0)'
else
    jq -n --arg run "$BRANCH" --arg s "$START" --arg d "$DEADLINE" --argjson cap "$CAP" --argjson mi "$MIN_ITER" \
        '{run:$run,iter:0,phase:"IDLE",started:$s,deadline:$d,cap_usd:$cap,min_iter_usd:$mi,spent_usd:0,live_spend_usd:0,score:null,best:null,delta:null,flat:0,rung:1,task:null,agents:[],stop_reason:null,denies:0,lockout:{},picks_history:[],failed_iters:0,dryrun_ok:false}' > "$STATE"
fi
# Spend is per run, not per project lifetime.
state_set '.spent_usd=0'
ITER=$(jq -r '.iter // 0' "$STATE")
# A project that came through the pipeline (mkconfig, not init) has no
# baseline row yet: score it once in the fresh worktree, so the first
# iteration's delta is against a measurement and not against 0.
if [ ! -s "$LOOP/scores.jsonl" ]; then
    STEP="baseline"
    note "no scores.jsonl — scoring the baseline (iter 0) first"
    state_set '.phase="SCORE"'
    if ! python3 "$KIT/score.py" --config "$CONFIG" --workdir "$WT" --iter 0 --commit "$(git -C "$WT" rev-parse HEAD)" \
        --cost 0 --duration 0 --task none --outcome baseline \
        --out "$LOOP/scores.jsonl" --manifest "$MANIFEST" > "$RUN/baseline-score.json" 2>>"$RUN/loop.log"; then
        stop score-infra-broken "$(jq -r '.error // "baseline scorer failed"' "$RUN/baseline-score.json" 2>/dev/null)"
        exit 1
    fi
    event BASELINE composite="$(jq -r '.composite' "$RUN/baseline-score.json")"
fi
LAST_SCORE=$(jq -s 'map(select(.composite != null)) | last | .composite // null' "$LOOP/scores.jsonl" 2>/dev/null) || LAST_SCORE=null
BEST=$(jq -s 'map(select(.composite != null) | .composite) | max // null' "$LOOP/scores.jsonl" 2>/dev/null) || BEST=null
[ -n "$LAST_SCORE" ] || LAST_SCORE=null; [ -n "$BEST" ] || BEST=null
state_set --argjson s "${LAST_SCORE:-null}" --argjson b "${BEST:-null}" '.score=$s | .best=$b'
event RUN_START cap="$CAP" hours="$HOURS" branch="$BRANCH"
beat

# Child settings: generated fresh so the child never depends on committed state.
STEP="child-settings"
CHILD_SETTINGS="$RUN/child-settings.json"
ALLOW_JSON=$(python3 "$KIT/allowlist.py" --config "$CONFIG" --kit "$KIT")
jq -n --arg kit "$KIT" --argjson allow "$ALLOW_JSON" '
{
  worktree: {baseRef: "head"},
  permissions: { allow: $allow },
  hooks: {
    SubagentStart: [{hooks:[{type:"command",command:("bash " + $kit + "/hooks/events.sh"),async:true,timeout:5}]}],
    SubagentStop:  [{hooks:[{type:"command",command:("bash " + $kit + "/hooks/events.sh"),async:true,timeout:5}]}],
    PreToolUse:    [{matcher:"Agent",hooks:[{type:"command",command:("bash " + $kit + "/hooks/budget-gate.sh"),timeout:5}]}]
  }
}' > "$CHILD_SETTINGS"
AGENTS_JSON=$(python3 "$KIT/agents_json.py" $([ "$UI" = "true" ] || echo --no-ui))

# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------
while :; do
    # ---- pre-iteration checks -------------------------------------------
    STEP="check"; beat
    ITER=$((ITER + 1))
    # Everything the child writes lives INSIDE the loop worktree: native
    # worktree isolation blocks writes into the main checkout, so a planner
    # told to write to <project>/.loop/ was denied and wrote into its own cwd
    # instead -- which the driver then could not find. The driver's own state
    # (state.json, events, scores) stays in <project>/.loop/.
    ITER_DIR="$WT/.loop/iterations/$ITER"
    BACKLOG="$WT/.loop/backlog.yaml"
    QUESTIONS="$WT/.loop/questions.md"
    mkdir -p "$ITER_DIR/tasks"
    SPENT=$(jq -r '.spent_usd' "$STATE")
    FLAT=$(jq -r '.flat' "$STATE")
    FAILED=$(jq -r '.failed_iters' "$STATE")
    [ -f "$RUN/STOP" ] && { stop manual "STOP file"; break; }
    [ "$(ts)" \< "$DEADLINE" ] || { stop hours "deadline $DEADLINE"; break; }
    if jq -en --argjson s "$SPENT" --argjson m "$MIN_ITER" --argjson c "$CAP" '$s + $m > $c' >/dev/null; then
        stop cap "spent $SPENT + min_iter $MIN_ITER > cap $CAP"; break; fi
    [ "$FLAT" -lt "$MAX_FLAT" ] || { stop flat "$FLAT flat iterations"; break; }
    [ "$FAILED" -lt 2 ] || { stop two-failed-iterations ""; break; }
    state_set --argjson i "$ITER" '.iter=$i | .phase="PICK" | .step="check" | .agents=[] | .live_spend_usd=0 | .stall=null'
    # Events written before this line belong to earlier iterations/runs; the
    # safety trip below looks only at lines after it.
    EV_MARK=$( [ -f "$EVENTS" ] && wc -l < "$EVENTS" || echo 0 )
    event ITERATION_START
    note "iteration $ITER begins (spent $SPENT/$CAP flat $FLAT)"

    # ---- PICK -----------------------------------------------------------
    STEP="PICK"; beat
    PRE_SHA=$(git -C "$WT" rev-parse HEAD)
    set +e
    python3 "$KIT/pick.py" --config "$CONFIG" --backlog "$BACKLOG" --scores "$LOOP/scores.jsonl" \
        --state "$STATE" --iter "$ITER" --out "$ITER_DIR/target.json" > "$ITER_DIR/pick.out" 2>>"$RUN/loop.log"
    PICK_RC=$?
    set -e
    if [ "$PICK_RC" = "3" ]; then stop needs-human "nothing selectable — see .loop/questions.md"; break; fi
    if [ "$PICK_RC" != "0" ]; then stop crashed-at-PICK "pick.py exit $PICK_RC"; break; fi
    # apply the state patch pick.py asked for (lockout / picks_history), if any
    PATCH=$(grep -m1 '"state_patch"' "$ITER_DIR/pick.out" | jq -c '.state_patch' 2>/dev/null || echo '{}')
    [ "$PATCH" != "{}" ] && [ -n "$PATCH" ] && state_set --argjson p "$PATCH" '. * $p'
    jq --arg pre "$PRE_SHA" '. + {pre_sha: $pre}' "$ITER_DIR/target.json" > "$ITER_DIR/.t" && mv "$ITER_DIR/.t" "$ITER_DIR/target.json"
    MODE=$(jq -r '.mode' "$ITER_DIR/target.json")
    TASK=$(jq -r '.task_ids[0] // "-"' "$ITER_DIR/target.json")
    RUNG=$(jq -r '.rung' "$ITER_DIR/target.json")
    state_set --arg t "$TASK" --argjson r "$RUNG" '.task=$t | .rung=$r | .phase="PLAN"'
    event PICK mode="$MODE" task="$TASK" rung="$RUNG"

    # The child's budget: per-iteration max, or whatever is left under the cap.
    BUDGET=$(jq -n --argjson p "$PER_ITER" --argjson c "$CAP" --argjson s "$SPENT" '[$p, ($c - $s)] | min')
    # The goal lives outside the worktree (tamper-evident); the child gets a
    # per-iteration copy it is allowed to read.
    cp "$GOAL" "$ITER_DIR/goal.md" 2>/dev/null || true
    # ---- PROMPT ---------------------------------------------------------
    STEP="prompt"; beat
    PREV_SUMMARY="$WT/.loop/iterations/$((ITER - 1))/summary.md"; [ -f "$PREV_SUMMARY" ] || PREV_SUMMARY="(none)"
    ARCH="$LOOP/ARCHITECTURE.md"; [ -f "$ARCH" ] || ARCH="(none yet)"
    SCORES_TAIL=$(tail -n 5 "$LOOP/scores.jsonl" 2>/dev/null | jq -c '{iter,composite,dims:(.dims|map_values(.value)),outcome}' | tr '\n' ' ')
    python3 - "$KIT/prompts/iterate.md" "$ITER_DIR/prompt.md" <<PY
import sys, pathlib
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
subs = {
 "ITER": "$ITER", "PROJECT_DIR": "$PROJECT", "LOOP_WT": "$WT", "LOOP_BRANCH": "$BRANCH",
 "KIT": "$KIT", "CONFIG": "$CONFIG", "GOAL": "$ITER_DIR/goal.md", "TARGET": "$ITER_DIR/target.json",
 "GOAL_TEXT": pathlib.Path("$GOAL").read_text(), "TARGET_JSON": pathlib.Path("$ITER_DIR/target.json").read_text(),
 "BACKLOG": "$BACKLOG", "SCORES_TAIL": """$SCORES_TAIL""", "PREV_SUMMARY": "$PREV_SUMMARY",
 "ARCH": "$ARCH", "MODE": "$MODE", "SETUP_CMD": """$SETUP_CMD""", "TEST_CMD": """$TEST_CMD""", "BUDGET": "$BUDGET",
 "MAX_FANOUT": "$MAX_FANOUT", "OPUS_ALLOWED": "$OPUS", "ITER_DIR": "$ITER_DIR", "QUESTIONS": "$QUESTIONS",
}
t = src.read_text()
for k, v in subs.items():
    t = t.replace("{{" + k + "}}", v)
dst.write_text(t)
PY

    # ---- MAP (cheap when unchanged: files_sha gate) ---------------------
    # check_plan.py reads <repo>/.loop/map.json with repo = the loop worktree,
    # so the map is written there; the main checkout gets a copy for the UI.
    STEP="map"; beat
    build_map

    # ---- CHILD ----------------------------------------------------------
    STEP="child"; beat
    ITER_START=$(date +%s)
    state_set --argjson b "$BUDGET" '.phase="PLAN" | .iter_budget_usd=$b'
    note "spawning child budget=$BUDGET mode=$MODE"
    set +e
    (
      cd "$WT"
      export NIGHTSHIFT_CONFIG="$CONFIG" NIGHTSHIFT_KIT="$KIT" NIGHTSHIFT_ITER_DIR="$ITER_DIR"
      rm -f "$RUN/child.pgid"
      # setsid makes the child its own session leader: its pid is its pgid.
      # Record it from inside so kill paths never have to guess with pgrep.
      setsid bash -c 'echo $$ > "$1"; shift; exec "$@"' _ "$RUN/child.pgid" \
        timeout "${CHILD_TIMEOUT}m" "$CLAUDE_BIN" -p "$(cat "$ITER_DIR/prompt.md")" \
        --model fable --max-budget-usd "$BUDGET" --max-turns "$CHILD_TURNS" \
        --permission-mode acceptEdits --permission-prompts none \
        --settings "$CHILD_SETTINGS" --agents "$AGENTS_JSON" \
        --output-format stream-json --include-hook-events --forward-subagent-text --verbose \
        2>>"$RUN/loop.log"
    ) | tee "$RUN/stream-$ITER.jsonl" \
      | python3 "$KIT/tail.py" --state "$STATE" --events "$EVENTS" --pricing "$KIT/pricing.json" --iter "$ITER" \
          --live-out "$WT/.loop/run/live.json" --budget "$BUDGET" \
          > "$ITER_DIR/tail.json" 2>>"$RUN/loop.log" &
    PIPE_PID=$!
    for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$RUN/child.pgid" ] && break; sleep 0.5; done
    CHILD_PGID=$(cat "$RUN/child.pgid" 2>/dev/null || true)
    # live cost watch: kill the process group if spent + live >= cap
    while kill -0 "$PIPE_PID" 2>/dev/null; do
        sleep 5; beat
        [ -f "$RUN/STOP" ] && { note "STOP file seen mid-iteration; letting child finish"; }
        if jq -e --argjson c "$CAP" '(.spent_usd + (.live_spend_usd // 0)) >= $c' "$STATE" >/dev/null 2>&1; then
            note "live spend reached cap — killing child"; kill_child; KILLED=1; break
        fi
        if [ "$(jq -r '.stall // empty' "$STATE")" = "permission" ]; then
            note "permission stall — killing child"; kill_child; STALLED=1; break
        fi
    done
    wait "$PIPE_PID" 2>/dev/null; CHILD_RC=$?
    set -e
    DURATION=$(( $(date +%s) - ITER_START ))

    # ---- COST -----------------------------------------------------------
    STEP="cost"; beat
    RESULT_COST=$(jq -r '.result_cost_usd // 0' "$ITER_DIR/tail.json" 2>/dev/null); [ -n "$RESULT_COST" ] || RESULT_COST=0
    LIVE=$(jq -r '.live_spend_usd // 0' "$ITER_DIR/tail.json" 2>/dev/null); [ -n "$LIVE" ] || LIVE=$(jq -r '.live_spend_usd // 0' "$STATE")
    AGREE=$(jq -r '.agreement // "null"' "$ITER_DIR/tail.json" 2>/dev/null); [ -n "$AGREE" ] || AGREE=null
    if [ "${KILLED:-0}" = "1" ] || [ "${STALLED:-0}" = "1" ] || [ "$CHILD_RC" != "0" ] || \
       jq -en --argjson r "$RESULT_COST" '$r <= 0' >/dev/null; then
        CHARGE=$(jq -n --argjson l "$LIVE" --argjson p "$PER_ITER" '[$l, $p] | max')
        note "pessimistic charge $CHARGE (rc=$CHILD_RC killed=${KILLED:-0} stalled=${STALLED:-0} result_cost=$RESULT_COST live=$LIVE)"
    else
        CHARGE="$RESULT_COST"
    fi
    state_set --argjson c "$CHARGE" '.spent_usd = (.spent_usd + $c) | .live_spend_usd=0 | .agents=[]'
    SPENT=$(jq -r '.spent_usd' "$STATE")
    event CHILD_DONE rc="$CHILD_RC" charge="$CHARGE" live="$LIVE" agreement="$AGREE" duration_s="$DURATION"
    prune_executor_worktrees
    if [ "$DRYRUN" = "1" ]; then
        DRY_OK=false
        # Signed: (live - result) / result. An under-estimate means the kill
        # switch would fire late, so it gets the tight bound; an over-estimate
        # only stops early. Measured on real runs: +8% with no executors,
        # +21% with a Sonnet executor and a Fable reviewer in the stream.
        SIGNED=$(jq -n --argjson l "$LIVE" --argjson r "$RESULT_COST" 'if $r > 0 then (($l - $r) / $r) else null end')
        if [ "$SIGNED" != "null" ] && jq -en --argjson s "$SIGNED" '$s >= -0.10 and $s <= 0.35' >/dev/null; then DRY_OK=true; fi
        AGREE="$SIGNED"
        state_set --argjson ok "$DRY_OK" '.dryrun_ok=$ok'
        echo "$(ts) DRYRUN cost agreement: result=$RESULT_COST live=$LIVE agreement=$AGREE ok=$DRY_OK" >> "$EVLOG"
    fi

    if [ "${STALLED:-0}" = "1" ]; then
        printf '\n## Iteration %s — permission stall\nThe child waited on a permission prompt. Add the needed allowlist entry.\n' "$ITER" >> "$QUESTIONS"
        stop permission-stall "iteration $ITER"; break
    fi
    if [ "${KILLED:-0}" = "1" ]; then stop cap "live spend reached cap mid-iteration $ITER"; break; fi
    if [ "$CHILD_RC" != "0" ] || [ ! -f "$ITER_DIR/summary.md" ]; then
        state_set '.failed_iters += 1'
        event ITERATION_FAILED rc="$CHILD_RC"
        note "iteration failed rc=$CHILD_RC summary=$([ -f "$ITER_DIR/summary.md" ] && echo yes || echo no)"
        git -C "$WT" reset --hard "$PRE_SHA" >>"$RUN/loop.log" 2>&1 || true
        if [ "$DRYRUN" = "1" ]; then stop dryrun-complete "iteration failed; agreement=$AGREE ok=${DRY_OK:-false}"; break; fi
        if [ "$MAX_ITERS" -gt 0 ] && [ "$ITER" -ge "$MAX_ITERS" ]; then stop iters "max iterations $MAX_ITERS"; break; fi
        continue
    fi
    state_set '.failed_iters = 0'

    # Safety trip: a push/main/out-of-worktree deny during THIS iteration.
    # Scope denies (read-only wandering) are logged but do not trip.
    if [ -f "$EVENTS" ] && tail -n +"$((EV_MARK + 1))" "$EVENTS" | grep -q '"kind":"safety"' ; then
        DEN=$(tail -n +"$((EV_MARK + 1))" "$EVENTS" | grep -c '"event":"deny"' || echo 0)
        state_set --argjson d "$DEN" '.denies=$d'
        stop safety-trip "an executor attempted a denied action — see .loop/events.log"; break
    fi

    # ---- SCORE ----------------------------------------------------------
    STEP="SCORE"; beat
    HEAD_SHA=$(git -C "$WT" rev-parse HEAD)
    if [ "$MODE" != "task" ] || [ "$HEAD_SHA" = "$PRE_SHA" ]; then
        if [ "$MODE" != "task" ] && [ "$HEAD_SHA" != "$PRE_SHA" ]; then
            note "child committed during $MODE — discarding (non-task iterations may only edit the backlog)"
            git -C "$WT" reset --hard "$PRE_SHA" >>"$RUN/loop.log" 2>&1 || true
        fi
        note "nothing merged (mode=$MODE)"
        if [ "$MODE" = "task" ]; then state_set '.flat += 1'; fi
        state_set '.phase="CLOSE"'
        commit_backlog
        event ITER_DONE outcome="nothing-merged" spent="$SPENT"
        if [ "$DRYRUN" = "1" ]; then stop dryrun-complete "nothing merged; agreement=$AGREE ok=${DRY_OK:-false}"; break; fi
        if [ "$MAX_ITERS" -gt 0 ] && [ "$ITER" -ge "$MAX_ITERS" ]; then stop iters "max iterations $MAX_ITERS"; break; fi
        continue
    fi
    state_set '.phase="SCORE"'
    set +e
    python3 "$KIT/score.py" --config "$CONFIG" --workdir "$WT" --iter "$ITER" --commit "$HEAD_SHA" \
        --cost "$CHARGE" --duration "$DURATION" --task "$TASK" --outcome pending \
        --out "$LOOP/scores.jsonl" --manifest "$MANIFEST" > "$ITER_DIR/score.json" 2>>"$RUN/loop.log"
    SCORE_RC=$?
    set -e
    if [ "$SCORE_RC" = "4" ]; then
        git -C "$WT" reset --hard "$PRE_SHA" >>"$RUN/loop.log" 2>&1 || true
        stop score-infra-broken "$(jq -r '.error // "scorer failed"' "$ITER_DIR/score.json" 2>/dev/null)"; break
    fi
    [ "$SCORE_RC" = "0" ] || { stop crashed-at-SCORE "score.py exit $SCORE_RC"; break; }
    COMPOSITE=$(jq -r '.composite' "$ITER_DIR/score.json")
    PREV=$(jq -r '.score // null' "$STATE")
    if [ "$PREV" = "null" ]; then PREV=$(jq -s 'map(select(.composite != null and .iter < '"$ITER"')) | last | .composite // 0' "$LOOP/scores.jsonl"); fi
    DELTA=$(jq -n --argjson c "$COMPOSITE" --argjson p "$PREV" '($c - $p) * 100 | round / 100')
    event SCORED composite="$COMPOSITE" delta="$DELTA"

    # ---- DECIDE (arithmetic only) ---------------------------------------
    STEP="DECIDE"; beat
    state_set '.phase="DECIDE"'
    fix_outcome() {  # rewrite the pending row's outcome
        python3 - "$LOOP/scores.jsonl" "$ITER" "$1" <<'PY'
import sys, json, pathlib
p = pathlib.Path(sys.argv[1]); it = int(sys.argv[2]); oc = sys.argv[3]
rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
for r in rows:
    if r.get("iter") == it and r.get("outcome") == "pending": r["outcome"] = oc
p.write_text("".join(json.dumps(r) + "\n" for r in rows))
PY
    }
    OUTCOME=""
    if jq -en --argjson d "$DELTA" --argjson e "$REGRESS_EPS" '$d < (0 - $e)' >/dev/null; then
        # Regression: is the ground truth stable? Re-score the pre-iteration tree.
        note "regression delta=$DELTA — re-scoring pre_sha for drift"
        git -C "$WT" checkout -q --detach "$PRE_SHA"
        set +e
        python3 "$KIT/score.py" --config "$CONFIG" --workdir "$WT" --iter "$ITER" --commit "$PRE_SHA" \
            --cost 0 --duration 0 --task drift-check --outcome drift-check --dry --manifest "$MANIFEST" > "$ITER_DIR/drift.json" 2>>"$RUN/loop.log"
        set -e
        git -C "$WT" checkout -q "$BRANCH"
        RESCORE=$(jq -r '.composite // "null"' "$ITER_DIR/drift.json")
        if [ "$RESCORE" != "null" ] && jq -en --argjson r "$RESCORE" --argjson p "$PREV" --argjson e "$FLAT_EPS" '(($r - $p) | fabs) > $e' >/dev/null; then
            git -C "$WT" reset --hard "$PRE_SHA" >>"$RUN/loop.log" 2>&1
            fix_outcome reset-drift
            stop regression-drift "loop branch re-scored $RESCORE vs recorded $PREV — flaky suite or environment"; break
        fi
        git -C "$WT" tag -f "nightshift/rejected/iter-$ITER" "$HEAD_SHA" >/dev/null 2>&1 || true
        git -C "$WT" reset --hard "$PRE_SHA" >>"$RUN/loop.log" 2>&1
        fix_outcome reset-regressed
        event REGRESSION delta="$DELTA"
        stop regression "delta $DELTA below -$REGRESS_EPS; tree reset to $PRE_SHA, rejected sha tagged"; break
    elif jq -en --argjson d "$DELTA" --argjson e "$FLAT_EPS" '($d | fabs) < $e' >/dev/null; then
        git -C "$WT" tag -f "nightshift/flat/iter-$ITER" "$HEAD_SHA" >/dev/null 2>&1 || true
        git -C "$WT" reset --hard "$PRE_SHA" >>"$RUN/loop.log" 2>&1
        fix_outcome reset-flat
        state_set '.flat += 1'
        OUTCOME="reset-flat"
        event RESET reason=flat delta="$DELTA"
    else
        fix_outcome kept
        state_set --argjson c "$COMPOSITE" --argjson d "$DELTA" '.score=$c | .delta=$d | .flat=0 | .best=([.best // 0, $c] | max)'
        OUTCOME="kept"
        event KEPT delta="$DELTA" composite="$COMPOSITE"
    fi
    # same-dimension checkpoint from pick.py
    if jq -e '.checkpoint == true' "$ITER_DIR/target.json" >/dev/null 2>&1; then
        printf '\n## Iteration %s — checkpoint\n%s\n' "$ITER" "$(jq -r '.checkpoint_note' "$ITER_DIR/target.json")" >> "$QUESTIONS"
        event ASK kind=checkpoint
    fi
    # picks history for lockout
    DIM=$(jq -r '.dimension // "none"' "$ITER_DIR/target.json")
    state_set --argjson i "$ITER" --arg d "$DIM" --argjson x "$DELTA" '.picks_history += [{iter:$i,dimension:$d,delta_after:$x}]'

    # ---- CLOSE ----------------------------------------------------------
    STEP="CLOSE"; beat
    state_set '.phase="CLOSE"'
    if [ "$OUTCOME" = "kept" ]; then
        build_map
        cp "$LOOP/ARCHITECTURE.md" "$WT/ARCHITECTURE.md" 2>/dev/null || true
        (cd "$WT" && git add -A ARCHITECTURE.md 2>/dev/null && git commit -q -m "nightshift: architecture map after iteration $ITER" 2>/dev/null) || true
    fi
    commit_backlog
    event ITER_DONE outcome="$OUTCOME" composite="$COMPOSITE" delta="$DELTA" spent="$SPENT"
    note "iteration $ITER done outcome=$OUTCOME composite=$COMPOSITE delta=$DELTA spent=$SPENT"

    if [ "$DRYRUN" = "1" ]; then stop dryrun-complete "agreement=$AGREE ok=${DRY_OK:-false}"; break; fi
    if [ "$MAX_ITERS" -gt 0 ] && [ "$ITER" -ge "$MAX_ITERS" ]; then stop iters "max iterations $MAX_ITERS"; break; fi
done

STEP="done"
note "run ended: $STOP_REASON"
exit 0
