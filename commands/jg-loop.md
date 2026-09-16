---
description: Nightshift — the unattended improvement loop. init · dryrun · start · status · why · watch · stop · review · map
---

Drive Nightshift for the current project. The kit lives at `~/Claude_Upgrade/loop/`
(`KIT` below). The argument is `$ARGUMENTS`. Dispatch on its first word:

| Argument | Do this |
|---|---|
| *(empty)* or `status` | `python3 $KIT/status.py` and print its output verbatim. If there is no `.loop/` here, say so and point at `init`. |
| `init` | Follow **Init** below. Interactive; you are the planner with the human present. |
| `dryrun` | `bash $KIT/run.sh --dryrun` via Bash with `run_in_background`, then `Monitor` `.loop/events.log` for `STOP|ASK|ITER_DONE|DENY` for up to 30 min and narrate each event in one line. When it stops, print `python3 $KIT/status.py` and the dryrun's cost-agreement line. |
| `start [--cap USD] [--hours N]` | Refuse unless `init` and a `dryrun` have completed (`.loop/state.json` has `dryrun_ok: true`). Then `bash $KIT/run.sh --cap <USD> --hours <N>` with `run_in_background`, arm `/loop 20m /jg-loop status`, and `Monitor` `.loop/events.log` for `STOP|ASK|REGRESSION` for 30 min. Tell the human the cap, the hours, and that a macOS notification fires on every stop. |
| `why` | `python3 $KIT/status.py --why`: the current target, the weakest dimension, the rung, the candidates considered and why this row won. |
| `watch` | `Monitor` `.loop/events.log` for `STOP|ASK|REGRESSION|MERGED|DENY` (30 min), one line per event. |
| `stop [--now]` | `touch .loop/run/STOP` (stops between iterations). With `--now`, `bash $KIT/run.sh --kill` (kills the process group; partial branches stay unmerged). |
| `review` | `python3 $KIT/report.py` then print `.loop/report.md` — the plain-language digest. End by stating the exact `git merge --no-ff loop/<date>` command the human would run, and do **not** run it. |
| `map` | `python3 $KIT/map.py --tree` and print the tree; mention `ARCHITECTURE.md`. |
| anything else | Treat as `status`. |

## Rules

- **Never merge to main.** Not in `review`, not on request inside this command.
  If asked, print the command and stop.
- **Never install anything** during `init` without the human confirming the
  slug, tier and one-line purpose. Red tier: the human retypes the slug.
- **Never start an unattended run without a completed dryrun.** The dryrun is
  where the cost meter and the crash trap get proven.
- Everything you print in `status`, `why`, `watch` is plain text in the
  transcript; no images, no side panes — the terminal cannot show them.

## Init

Run in order, each step writing its artifact before the next. Stop and ask
the human only where marked.

1. **Preconditions.** Git repo, clean tree, `main` (or `master`) exists,
   `CLAUDE.md`/`README.md`/`CHANGELOG.md`/`DEAD_ENDS.md` present — if not,
   offer `/jg-new-project` and stop. `rdx status` (warn if the index is >7
   days old). `jq`, `python3` on PATH.
2. **Assess.** `python3 $KIT/assess.py` → print its JSON compactly.
3. **Tests.** Invoke the `test-assessor` agent with the assess output. Print
   the result: state, n_tests, pass rate, coverage, flaky, runtime.
   - `none`: **ask the human** whether to have a baseline suite written first
     (recommended). If yes: plan a characterisation suite for the public
     surface, dispatch `exec-sonnet` for it in a worktree, review with
     `reviewer`, and merge to the *loop* branch — never main. The human
     merges that to main before the first run.
4. **Goal.** Draft `GOAL.md` from README/CLAUDE.md as numbered lines, ≤ 8,
   each a measurable-ish outcome. **Ask the human** one question: "what
   should be better after six hours?" Fold the answer in. **Ask the human**
   to confirm the list. Write `~/.claude/nightshift/<slug>/goal.md` and a
   copy at `<project>/GOAL.md`.
5. **Scorers.** `python3 $KIT/init.py --propose` prints the proposed
   config as a table: scorers enabled, weights, commands, eps from three
   baseline runs, cap, hours, and the consequence preview ("First pick: …
   With this config the loop can ONLY improve: …"). If it refuses (one
   dimension, or the goal names none of them), tell the human exactly why
   and what would unblock it. **Ask the human** to confirm or edit.
6. **Tools.** For each capability gap in the assess output (no coverage
   tool, no bench, no a11y checker…), run `rdx search "<gap>"` and list slug
   / tier / one line. Human-gated installs only. Coverage tooling for the
   detected stack is a dev dependency, pinned, added to the manifest.
7. **Write.** `python3 $KIT/init.py --write` → config, manifest, agents
   copied to `.claude/agents/`, `.claude/settings.json` merged (hooks gated on
   `loop.pid`, statusline, `worktree.baseRef: head`, allowlist), `.gitignore`
   additions, seeded `backlog.yaml` (issues, TODOs by rung, coverage gaps),
   baseline row 0 in `scores.jsonl`. Print what was written.
8. Tell the human: next is `/jg-loop dryrun`.
