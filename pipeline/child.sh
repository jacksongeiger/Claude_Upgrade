#!/usr/bin/env bash
# child.sh — run one pipeline stage as a metered `claude -p` child, the same
# way Nightshift and build.sh run theirs: the derived allowlist, the project's
# agents (executors, reviewers, persona, judge), the budget gate, the live
# cost meter, a process group that dies with the driver.
#
#   child.sh --project DIR --prompt FILE --stage NAME [--budget USD] [--model fable|sonnet]
#            [--cwd DIR] [--turns N] [--timeout-min N] [--sub KEY=VALUE ...] [--sub-file KEY=PATH ...]
#
# `--sub` substitutes {{KEY}} in the prompt template; `--sub-file` substitutes
# the file's contents. Writes <project>/.pipeline/run/<stage>-<ts>/{prompt.md,
# stream.jsonl, tail.json, result.txt} and appends the cost to the ledger.
# Exit: the child's exit code (0 ok); 1 usage; 4 when the child could not start.

set -uo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LOOP_KIT="$(cd "$KIT/../loop" && pwd -P)"
CLAUDE_BIN="${NIGHTSHIFT_CLAUDE:-claude}"

PROJECT="$PWD"; PROMPT=""; STAGE="stage"; BUDGET="6"; MODEL="fable"; CWD=""; TURNS=80; TMO=45
SUBS=(); SUBFILES=()
while [ $# -gt 0 ]; do
    case "$1" in
        --project) PROJECT="$2"; shift 2 ;;
        --prompt) PROMPT="$2"; shift 2 ;;
        --stage) STAGE="$2"; shift 2 ;;
        --budget) BUDGET="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --cwd) CWD="$2"; shift 2 ;;
        --turns) TURNS="$2"; shift 2 ;;
        --timeout-min) TMO="$2"; shift 2 ;;
        --sub) SUBS+=("$2"); shift 2 ;;
        --sub-file) SUBFILES+=("$2"); shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done
[ -f "$PROMPT" ] || { echo "child.sh: --prompt FILE required" >&2; exit 1; }
PROJECT=$(cd "$PROJECT" && pwd -P); [ -n "$CWD" ] || CWD="$PROJECT"
PIPE="$PROJECT/.pipeline"; RUN="$PIPE/run/$STAGE-$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$RUN" "$PROJECT/.loop/run"
SLUG="$(basename "$PROJECT")-$(printf '%s' "$PROJECT" | sha256sum | cut -c1-8)"
CONFIG="$HOME/.claude/nightshift/$SLUG/config.json"
[ -f "$CONFIG" ] || python3 "$KIT/mkconfig.py" --project "$PROJECT" --spec "$PROJECT/spec.json" --out "$CONFIG" >/dev/null 2>&1 || true

# prompt rendering
python3 - "$PROMPT" "$RUN/prompt.md" "${SUBS[@]}" --- "${SUBFILES[@]}" <<'PY'
import sys, pathlib
args = sys.argv[3:]; sep = args.index("---") if "---" in args else len(args)
subs, files = args[:sep], args[sep+1:]
t = pathlib.Path(sys.argv[1]).read_text()
for kv in subs:
    k, _, v = kv.partition("="); t = t.replace("{{"+k+"}}", v)
for kv in files:
    k, _, v = kv.partition("="); p = pathlib.Path(v)
    t = t.replace("{{"+k+"}}", p.read_text() if p.exists() else "")
pathlib.Path(sys.argv[2]).write_text(t)
PY

