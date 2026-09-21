#!/usr/bin/env bash
# A stand-in for `claude -p` used by test_run.sh. Emits the stream-json shapes
# run.sh/tail.py consume, and "does work" the way a planner child would:
# commits into the loop worktree (cwd) and writes summary.md.
#
# Behaviour is driven by FAKE_CLAUDE_MODE:
#   improve   commit a change that makes the fake test runner report more passes
#   flat      commit a change that leaves the score identical
#   regress   commit a change that makes tests fail
#   nothing   write summary.md only, no commit
#   crash     exit 1 without a result event
#   slow      hang for 60s (killed by --kill in the test)
#   expensive emit usage worth far more than the cap, no result
#   deny      simulate an executor safety deny (writes to events.jsonl) then improve
#   scope     simulate a read-only scope deny (must NOT trip) then improve
# and FAKE_CLAUDE_COST (result total_cost_usd, default 0.42).

set -euo pipefail
MODE="${FAKE_CLAUDE_MODE:-improve}"
COST="${FAKE_CLAUDE_COST:-0.42}"
ITER_DIR="${NIGHTSHIFT_ITER_DIR:?}"
ROOT=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")

emit() { printf '%s\n' "$1"; }

emit '{"type":"system","subtype":"init","model":"claude-fable-5-1","session_id":"fake"}'
emit '{"type":"assistant","message":{"model":"claude-fable-5-1","content":[{"type":"text","text":"Planning."}],"usage":{"input_tokens":1000,"output_tokens":200,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}'

case "$MODE" in
  crash)
    exit 1 ;;
  slow)
    # a child that keeps running until killed (for the --kill test)
    trap 'exit 143' TERM
    sleep 60 & wait $!
    exit 0 ;;
  expensive)
    # 5,000,000 output tokens on fable ≈ $125 — must trip the live meter.
    for i in 1 2 3 4 5; do
      emit '{"type":"assistant","message":{"model":"claude-fable-5-1","content":[{"type":"text","text":"burning"}],"usage":{"input_tokens":0,"output_tokens":1000000,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}'
      sleep 1
    done
    sleep 30
    exit 0 ;;
esac

if [ "$MODE" = "stopfile" ]; then
  mkdir -p "$ROOT/.loop/run"; touch "$ROOT/.loop/run/STOP"; MODE=improve
fi

if [ "$MODE" = "scope" ]; then
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"ts":"%s","event":"deny","kind":"scope","tool":"Bash","reason":"git worktree list"}\n' "$ts" >> "$ROOT/.loop/events.jsonl"
  printf '%s DENY scope git worktree list\n' "$ts" >> "$ROOT/.loop/events.log"
  MODE=improve
fi

if [ "$MODE" = "deny" ]; then
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"ts":"%s","event":"deny","kind":"safety","tool":"Bash","reason":"git push"}\n' "$ts" >> "$ROOT/.loop/events.jsonl"
  printf '%s DENY safety git push\n' "$ts" >> "$ROOT/.loop/events.log"
  MODE=improve
fi

# Pretend an executor ran.
emit '{"type":"assistant","message":{"model":"claude-fable-5-1","content":[{"type":"tool_use","name":"Agent","input":{"subagent_type":"exec-sonnet","prompt":"..."}}],"usage":{"input_tokens":500,"output_tokens":50,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}'
emit '{"type":"assistant","message":{"model":"claude-sonnet-5","content":[{"type":"text","text":"done"}],"usage":{"input_tokens":3000,"output_tokens":800,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}'

case "$MODE" in
  improve)  echo "PASS PASS PASS PASS" > tests.txt ;;
  flat)     echo "# comment" >> README.md ;;
  report)   echo "PASS PASS PASS FAIL FAIL FAIL" > tests.txt ;;   # same pass rate, one more test each way: flat score, larger suite
  regress)  echo "PASS FAIL FAIL FAIL" > tests.txt ;;
  nothing)  ;;
esac
if [ "$MODE" != "nothing" ]; then
  git add -A >/dev/null 2>&1
  git commit -q -m "nightshift: fake $MODE" >/dev/null 2>&1 || true
fi
mkdir -p "$ITER_DIR"
printf 'CLAUDE_CONFIG_DIR=%s\nARGS=%s\n' "${CLAUDE_CONFIG_DIR:-}" "$*" > "$ITER_DIR/child-env.txt"
printf 'merged: t-fake (bl-001) — %s\nfailed: —\nneeds-human: —\nquestions: 0\n' "$MODE" > "$ITER_DIR/summary.md"
# the planner edits the backlog at CLOSE; the driver must commit it
mkdir -p .loop; printf '# fake planner touched this (%s)\n' "$MODE" >> .loop/backlog.yaml 2>/dev/null || true

emit "{\"type\":\"result\",\"subtype\":\"success\",\"total_cost_usd\":$COST,\"num_turns\":4,\"duration_ms\":1200,\"result\":\"ok\"}"
exit 0
