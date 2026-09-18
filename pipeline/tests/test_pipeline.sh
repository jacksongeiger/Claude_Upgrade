#!/usr/bin/env bash
# Integration test for the pipeline drivers: a synthetic project goes through
# spec_check --derive → build.sh (fake claude) → accept → ship_check →
# feedback.py. Exercises the scripts together, the way the commands run them.
set -uo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
LOOP="$(cd "$KIT/../loop" && pwd -P)"
PASSED=0; FAILED=0
pass() { echo "  ok   $*"; PASSED=$((PASSED+1)); }
fail() { echo "  FAIL $*"; FAILED=$((FAILED+1)); }

D=$(mktemp -d); export HOME="$D/home"; mkdir -p "$HOME"; export CLASSIFY_OFF=1
P="$D/proj"; mkdir -p "$P"; cd "$P"
git init -q -b main . ; git config user.email t@t; git config user.name t
echo "# proj" > README.md; echo "PASS PASS FAIL FAIL" > tests.txt
cp "$LOOP/tests/fake_test_runner.sh" ./run_tests.sh; chmod +x run_tests.sh
printf 'CHANGELOG\n---\n### v0.1.0 — first\n' > CHANGELOG.md; echo "# dead ends" > DEAD_ENDS.md; echo "# proj" > CLAUDE.md
printf 'API_KEY=\n' > .env.example; printf 'const k = process.env.API_KEY;\n' > app.js
cat > spec.json <<'EOF'
{"version":1,"name":"Proj","one_liner":"a synthetic project","stack":{"language":"bash","ui":false,"llm":false},
 "needs":["unit-tests"],
 "milestones":[{"id":"m1","title":"first","depends_on":[]},{"id":"m2","title":"second","depends_on":["m1"]}],
 "features":[
  {"id":"f-001","milestone":"m1","title":"tests pass","acceptance":[{"type":"test","cmd":"! grep -q FAIL tests.txt","must":"pass"}]},
  {"id":"f-002","milestone":"m2","title":"still pass","acceptance":[{"type":"test","cmd":"! grep -q FAIL tests.txt","must":"pass"},{"type":"manual","what":"feels right"}]}],
 "success":["Every acceptance check passes"],"budget":{"build_usd":5,"nightshift_cap_usd":5}}
EOF
git add -A; git commit -qm init

echo "test_pipeline.sh"
# 1. spec_check + derive
python3 "$KIT/spec_check.py" spec.json >/dev/null && pass "spec validates" || fail "spec invalid"
python3 "$KIT/spec_check.py" spec.json --derive >/dev/null
[ -f GOAL.md ] && grep -q "^1\. Every acceptance" GOAL.md && pass "GOAL.md derived" || fail "GOAL.md missing"
grep -q "spec-f-001" .loop/backlog.yaml && pass "backlog rows derived" || fail "backlog rows missing"
jq -e '.["f-002"].milestone == "m2"' .pipeline/acceptance-index.json >/dev/null && pass "acceptance index carries milestones" || fail "index shape"
grep -q ".pipeline/run/" .gitignore && pass "gitignore additions" || fail "gitignore not updated"
git add -A; git commit -qm "spec derived"

# 2. milestone ordering
[ "$(python3 "$KIT/milestone.py" --spec spec.json --state .pipeline/build/state.json next)" = "m1" ] && pass "next milestone is m1" || fail "next != m1"

