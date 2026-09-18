#!/usr/bin/env bash
# End-to-end test of the validation stage driver with the fake claude and a
# local evidence server: GO, NO-GO (DEAD_ENDS), PIVOT, the cap, the small
# band's four roles, and the handoff into a spec that then passes the gate.
set -uo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
LOOP="$(cd "$KIT/../loop" && pwd -P)"
PASSED=0; FAILED=0
pass() { echo "  ok   $*"; PASSED=$((PASSED+1)); }
fail() { echo "  FAIL $*"; FAILED=$((FAILED+1)); }
echo "test_validate.sh"

D=$(mktemp -d); export HOME="$D/home"; mkdir -p "$HOME"
export NO_PROXY="127.0.0.1,localhost" no_proxy="127.0.0.1,localhost"
# evidence server
E="$D/evidence"; mkdir -p "$E"
echo '{"downloads":{"monthly":120000}}' > "$E/api.json"
echo '{"total":9}' > "$E/issues.json"
for n in 1 2 3; do printf '<html><body><p>I hate writing changelogs by hand, post %s</p></body></html>' "$n" > "$E/thread$n.html"; done
printf '<html><body>this tool is abandoned</body></html>' > "$E/dead.html"
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
(cd "$E" && python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) & SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
for i in $(seq 1 40); do curl -sS -m 1 -o /dev/null "http://127.0.0.1:$PORT/api.json" 2>/dev/null && break; sleep 0.25; done
export FAKE_EVIDENCE_URL="http://127.0.0.1:$PORT"
export NIGHTSHIFT_CLAUDE="$KIT/tests/fake_validate_claude.sh"

mkproj() { local p="$D/$1"; mkdir -p "$p"; (cd "$p" && git init -q -b main . && git config user.email t@t && git config user.name t && echo "# p" > README.md && git add -A && git commit -qm init); echo "$p"; }

# 1. GO on the mid band: eight roles, refetch against the live server, handoff, gate
P=$(mkproj go)
FAKE_VALIDATE_MODE=go bash "$KIT/validate.sh" --project "$P" --idea "a changelog generator from git history" --build-usd 50 > "$D/go.out" 2>"$D/go.err"; RC=$?
VD=$(tail -n 1 "$D/go.out")
[ "$RC" = 0 ] && pass "GO exits 0" || { fail "GO rc=$RC"; tail -5 "$D/go.err"; cat "$VD/driver.log" 2>/dev/null | tail -5; }
[ "$(jq -r .verdict "$VD/verdict.json" 2>/dev/null)" = "GO" ] && pass "verdict.json says GO" || fail "verdict: $(jq -c . "$VD/verdict.json" 2>/dev/null | head -c 300)"
[ "$(jq -r '.claims.c1.tier' "$VD/verdict.json")" = "2" ] && pass "core claim at tier 2 after a live re-fetch" || fail "c1 tier $(jq -c .claims.c1 "$VD/verdict.json")"
[ "$(jq -r '.claims.c3.tier' "$VD/verdict.json")" = "1" ] && pass "three independent anecdotes make tier 1" || fail "c3: $(jq -c .claims.c3 "$VD/verdict.json")"
grep -q "set by skeptic" "$VD/VERDICT.md" && pass "the skeptic's stricter number was frozen" || fail "stricter number not in VERDICT.md"
[ "$(jq -s '[.[] | select(.stage | startswith("validate:"))] | length' "$P/.pipeline/ledger.jsonl")" = "5" ] && pass "five metered children on the mid band" || fail "ledger: $(jq -c '.stage' "$P/.pipeline/ledger.jsonl" | tr '\n' ' ')"
[ -f "$VD/freeze.sha" ] && [ "$(sha256sum "$VD/plan.frozen.json" | cut -c1-64)" = "$(cat "$VD/freeze.sha")" ] && pass "frozen plan intact after fetching" || fail "freeze sha mismatch"
grep -q "missing.html" "$VD/VERDICT.md" && grep -q "unobtainable: 404" "$VD/VERDICT.md" && pass "a required source that 404s is recorded, not invented" || fail "404 row missing from VERDICT.md"
[ ! -f "$P/DEAD_ENDS.md" ] && pass "no DEAD_ENDS entry on GO" || fail "DEAD_ENDS written on GO"
ls "$VD/bodies"/*.txt >/dev/null 2>&1 && pass "bodies stored for the judge" || fail "no bodies"
# handoff + gate
printf '{"version":1,"name":"P","one_liner":"x","stack":{"language":"bash","ui":false,"llm":false},"needs":["unit-tests"],"milestones":[{"id":"m1","title":"a","depends_on":[]}],"features":[{"id":"f-001","milestone":"m1","title":"t","acceptance":[{"type":"test","cmd":"true","must":"pass"}]}],"success":["Every acceptance check passes"],"budget":{"build_usd":40,"nightshift_cap_usd":5}}' > "$P/spec.json"
python3 "$KIT/validate.py" handoff --dir "$VD" --spec "$P/spec.json" >/dev/null && pass "handoff wrote the spec" || fail "handoff failed"
jq -e '.anchors | length == 3' "$P/spec.json" >/dev/null && jq -e '.validation.slug' "$P/spec.json" >/dev/null && pass "anchors and validation block present" || fail "spec after handoff: $(jq -c '{anchors, validation}' "$P/spec.json")"
(cd "$P" && python3 "$KIT/spec_check.py" spec.json --gate-validate >/dev/null) && pass "spec passes the validation gate" || fail "gate refused a GO spec"
(cd "$P" && python3 "$KIT/spec_check.py" spec.json --derive >/dev/null 2>&1); grep -q "validate/\*/bodies" "$P/.gitignore" && pass "derive ignores bodies/" || fail "bodies not ignored"
jq '.budget.build_usd = 90' "$P/spec.json" > "$P/s2.json" && mv "$P/s2.json" "$P/spec.json"
(cd "$P" && python3 "$KIT/spec_check.py" spec.json --gate-validate >/dev/null); [ "$?" = 2 ] && pass "a budget above the validated one is refused" || fail "budget ceiling not enforced"

