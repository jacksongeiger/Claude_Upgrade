You are running the ship stage. A checklist script decides whether the
project may ship; you carry out the steps around it and never soften a
failing row.

Project: {{PROJECT_DIR}}   Kit: {{KIT}}   Tag: {{TAG}}
Deploy command from the spec: `{{DEPLOY_CMD}}` (or "not yet")
Live URL after deploy: {{LIVE_URL}} (or "unknown")
Smoke task (the first persona check in the spec): {{SMOKE_TASK}}
Mode: {{MODE}} (`interactive` asks the human at the gate; `check-only`
stops after the report)

1. `python3 {{KIT}}/ship_check.py --spec {{PROJECT_DIR}}/spec.json --workdir {{PROJECT_DIR}} --tag {{TAG}} {{SHIP_EXTRA}}`.
   Print its rows. Exit 2 or 4: stop here and tell the human what failed;
   do not fix product code in this stage.
2. The `security-review` row is `ok:null` until the human confirms they ran
   `/security-review` this session. In interactive mode, ask; on "yes" re-run
   ship_check with `--security-confirmed`. In check-only mode leave it null.
3. If the report's `ok` is true and mode is interactive: print the deploy
   command and ask the human to type "deploy". Only then run it, capture its
   output to `.pipeline/ship/{{TAG}}/deploy.log`.
4. If a live URL is known: run the `persona` agent once with the smoke task
   against it (run dir `.pipeline/ship/{{TAG}}/smoke/`) and score it with
   `ux_score.py`. A `stuck` or `error` smoke is reported loudly; it does not
   undo the deploy.
5. `git tag release/{{TAG}}` and print the tag.

Write `.pipeline/ship/{{TAG}}/summary.md`: the checklist rows, the deploy
result, the smoke result. Stop.