# 2b. mkconfig pins an in-repo judge (a perf bench script) so the loop cannot edit it
mkdir -p bench; printf 'echo "{\"ms\": 1}"\n' > bench/run.sh
python3 - <<'PY'
import json; s=json.load(open("spec.json")); s["needs"]=["unit-tests","perf"]
s["features"][0]["acceptance"].append({"type":"perf","cmd":"bash bench/run.sh","metric":"ms","max":10})
json.dump(s, open("spec.json","w"))
PY
python3 "$KIT/spec_check.py" spec.json --derive >/dev/null
python3 "$KIT/mkconfig.py" --project "$P" --spec spec.json --out "$D/mk/config.json" >/dev/null 2>&1
jq -e '.scorers[] | select(.name=="perf") | .pins == ["bench/run.sh"]' "$D/mk/config.json" >/dev/null && pass "mkconfig pins the bench script" || fail "mkconfig pins: $(jq -c '.scorers[] | select(.name=="perf")' "$D/mk/config.json")"
grep -q "repo:bench/run.sh" "$D/mk/manifest.sha256" && pass "manifest carries the pinned bench" || fail "manifest lacks repo:bench/run.sh"
# 2c. an evals check in the spec becomes a cmd scorer reading the named metric (Inbox Triage: {"accuracy_tags": 0.9})
mkdir -p evals; cat > evals/run.sh <<'SH'
echo '{"accuracy_tags": 0.9, "n": 4}'
SH
python3 - <<'PY'
import json; s=json.load(open("spec.json")); s["needs"]=["unit-tests","perf","evals"]
s["features"][0]["acceptance"].append({"type":"evals","cmd":"bash evals/run.sh","metric":"accuracy_tags","min":0.8})
json.dump(s, open("spec.json","w"))
PY
python3 "$KIT/spec_check.py" spec.json --derive >/dev/null
python3 "$KIT/mkconfig.py" --project "$P" --spec spec.json --out "$D/mk2/config.json" >/dev/null 2>&1
jq -e '.scorers[] | select(.name=="evals") | .script=="cmd" and .metric=="accuracy_tags" and .scale==100 and any(.pins[]; .=="evals/*")' "$D/mk2/config.json" >/dev/null && pass "mkconfig: evals check -> cmd scorer with metric" || fail "mkconfig evals: $(jq -c '[.scorers[] | {name, script, metric, scale, pins}]' "$D/mk2/config.json" 2>&1) :: $(python3 "$KIT/mkconfig.py" --project "$P" --spec spec.json --out "$D/mk2/config.json" 2>&1 | tail -2)"
EV_OUT=$(python3 "$LOOP/scorers/cmd.py" --config "$(jq -c '.scorers[] | select(.name=="evals")' "$D/mk2/config.json")" --workdir "$P" | tail -1)
echo "$EV_OUT" | jq -e '.ok==true and .value==90' >/dev/null && pass "evals cmd scorer scores 90 from the fraction" || fail "evals cmd scorer: $EV_OUT"
rm -rf evals
git checkout -q spec.json .loop/backlog.yaml 2>/dev/null; rm -rf bench .pipeline/acceptance-index.json; python3 "$KIT/spec_check.py" spec.json --derive >/dev/null; git add -A; git commit -qm "restore" >/dev/null

# 3. build.sh with the fake claude: config from mkconfig, m1 then m2
NIGHTSHIFT_CLAUDE="$LOOP/tests/fake_claude.sh" FAKE_CLAUDE_MODE=improve bash "$KIT/build.sh" --project "$P" --milestone all --cap 5 > "$D/build.out" 2>&1; RC=$?
[ "$RC" = "0" ] && pass "build.sh exits 0 on two accepted milestones" || { fail "build.sh rc=$RC"; tail -5 "$D/build.out"; tail -5 .pipeline/run/build.log; }
[ "$(jq -r '.milestones.m1.status' .pipeline/build/state.json)" = "done" ] && pass "m1 done" || fail "m1 state: $(jq -c .milestones .pipeline/build/state.json)"
[ "$(jq -r '.milestones.m2.status' .pipeline/build/state.json)" = "done" ] && pass "m2 done" || fail "m2 not done"
git tag | grep -q "^build/m1$" && pass "milestone tag" || fail "no build/m1 tag"
[ "$(git log --oneline main | wc -l)" = "2" ] && pass "main untouched" || fail "main moved"
[ "$(jq -s 'map(.cost_usd) | add' .pipeline/ledger.jsonl)" != "null" ] && pass "ledger has costs" || fail "ledger empty"
grep -q "MILESTONE_DONE id=m1" .pipeline/events.log && pass "events logged" || fail "no MILESTONE_DONE"
[ -f .pipeline/wt/build/.pipeline/build/m1/acceptance.final.json ] && pass "acceptance.final.json written" || fail "no acceptance record"
[ -z "$(git -C .pipeline/wt/build status --short | grep -v '^??')" ] && pass "build worktree clean" || fail "build worktree dirty: $(git -C .pipeline/wt/build status --short | grep -v '^??' | head -3 | tr '\n' ' ')"

# 3b. regression: m2 passes its own checks but an earlier done milestone no longer accepts → m2 blocked, exit 3
python3 - <<'PY'
import json; s=json.load(open("spec.json"))
for f in s["features"]:
    if f["milestone"] == "m1":
        for a in f["acceptance"]:
            if a["type"] == "test": a["cmd"] = "false"
