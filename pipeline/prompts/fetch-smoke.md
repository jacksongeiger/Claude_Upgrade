You are a headless fetch smoke test. Do exactly these three commands, in
order, with Bash, and nothing else. Do not use WebFetch or WebSearch. Do not
read any file other than the outputs named here.

1. `python3 {{KIT}}/fetch.py probe --out {{RUN_DIR}}/reachable.json`
2. `python3 {{KIT}}/fetch.py get https://pypi.org/pypi/requests/json --extract json:info.version --run-dir {{RUN_DIR}} --ledger {{RUN_DIR}}/ledger.jsonl --claim smoke --measure pypi_version --origin pypi.org`
3. `python3 {{KIT}}/fetch.py get https://hn.algolia.com/api/v1/search?query=changelog --extract json:nbHits --run-dir {{RUN_DIR}} --ledger {{RUN_DIR}}/ledger.jsonl --claim smoke --measure hn_hits --origin hn.algolia.com`

Then reply with one JSON object and nothing else:
`{"probe_ok": true|false, "reachable_count": N, "pypi_value": "<value or null>", "hn_reason": "<reason or null>"}`
