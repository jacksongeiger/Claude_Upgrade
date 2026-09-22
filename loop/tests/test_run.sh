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
  HOME_FAKE="$d/home"; mkdir -p "$HOME_FAKE/.claude"
  echo '{}' > "$HOME_FAKE/.claude/.credentials.json"   # the Linux auth file: with it the child gets a kit-owned CLAUDE_CONFIG_DIR
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
git -C "$P" rev-parse --verify -q main >/dev/null && [ "$(git -C "$P" rev-list --count main)" = "2" ] && pass "main untouched" || fail "main moved"
grep -q "STOP reason=iters" .loop/events.log && pass "STOP line in events.log" || fail "no STOP line"
[ ! -f .loop/run/loop.pid ] && pass "loop.pid removed" || fail "loop.pid left"
[ "$(jq -r '.spent_usd' .loop/state.json)" = "0.42" ] && pass "charged result cost" || fail "spent=$(jq -r .spent_usd .loop/state.json)"

# 1c. the child ran with a kit-owned CLAUDE_CONFIG_DIR holding only its settings (no user hooks, no plugins),
# and with --setting-sources project, which is what keeps them out on macOS where the config dir must stay unset
CFGDIR=$(sed -n 's/^CLAUDE_CONFIG_DIR=//p' .loop/wt/loop/.loop/iterations/1/child-env.txt 2>/dev/null)
case "$CFGDIR" in "$HOME_FAKE"/.claude/nightshift/*/claude) pass "child CLAUDE_CONFIG_DIR is the slug's own dir" ;; *) fail "child CLAUDE_CONFIG_DIR=$CFGDIR" ;; esac
[ -f "$CFGDIR/settings.json" ] && jq -e '.hooks.PreToolUse' "$CFGDIR/settings.json" >/dev/null && pass "child config dir carries the kit's settings and hooks" || fail "no settings.json with hooks in $CFGDIR"
grep -q -- "--setting-sources project" .loop/wt/loop/.loop/iterations/1/child-env.txt && pass "child gets --setting-sources project" || fail "no --setting-sources project in child args"
# without the auth file (macOS: Keychain), child_config.sh returns empty and the driver must leave the variable unset
NOCRED=$(mktemp -d); HOME="$NOCRED" bash "$KIT/child_config.sh" "$NOCRED/ns" "$CFGDIR/settings.json" > "$NOCRED/out"
[ -z "$(cat "$NOCRED/out")" ] && pass "no credentials file → empty config dir (macOS path)" || fail "child_config.sh without credentials printed: $(cat "$NOCRED/out")"

# 6b. child allowlist is derived from the config's commands (test_cmd "bash ./run_tests.sh")
jq -e '.permissions.allow | index("Bash(bash:*)")' .loop/run/child-settings.json >/dev/null && pass "child allowlist carries the test runner" || fail "runner rule missing from child-settings.json"
jq -e '.permissions.allow | index("Bash(git push:*)")' .loop/run/child-settings.json >/dev/null && fail "git push allowlisted" || pass "git push not allowlisted"
[ -f .loop/wt/loop/.loop/map.json ] && pass "map.json written into the loop worktree for check_plan" || fail "no worktree map.json"
[ -z "$(git -C .loop/wt/loop status --short -- .loop/backlog.yaml)" ] && pass "backlog committed at CLOSE" || fail "backlog left uncommitted in the loop worktree"
[ "$(git -C .loop/wt/loop log --format=%s | grep -c "backlog after iteration")" -ge 1 ] && pass "backlog commit present" || fail "no backlog commit"
[ -f .loop/wt/loop/.loop/iterations/1/goal.md ] && pass "goal.md copied into the iteration dir" || fail "goal.md copy missing"

# 1b. nothing merged (planner wrote no commits) still commits the planner's backlog edits
P=$(mkproject); cd "$P"
run_driver nothing --iters 1 --cap 5 --hours 1
[ "$(git -C .loop/wt/loop log --format=%s | grep -c 'backlog after iteration')" -ge 1 ] && pass "backlog committed after a nothing-merged iteration" || fail "backlog not committed on nothing-merged"

# 2. flat → reset each time; two flats lock the only dimension out → ladder: harvest → hypothesize → needs-human
P=$(mkproject); cd "$P"
run_driver flat --cap 5 --hours 1
[ "$(stop_reason)" = "needs-human" ] && pass "flat → lockout → ladder exhausted → needs-human" || fail "stop_reason=$(stop_reason)"
[ "$(jq -r '.flat' .loop/state.json)" = "2" ] && pass "two flats counted before lockout" || fail "flat=$(jq -r .flat .loop/state.json)"
[ "$(git -C .loop/wt/loop rev-list --count HEAD)" = "2" ] && pass "flat commits reset" || fail "flat commits kept"
grep -q "reset-flat" .loop/scores.jsonl && pass "reset-flat recorded" || fail "no reset-flat row"
grep -q "PICK mode=harvest" .loop/events.log && grep -q "PICK mode=hypothesize" .loop/events.log && pass "ladder climbed harvest → hypothesize" || fail "ladder not climbed"

# 2b. a flat night that closed a REPORTED defect (production row) with a bigger suite is kept, not reset
P=$(mkproject); cd "$P"
sed -i 's/source: human/source: production/' .loop/backlog.yaml; git commit -qam "row from the inbox"
run_driver report --cap 5 --hours 1 --iters 1
[ "$(last_outcome)" = "kept" ] && pass "flat + reported row + suite grew → kept" || fail "outcome=$(last_outcome)"
grep -q "KEPT.*reason=closed-report rows=bl-001" .loop/events.log && pass "KEPT names the closed row" || fail "no closed-report KEPT line: $(grep KEPT .loop/events.log | tail -1)"
[ "$(cat .loop/wt/loop/tests.txt)" = "PASS PASS PASS FAIL FAIL FAIL" ] && pass "the fix stayed on the loop branch" || fail "tree reset: $(cat .loop/wt/loop/tests.txt)"
# the same flat night on a SPEC/human row is still reset
P=$(mkproject); cd "$P"
run_driver report --cap 5 --hours 1 --iters 1
[ "$(last_outcome)" = "reset-flat" ] && pass "flat on a human row still resets" || fail "outcome=$(last_outcome)"

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

# 6c. main does not track .loop/backlog.yaml (init wrote it, nobody committed) → seeded on the loop branch
P=$(mkproject); cd "$P"
git rm -q --cached .loop/backlog.yaml && git commit -q -m "untrack backlog"
run_driver improve --cap 5 --hours 1 --iters 1
[ "$(stop_reason)" = "iters" ] && pass "runs with an untracked backlog" || fail "stop_reason=$(stop_reason)"
[ "$(git -C .loop/wt/loop log --format=%s | grep -c 'seed backlog')" -ge 1 ] && pass "backlog seeded onto the loop branch" || fail "no seed commit"
[ "$(last_outcome)" = "kept" ] && pass "picked a row from the seeded backlog" || fail "outcome=$(last_outcome)"

# 7b. scope deny (read-only wandering) → logged, not a trip
P=$(mkproject); cd "$P"
run_driver scope --cap 5 --hours 1 --iters 1
[ "$(stop_reason)" = "iters" ] && pass "scope deny does not trip" || fail "stop_reason=$(stop_reason)"
grep -q '"kind":"scope"' .loop/events.jsonl && pass "scope deny logged" || fail "scope deny not logged"

# 7c. a safety deny from an EARLIER run does not trip a later one
P=$(mkproject); cd "$P"
run_driver deny --cap 5 --hours 1
[ "$(stop_reason)" = "safety-trip" ] || fail "setup: expected safety-trip, got $(stop_reason)"
run_driver improve --cap 5 --hours 1 --iters 1
[ "$(stop_reason)" = "iters" ] && pass "old safety deny does not trip a new run" || fail "stop_reason=$(stop_reason)"

# 7d. --kill takes the child's own process group down and charges the spend
P=$(mkproject); cd "$P"
# own session, or --kill's TERM to the driver's group would hit this script
HOME="$(dirname "$PWD")/home" NIGHTSHIFT_CLAUDE="$KIT/tests/fake_claude.sh" FAKE_CLAUDE_MODE=slow \
  setsid bash "$KIT/run.sh" --project "$PWD" --cap 5 --hours 1 >/dev/null 2>&1 &
DRV=$!
for _ in $(seq 1 40); do [ -s .loop/run/child.pgid ] && break; sleep 0.5; done
CPG=$(cat .loop/run/child.pgid 2>/dev/null || echo "")
[ -n "$CPG" ] && pass "child pgid recorded" || fail "no child.pgid written"
bash "$KIT/run.sh" --kill --project "$PWD" >/dev/null 2>&1
wait $DRV 2>/dev/null || true
sleep 1
if [ -n "$CPG" ] && kill -0 -- "-$CPG" 2>/dev/null; then fail "child process group survived --kill"; kill -KILL -- "-$CPG" 2>/dev/null; else pass "--kill took the child down"; fi
[ "$(stop_reason)" = "crashed-at-child" ] && pass "killed run records crashed-at-child" || fail "stop_reason=$(stop_reason)"
[ "$(jq -r '.spent_usd >= 2' .loop/state.json)" = "true" ] && pass "killed child charged pessimistically" || fail "spent=$(jq -r .spent_usd .loop/state.json)"

# 8. expensive child → live meter kills, stop cap
P=$(mkproject); cd "$P"
run_driver expensive --cap 5 --hours 1
[ "$(stop_reason)" = "cap" ] && pass "live meter kills runaway child" || fail "stop_reason=$(stop_reason)"
[ "$(jq -r '.spent_usd >= 2' .loop/state.json)" = "true" ] && pass "pessimistic charge after kill" || fail "spent=$(jq -r .spent_usd .loop/state.json)"

# 9. crash trap: a driver bug must not be silent
P=$(mkproject); cd "$P"
echo 'not json' > "$P/.loop/scores.jsonl"   # pick.py cannot read its scores: exit 1
run_driver improve --cap 5 --hours 1
case "$(stop_reason)" in crashed-at-*) pass "crash trap writes stop_reason ($(stop_reason))" ;; *) fail "stop_reason=$(stop_reason)" ;; esac
grep -q "STOP reason=crashed" .loop/events.log && pass "crash visible in events.log" || fail "crash silent"

# 9b. no scores.jsonl (a pipeline project: mkconfig, never init) → the driver
# scores the baseline itself and the first delta is against it, not against 0
P=$(mkproject); cd "$P"
rm -f "$P/.loop/scores.jsonl"
run_driver improve --cap 5 --hours 1 --iters 1
[ "$(head -n 1 .loop/scores.jsonl | jq -r '.outcome')" = "baseline" ] && pass "missing scores.jsonl → baseline row scored first" || fail "first row: $(head -n 1 .loop/scores.jsonl | head -c 120)"
grep -q "BASELINE" .loop/events.log && pass "baseline event logged" || fail "no BASELINE event"
[ "$(last_outcome)" = "kept" ] && pass "iteration after a self-scored baseline is kept" || fail "outcome=$(last_outcome) stop=$(stop_reason)"

# 10. dryrun → dryrun_ok when agreement within 10%
P=$(mkproject); cd "$P"
run_driver improve --dryrun
[ "$(stop_reason)" = "dryrun-complete" ] && pass "dryrun completes" || fail "stop_reason=$(stop_reason)"
grep -q "DRYRUN cost agreement" .loop/events.log && pass "agreement line written" || fail "no agreement line"

[ "$FAIL" = "0" ] && echo "test_run.sh: all passed" || { echo "test_run.sh: FAILURES"; exit 1; }
