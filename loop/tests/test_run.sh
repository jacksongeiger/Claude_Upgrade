#!/usr/bin/env bash
# Driver control-flow tests with a fake claude. No model calls.
# Usage: bash loop/tests/test_run.sh   (exit non-zero on failure)
set -uo pipefail
KIT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
FAIL=0
pass() { echo "  ok   $1"; }
fail() { echo "  FAIL $1"; FAIL=1; }

mkproject() {  # creates a project + nightshift config; prints project path
  local d; d=$(mktemp -d)
  HOME_FAKE="$d/home"; mkdir -p "$HOME_FAKE"
  local p="$d/proj"; mkdir -p "$p"; cd "$p"
  git init -q -b main . ; git config user.email t@t; git config user.name t
  echo "# proj" > README.md; echo "PASS PASS FAIL FAIL" > tests.txt
  cp "$KIT/tests/fake_test_runner.sh" ./run_tests.sh; chmod +x run_tests.sh
  printf 'CHANGELOG\n---\nv0\n---\n' > CHANGELOG.md; echo "# dead ends" > DEAD_ENDS.md
  git add -A; git commit -q -m init
  local slug; slug="$(basename "$p")-$(printf '%s' "$(cd "$p" && pwd -P)" | sha256sum | cut -c1-8)"
  local ns="$HOME_FAKE/.claude/nightshift/$slug"; mkdir -p "$ns"
  cat > "$ns/config.json" <<EOF
{"version":1,"project_dir":"$p","slug":"$slug","main_branch":"main","setup_cmd":"","test_cmd":"bash ./run_tests.sh",
 "cap_usd":5,"per_iter_max_usd":2,"min_iter_usd":0.5,"hours":1,"max_flat":3,"max_fanout":3,"max_files_per_iteration":25,
 "hypothesis_max_turns":30,"child_max_turns":80,"child_timeout_min":2,"opus_allowed":true,"regress_eps":5.0,
 "ui":{"enabled":false},
 "scorers":[{"name":"tests","weight":1.0,"runs":1,"eps":0.5,"target":100,"cmd":"bash ./run_tests.sh"}]}
EOF
  echo "1. make tests pass" > "$ns/goal.md"
  python3 "$KIT/score.py" --manifest-write "$ns/manifest.sha256" --config "$ns/config.json" >/dev/null
  mkdir -p .loop
  cat > .loop/backlog.yaml <<EOF
rows:
  - id: bl-001
    title: "fix failing tests"
    dimension: tests
    est: S
    source: human
    status: open
    rung: 1
    attempts: 0
    iter_added: 0
EOF
  python3 "$KIT/score.py" --config "$ns/config.json" --workdir "$p" --iter 0 --commit "$(git rev-parse HEAD)" \
     --cost 0 --duration 0 --task none --outcome baseline --out .loop/scores.jsonl --manifest "$ns/manifest.sha256" >/dev/null
  printf '.loop/run/\n.loop/wt/\n.loop/heartbeat\n.loop/state.json\n.loop/events.*\n.loop/iterations/\n' > .gitignore
  git add -A; git commit -q -m "nightshift init"
  echo "$p"
}

run_driver() {  # run_driver MODE [extra args]
  HOME_FAKE="$(dirname "$PWD")/home"
  local mode="$1"; shift
  HOME="$HOME_FAKE" NIGHTSHIFT_CLAUDE="$KIT/tests/fake_claude.sh" FAKE_CLAUDE_MODE="$mode" \
    bash "$KIT/run.sh" --project "$PWD" "$@" >/dev/null 2>&1
}
stop_reason() { jq -r '.stop_reason' .loop/state.json; }
last_outcome() { tail -n 1 .loop/scores.jsonl | jq -r '.outcome'; }

echo "test_run.sh"

# 1. improve → kept, score rises, one iteration then --iters stop
P=$(mkproject); cd "$P"
run_driver improve --iters 1 --cap 5 --hours 1
[ "$(stop_reason)" = "iters" ] && pass "stops on --iters" || fail "stop_reason=$(stop_reason)"
[ "$(last_outcome)" = "kept" ] && pass "improvement kept" || fail "outcome=$(last_outcome)"
[ "$(jq -r '.score == 100' .loop/state.json)" = "true" ] && pass "score updated to 100" || fail "score=$(jq -r .score .loop/state.json)"
git -C "$P" rev-parse --verify -q main >/dev/null && [ "$(git -C "$P" log --oneline main | wc -l)" = "2" ] && pass "main untouched" || fail "main moved"
grep -q "STOP reason=iters" .loop/events.log && pass "STOP line in events.log" || fail "no STOP line"
[ ! -f .loop/run/loop.pid ] && pass "loop.pid removed" || fail "loop.pid left"
[ "$(jq -r '.spent_usd' .loop/state.json)" = "0.42" ] && pass "charged result cost" || fail "spent=$(jq -r .spent_usd .loop/state.json)"

