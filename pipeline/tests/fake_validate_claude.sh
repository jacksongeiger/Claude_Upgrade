#!/usr/bin/env bash
# A stand-in for `claude -p` in the validation stage's end-to-end test. Reads
# the role from the prompt text, writes the file that role would write
# (using the real fetch.py for the fetcher), and emits the stream-json shapes
# child.sh/tail.py consume. Driven by:
#   VALIDATE_DIR        exported by validate.sh
#   PIPELINE_KIT        exported by child.sh
#   FAKE_EVIDENCE_URL   a local http server the test started
#   FAKE_VALIDATE_MODE  go (default) | nogo (setter's kill number too high) | pivot (a supporting claim dies)
#   FAKE_CLAUDE_COST    result cost per child (default 0.30)
set -uo pipefail
PROMPT="${2:-}"; for a in "$@"; do case "$a" in -p) ;; esac; done
MODE="${FAKE_VALIDATE_MODE:-go}"; COST="${FAKE_CLAUDE_COST:-0.30}"
D="${VALIDATE_DIR:?}"; KIT="${PIPELINE_KIT:?}"; URL="${FAKE_EVIDENCE_URL:?}"
emit() { printf '%s\n' "$1"; }
emit '{"type":"system","subtype":"init","model":"claude-fable-5-1","session_id":"fake"}'
emit '{"type":"assistant","message":{"model":"claude-fable-5-1","content":[{"type":"text","text":"working"}],"usage":{"input_tokens":800,"output_tokens":120,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}'

role=""
case "$PROMPT" in
  *"You are the author"*) role=author ;;
  *"You set the bar"*) role=setter ;;
  *"You are the skeptic"*) role=skeptic ;;
  *"You fetch evidence"*) role=fetcher ;;
  *"evidence-judge"*) role=judge ;;
esac

case "$role" in
  author)
    cat > "$D/claims.json" <<EOF
{"claims":[
 {"id":"c1","core":true,"who":"solo developers","pain":"changelogs are written by hand and go stale","statement":"developers want a changelog generated from git history","metric_hint":"downloads of existing tools"},
 {"id":"c2","core":false,"who":"maintainers","pain":"release notes take an hour","statement":"existing tools require conventional commits","metric_hint":"issues complaining"},
 {"id":"c3","core":false,"who":"teams","pain":"nobody reads the changelog","statement":"people describe this pain in public","metric_hint":"forum posts"}
]}
EOF
    ;;
  setter)
    KILL=10000; [ "$MODE" = nogo ] && KILL=500000
    SP=3; [ "$MODE" = pivot ] && SP=50
    cat > "$D/plan.json" <<EOF
{"claims":[
 {"id":"c1","measures":[{"name":"monthly_downloads","unit":"downloads/month","direction":"min","kill_value":$KILL,
   "sources":[{"kind":"url","where":"$URL/api.json","extract":"json:downloads.monthly"}]}]},
 {"id":"c2","measures":[{"name":"issues_mentioning","unit":"issues","direction":"min","kill_value":$SP,
   "sources":[{"kind":"url","where":"$URL/issues.json","extract":"json:total"}]}]},
 {"id":"c3","measures":[{"name":"complaints","unit":"posts","direction":"min","kill_value":1,
   "sources":[{"kind":"url","where":"$URL/thread1.html","extract":"text"},{"kind":"url","where":"$URL/thread2.html","extract":"text"},{"kind":"url","where":"$URL/thread3.html","extract":"text"}]}]}
]}
EOF
    ;;
  skeptic)
    cat > "$D/skeptic.json" <<EOF
{"claims":[
 {"id":"c1","kill_numbers":{"monthly_downloads":20000},"added_measures":[],
  "disconfirming_sources":[{"where":"$URL/dead.html","extract":"text","note":"abandoned tools"},{"where":"$URL/missing.html","extract":"text","note":"a page that does not exist"}]},
 {"id":"c2","kill_numbers":{},"added_measures":[],
  "disconfirming_sources":[{"where":"$URL/dead.html","extract":"text"},{"where":"$URL/thread1.html","extract":"text"}]},
 {"id":"c3","kill_numbers":{},"added_measures":[],
  "disconfirming_sources":[{"where":"$URL/dead.html","extract":"text"},{"where":"$URL/thread2.html","extract":"text"}]}
]}
EOF
    printf '# The case against\n\nNobody pays for changelogs.\n' > "$D/skeptic.md"
    ;;
  fetcher)
    python3 - "$D" "$KIT" <<'PY'
import json, subprocess, sys, os
d, kit = sys.argv[1], sys.argv[2]
env = dict(os.environ); env["NO_PROXY"] = "127.0.0.1,localhost"; env["no_proxy"] = "127.0.0.1,localhost"
fz = json.load(open(os.path.join(d, "plan.frozen.json")))
origins = {"thread1.html": "alice", "thread2.html": "bob", "thread3.html": "carol", "dead.html": "127.0.0.1", "missing.html": "127.0.0.1"}
def get(where, extract, claim, measure, unit, required=False):
    origin = next((v for k, v in origins.items() if where.endswith(k)), "127.0.0.1")
    cmd = [sys.executable, os.path.join(kit, "fetch.py"), "get", where, "--extract", extract, "--run-dir", d,
           "--ledger", os.path.join(d, "ledger.jsonl"), "--claim", claim, "--measure", measure, "--origin", origin]
    if unit: cmd += ["--unit", unit]
    if required: cmd.append("--required")
    subprocess.run(cmd, capture_output=True, text=True, env=env)
for c in fz["claims"]:
    for m in c["measures"]:
        for s in m["sources"]:
            get(s["where"], s.get("extract", "text"), c["id"], m["name"], m.get("unit"))
    for r in c.get("required_sources", []):
        get(r["where"], r.get("extract", "text"), c["id"], r.get("measure") or "text", None, required=True)
PY
    ;;
  judge)
    python3 - "$D" <<'PY'
import json, sys, os
d = sys.argv[1]
rows = [json.loads(l) for l in open(os.path.join(d, "ledger.jsonl")) if l.strip()]
out = {"rows": [{"index": i, "score": 2, "quote": "I hate writing changelogs by hand"} for i, r in enumerate(rows) if r["source"].get("body_hash")]}
json.dump(out, open(os.path.join(d, "judge.json"), "w"))
PY
    ;;
  *) emit '{"type":"assistant","message":{"model":"claude-fable-5-1","content":[{"type":"text","text":"unknown role"}],"usage":{"input_tokens":1,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}' ;;
esac
emit "{\"type\":\"result\",\"subtype\":\"success\",\"total_cost_usd\":$COST,\"num_turns\":3,\"duration_ms\":900,\"result\":\"$role done\"}"
exit 0