json.dump(s, open("spec.json","w"))
PY
python3 "$KIT/milestone.py" --spec spec.json --state .pipeline/build/state.json set m2 open --note "regression test" >/dev/null
bash "$KIT/build.sh" --project "$P" --accept-only m2 > "$D/build-regress.out" 2>&1; RC=$?
[ "$RC" = "3" ] && pass "accept-only m2 exits 3 when m1 regressed" || { fail "regression rc=$RC (expected 3)"; tail -3 "$D/build-regress.out"; }
grep -q "regression of an earlier milestone: m1" "$D/build-regress.out" && pass "regression names the milestone" || fail "no regression line: $(tail -2 "$D/build-regress.out")"
[ "$(jq -r '.milestones.m2.status' .pipeline/build/state.json)" = "blocked" ] && pass "m2 blocked by the regression" || fail "m2 state: $(jq -c .milestones .pipeline/build/state.json)"
git checkout -q spec.json; python3 "$KIT/milestone.py" --spec spec.json --state .pipeline/build/state.json set m2 done --note "restored" >/dev/null

# 4. blocked milestone: fake does nothing → tests still FAIL → accept exit 2 → blocked, exit 3
P2="$D/proj2"; mkdir -p "$P2"; cp "$P/spec.json" "$P/run_tests.sh" "$P/CHANGELOG.md" "$P/README.md" "$P2/"; cd "$P2"
git init -q -b main .; git config user.email t@t; git config user.name t; echo "PASS PASS FAIL FAIL" > tests.txt
python3 "$KIT/spec_check.py" spec.json --derive >/dev/null; git add -A; git commit -qm init
NIGHTSHIFT_CLAUDE="$LOOP/tests/fake_claude.sh" FAKE_CLAUDE_MODE=nothing bash "$KIT/build.sh" --project "$P2" --milestone next --cap 5 > "$D/build2.out" 2>&1; RC=$?
[ "$RC" = "3" ] && pass "unaccepted milestone exits 3" || { fail "build.sh rc=$RC (expected 3)"; tail -3 "$D/build2.out"; }
[ "$(jq -r '.milestones.m1.status' .pipeline/build/state.json)" = "blocked" ] && pass "m1 blocked with note" || fail "m1 state: $(jq -c .milestones .pipeline/build/state.json)"
cd "$P"

# 4b. accept-only re-runs the verdict without a child: fix the tests by hand, then accept-only → done
cd "$P2"; WT2="$P2/.pipeline/wt/build"; echo "PASS PASS PASS PASS" > "$WT2/tests.txt"; git -C "$WT2" commit -qam "fix tests by hand"
bash "$KIT/build.sh" --project "$P2" --accept-only m1 > "$D/build3.out" 2>&1; RC=$?
[ "$RC" = "0" ] && pass "accept-only exits 0 once checks pass" || { fail "accept-only rc=$RC"; tail -3 "$D/build3.out"; }
[ "$(jq -r '.milestones.m1.status' .pipeline/build/state.json)" = "done" ] && pass "accept-only marks m1 done" || fail "m1 state after accept-only: $(jq -c .milestones .pipeline/build/state.json)"
cd "$P"

# 5. ship_check on the accepted build worktree (main is untouched, so run it on the worktree checkout)
WT="$P/.pipeline/wt/build"
python3 "$KIT/ship_check.py" --spec spec.json --workdir "$WT" --tag v0.1.0 --security-confirmed > "$D/ship.out" 2>&1; SRC=$?
grep -q "acceptance" "$D/ship.out" && pass "ship_check ran acceptance" || fail "ship_check output: $(head -3 "$D/ship.out")"
jq -e '.checks[] | select(.name=="env-example") | .ok == true' "$WT/.pipeline/ship/v0.1.0/ship-report.json" >/dev/null && pass "env-example passes" || fail "env-example"
jq -e '.checks[] | select(.name=="git-clean") | .ok == false' "$WT/.pipeline/ship/v0.1.0/ship-report.json" >/dev/null && pass "git-clean false on a build branch (not main)" || fail "git-clean"
[ "$SRC" = "2" ] && pass "ship_check exits 2 while not on main" || fail "ship_check rc=$SRC"

# 6. feedback
printf '## 2026-09-16\n- the app crashed when I pasted a long note\n- search is slow with many notes\n- could not find the dark mode switch\n' > FEEDBACK.md
python3 "$KIT/feedback.py" --inbox FEEDBACK.md --backlog .loop/backlog.yaml > "$D/fb.out" 2>&1
grep -q "added 3" "$D/fb.out" && pass "feedback added 3 rows" || fail "feedback: $(cat "$D/fb.out")"
grep -q "## processed" FEEDBACK.md && pass "inbox marked processed" || fail "inbox not rewritten"
python3 "$KIT/feedback.py" --inbox FEEDBACK.md --backlog .loop/backlog.yaml | grep -q "added 0" && pass "feedback idempotent" || fail "feedback re-added"

echo
if [ "$FAILED" = "0" ]; then echo "test_pipeline.sh: all passed ($PASSED)"; else echo "test_pipeline.sh: FAILURES ($FAILED)"; exit 1; fi
