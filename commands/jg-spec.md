---
description: Stage 1 — the project interview. Produces spec.json, the file every later stage reads. Ends when the human signs it.
---

Run the interview for the current project. Kit: `~/Claude_Upgrade/pipeline/`
(`KIT`). Argument: `$ARGUMENTS`.

| Argument | Do this |
|---|---|
| *(empty)* | Interactive interview, below. |
| `check` | `python3 $KIT/spec_check.py spec.json` and print the result. |
| `derive` | `python3 $KIT/spec_check.py spec.json --derive`; print what changed (`git status --short`). |
| `show` | Print `SPEC.md`, then `python3 $KIT/spec_check.py spec.json`. |
| `answers <file.json>` | Unattended: `bash $KIT/child.sh --project . --prompt $KIT/prompts/interview.md --stage spec --budget 4 --sub MODE=answers --sub-file ANSWERS=<file> --sub PROJECT_DIR=$PWD --sub KIT=$KIT --sub-file EXISTING_SPEC=spec.json --sub ASSESS="$(python3 ~/Claude_Upgrade/loop/assess.py 2>/dev/null | head -c 4000)"` and print the child's result. |

## Interactive interview

You are Fable with the human present. Follow `$KIT/prompts/interview.md`
exactly, with `MODE=interactive`, `PROJECT_DIR` = the current directory,
`KIT` as above, `EXISTING_SPEC` = the contents of `spec.json` if it exists,
`ASSESS` = the output of `python3 ~/Claude_Upgrade/loop/assess.py` if this
is a git repo. Ask each round with the terminal question tool. Rewrite
`spec.json` after every round and run `spec_check.py`; never end a round on
a spec that does not validate.

## Rules

- Adjectives are not checks. "Fast", "clean", "intuitive" become a `perf`,
  `lighthouse`/`gate` or `persona` entry with a number, or they become
  `manual` and are counted against the 20% limit.
- Feasibility first, per CLAUDE.md: name the one assumption the project
  depends on and check it before the gate. A no-go goes to `DEAD_ENDS.md`.
- The gate is the word "signed" typed by the human. Only then commit
  `spec.json`, `SPEC.md`, `GOAL.md` as `spec: v1 signed`. Never commit
  before it, never push.
- Do not build, install or read the codebase beyond the assessment.
