---
description: Stage 3 — build the spec milestone by milestone. Fable plans, Sonnet/Opus build in worktrees, reviewers check, merge.sh lands, accept.py decides. You merge each milestone to main.
---

Kit: `~/Claude_Upgrade/pipeline/` (`KIT`). Argument: `$ARGUMENTS`.

| Argument | Do this |
|---|---|
| *(empty)* or `status` | `python3 $KIT/milestone.py --spec spec.json --state .pipeline/build/state.json list`; then the last 10 lines of `.pipeline/events.log`; then the ledger total (`jq -s 'map(.cost_usd)|add' .pipeline/ledger.jsonl`). |
| `next` | `bash $KIT/build.sh --project . --milestone next` via Bash with `run_in_background`; `Monitor` `.pipeline/events.log` for `MILESTONE_|STOP|CHILD_DONE|DENY` (30 min); narrate each event in one line; when it stops print `status` and the merge command it printed. |
| `all [--cap USD]` | Same with `--milestone all`, cap from the argument or the spec's `budget.build_usd`. Re-arm the monitor while it runs. |
| `<milestone id>` | Same with `--milestone <id>` (re-runs a blocked milestone; the previous summary is handed to the planner as history). |
| `why <id>` | Print `.pipeline/wt/build/.pipeline/build/<id>/summary.md` and `acceptance.final.json` (which checks failed, in plain words). |
| `stop` | `bash $KIT/build.sh --kill --project .`. |
| `merge <id>` | Print `git merge --no-ff build/<id>` (the tag) or the branch name from `state.json`, and stop. Never run it. |

## Rules

- The build branch is `build/<date>`; a tag `build/<milestone>` marks each
  accepted milestone. **Never merge to main**; print the command.
- Never edit `spec.json`, acceptance checks or tests to make a milestone
  pass. A failing check is either a code fix (re-run the milestone) or a
  spec change the human makes in `/jg-spec`.
- A milestone is done when `accept.py` exits 0, not when the planner says so.
- `all` stops on the first blocked milestone; read `why` before re-running.