# 2. flat → reset each time; two flats lock the only dimension out → ladder: harvest → hypothesize → needs-human
P=$(mkproject); cd "$P"
run_driver flat --cap 5 --hours 1
[ "$(stop_reason)" = "needs-human" ] && pass "flat → lockout → ladder exhausted → needs-human" || fail "stop_reason=$(stop_reason)"
[ "$(jq -r '.flat' .loop/state.json)" = "2" ] && pass "two flats counted before lockout" || fail "flat=$(jq -r .flat .loop/state.json)"
[ "$(git -C .loop/wt/loop log --oneline | wc -l)" = "2" ] && pass "flat commits reset" || fail "flat commits kept"
grep -q "reset-flat" .loop/scores.jsonl && pass "reset-flat recorded" || fail "no reset-flat row"
grep -q "PICK mode=harvest" .loop/events.log && grep -q "PICK mode=hypothesize" .loop/events.log && pass "ladder climbed harvest → hypothesize" || fail "ladder not climbed"

# 3. regress → tree reset, stop regression
P=$(mkproject); cd "$P"
run_driver regress --cap 5 --hours 1
[ "$(stop_reason)" = "regression" ] && pass "regression stops" || fail "stop_reason=$(stop_reason)"
git -C .loop/wt/loop tag | grep -q "nightshift/rejected/iter-1" && pass "rejected sha tagged" || fail "no tag"
[ "$(cat .loop/wt/loop/tests.txt)" = "PASS PASS FAIL FAIL" ] && pass "tree reset to pre_sha" || fail "tree not reset"

# 4. crash → two failed iterations → stop
P=$(mkproject); cd "$P"
run_driver crash --cap 5 --hours 1
[ "$(stop_reason)" = "two-failed-iterations" ] && pass "two crashes stop" || fail "stop_reason=$(stop_reason)"
[ "$(jq -r '.spent_usd' .loop/state.json)" = "4" ] && pass "pessimistic charge on crash (2×per_iter)" || fail "spent=$(jq -r .spent_usd .loop/state.json)"

# 5. cap → stop before an iteration that cannot be afforded
P=$(mkproject); cd "$P"
FAKE_CLAUDE_COST=1.9 run_driver improve --cap 2.2 --hours 1
[ "$(stop_reason)" = "cap" ] && pass "cap stops" || fail "stop_reason=$(stop_reason)"

# 6. STOP file written mid-run → manual stop between iterations (a stale STOP file is cleared at start)
P=$(mkproject); cd "$P"
run_driver stopfile --cap 5 --hours 1
[ "$(stop_reason)" = "manual" ] && pass "STOP file honoured between iterations" || fail "stop_reason=$(stop_reason)"
[ "$(grep -c ITERATION_START .loop/events.log)" = "1" ] && pass "stopped before iteration 2 ran" || fail "iterations=$(grep -c ITERATION_START .loop/events.log)"

# 7. safety deny → safety-trip
P=$(mkproject); cd "$P"
run_driver deny --cap 5 --hours 1
[ "$(stop_reason)" = "safety-trip" ] && pass "safety deny trips" || fail "stop_reason=$(stop_reason)"

# 8. expensive child → live meter kills, stop cap
P=$(mkproject); cd "$P"
run_driver expensive --cap 5 --hours 1
[ "$(stop_reason)" = "cap" ] && pass "live meter kills runaway child" || fail "stop_reason=$(stop_reason)"
[ "$(jq -r '.spent_usd >= 2' .loop/state.json)" = "true" ] && pass "pessimistic charge after kill" || fail "spent=$(jq -r .spent_usd .loop/state.json)"

# 9. crash trap: a driver bug must not be silent
P=$(mkproject); cd "$P"
chmod -x "$P/run_tests.sh" 2>/dev/null; rm -f "$P/.loop/backlog.yaml"   # pick.py will exit 1
run_driver improve --cap 5 --hours 1
case "$(stop_reason)" in crashed-at-*) pass "crash trap writes stop_reason ($(stop_reason))" ;; *) fail "stop_reason=$(stop_reason)" ;; esac
grep -q "STOP reason=crashed" .loop/events.log && pass "crash visible in events.log" || fail "crash silent"

# 10. dryrun → dryrun_ok when agreement within 10%
P=$(mkproject); cd "$P"
run_driver improve --dryrun
[ "$(stop_reason)" = "dryrun-complete" ] && pass "dryrun completes" || fail "stop_reason=$(stop_reason)"
grep -q "DRYRUN cost agreement" .loop/events.log && pass "agreement line written" || fail "no agreement line"

[ "$FAIL" = "0" ] && echo "test_run.sh: all passed" || { echo "test_run.sh: FAILURES"; exit 1; }