# settings: allowlist + kit scripts + hooks, agents from the kit
# no config yet (the interview runs first): the fixed read-only set, kit scripts and runners still apply
[ -f "$CONFIG" ] && ALLOW_CFG="$CONFIG" || { echo '{}' > "$RUN/empty-config.json"; ALLOW_CFG="$RUN/empty-config.json"; }
ALLOW_JSON=$(python3 "$LOOP_KIT/allowlist.py" --config "$ALLOW_CFG" --kit "$LOOP_KIT")
ALLOW_JSON=$(jq -c --arg k "$KIT" '. + ["Bash(python3 " + $k + "/*)", "Bash(node " + $k + "/*)", "Bash(bash " + $k + "/*)", "Bash(git tag:*)", "Bash(git commit:*)", "Bash(git add:*)"]' <<<"$ALLOW_JSON")
jq -n --arg lk "$LOOP_KIT" --argjson allow "$ALLOW_JSON" '{worktree:{baseRef:"head"},permissions:{allow:$allow},
  hooks:{SubagentStart:[{hooks:[{type:"command",command:("bash "+$lk+"/hooks/events.sh"),async:true,timeout:5}]}],
         SubagentStop:[{hooks:[{type:"command",command:("bash "+$lk+"/hooks/events.sh"),async:true,timeout:5}]}],
         PreToolUse:[{matcher:"Agent",hooks:[{type:"command",command:("bash "+$lk+"/hooks/budget-gate.sh"),timeout:5}]}]}}' > "$RUN/child-settings.json"
AGENTS_JSON=$(python3 "$LOOP_KIT/agents_json.py" --no-ui)

CHILD_PGID=""
kill_child() { [ -n "$CHILD_PGID" ] || CHILD_PGID=$(cat "$RUN/child.pgid" 2>/dev/null || true); if [ -n "$CHILD_PGID" ]; then kill -TERM -- "-$CHILD_PGID" 2>/dev/null || true; sleep 2; kill -KILL -- "-$CHILD_PGID" 2>/dev/null || true; fi; }
trap 'kill_child; exit 130' INT TERM

( cd "$CWD"
  export NIGHTSHIFT_CONFIG="$CONFIG" NIGHTSHIFT_KIT="$LOOP_KIT" PIPELINE_KIT="$KIT"
  setsid bash -c 'echo $$ > "$1"; shift; exec "$@"' _ "$RUN/child.pgid" \
    timeout "${TMO}m" "$CLAUDE_BIN" -p "$(cat "$RUN/prompt.md")" \
    --model "$MODEL" --max-budget-usd "$BUDGET" --max-turns "$TURNS" \
    --permission-mode acceptEdits --permission-prompts none \
    --settings "$RUN/child-settings.json" --agents "$AGENTS_JSON" \
    --output-format stream-json --include-hook-events --forward-subagent-text --verbose 2>>"$RUN/child.log"
) | tee "$RUN/stream.jsonl" \
  | python3 "$LOOP_KIT/tail.py" --state "$RUN/state.json" --events "$RUN/events.jsonl" --pricing "$LOOP_KIT/pricing.json" --iter 1 \
      --live-out "$CWD/.loop/run/live.json" --budget "$BUDGET" > "$RUN/tail.json" 2>>"$RUN/child.log"
RC=${PIPESTATUS[0]}
COST=$(jq -r '.result_cost_usd // 0' "$RUN/tail.json" 2>/dev/null); [ -n "$COST" ] || COST=0
jq -cn --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg s "$STAGE" --arg id "$(basename "$RUN")" --argjson c "$COST" '{ts:$ts,stage:$s,id:$id,cost_usd:$c}' >> "$PIPE/ledger.jsonl"
# the child's last assistant text, for callers that want it
python3 - "$RUN/stream.jsonl" "$RUN/result.txt" <<'PY'
import json, sys, pathlib
last = ""
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    try: o = json.loads(line)
    except Exception: continue
    if o.get("type") == "result" and o.get("result"): last = o["result"]
    elif o.get("type") == "assistant":
        for c in (o.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"): last = c["text"]
pathlib.Path(sys.argv[2]).write_text(last)
PY
printf '%s stage=%s cost=$%s rc=%s run=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$STAGE" "$COST" "$RC" "$RUN" >> "$PIPE/events.log"
echo "$RUN"
exit "$RC"