# 2. NO-GO: the setter's kill number is above the fetched value
P=$(mkproj nogo)
FAKE_VALIDATE_MODE=nogo bash "$KIT/validate.sh" --project "$P" --idea "a changelog generator nobody wants" --build-usd 50 > "$D/nogo.out" 2>"$D/nogo.err"; RC=$?
[ "$RC" = 2 ] && pass "NO-GO exits 2" || { fail "NO-GO rc=$RC"; tail -3 "$D/nogo.err"; }
grep -q "changelog generator nobody wants" "$P/DEAD_ENDS.md" 2>/dev/null && pass "NO-GO lands in DEAD_ENDS.md with the idea" || fail "DEAD_ENDS missing"

# 3. PIVOT: a supporting claim dies; the author is re-run once, then the second PIVOT is NO-GO
P=$(mkproj pivot)
FAKE_VALIDATE_MODE=pivot bash "$KIT/validate.sh" --project "$P" --idea "a changelog generator with a weak side claim" --build-usd 50 > "$D/pivot.out" 2>"$D/pivot.err"; RC=$?
VD=$(tail -n 1 "$D/pivot.out")
[ "$RC" = 2 ] && grep -q "second PIVOT" "$VD/verdict.json" && pass "PIVOT re-runs once, then NO-GO" || { fail "pivot rc=$RC $(jq -c '.contradictions' "$VD/verdict.json" 2>/dev/null)"; }
[ "$(jq -s '[.[] | select(.stage | startswith("validate:") and endswith(":author"))] | length' "$P/.pipeline/ledger.jsonl")" = "2" ] && pass "the author ran twice" || fail "author runs: $(jq -s '[.[] | select(.stage | endswith(":author"))] | length' "$P/.pipeline/ledger.jsonl")"

# 4. the cap: expensive children stop the stage with exit 3 and no verdict
P=$(mkproj cap)
FAKE_CLAUDE_COST=1.9 bash "$KIT/validate.sh" --project "$P" --idea "an idea too expensive to validate" --build-usd 50 --cap 2.5 > "$D/cap.out" 2>"$D/cap.err"; RC=$?
[ "$RC" = 3 ] && pass "cap reached exits 3" || fail "cap rc=$RC"
[ ! -f "$P/.pipeline/validate/"*/verdict.json ] 2>/dev/null && pass "no verdict written at the cap" || fail "verdict written despite the cap"
grep -q "cap reached" "$P/.pipeline/events.log" && pass "cap is logged" || fail "cap not logged"

# 5. the small band: four roles, no skeptic, no judge, GO on the core alone
P=$(mkproj small)
FAKE_VALIDATE_MODE=go bash "$KIT/validate.sh" --project "$P" --idea "a tiny changelog helper" --build-usd 10 > "$D/small.out" 2>"$D/small.err"; RC=$?
VD=$(tail -n 1 "$D/small.out")
[ "$RC" = 0 ] && pass "small band GO" || { fail "small rc=$RC"; tail -3 "$D/small.err"; }
[ "$(jq -s '[.[] | select(.stage | startswith("validate:"))] | length' "$P/.pipeline/ledger.jsonl")" = "3" ] && pass "three metered children on the small band" || fail "small ledger: $(jq -c '.stage' "$P/.pipeline/ledger.jsonl" | tr '\n' ' ')"
[ ! -f "$VD/skeptic.json" ] && [ ! -f "$VD/judge.json" ] && pass "no skeptic, no judge under \$20" || fail "skeptic or judge ran on the small band"

echo
if [ "$FAILED" = "0" ]; then echo "test_validate.sh: all passed ($PASSED)"; else echo "test_validate.sh: FAILURES ($FAILED)"; exit 1; fi
