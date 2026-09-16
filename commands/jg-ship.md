---
description: Stage 6 — ship. A checklist script says whether the project may ship; the human approves the deploy; a persona smoke run checks the live site.
---

Kit: `~/Claude_Upgrade/pipeline/` (`KIT`). Argument: `$ARGUMENTS` (a tag like
`v0.1.0`; default: the next patch after the last `release/*` tag, or `v0.1.0`).

| Argument | Do this |
|---|---|
| `check [tag]` | `python3 $KIT/ship_check.py --spec spec.json --workdir . --tag <tag>`; print every row; say plainly what blocks. |
| `<tag>` | Follow `$KIT/prompts/ship.md` in this session with `MODE=interactive`: run the checklist; ask whether `/security-review` was run this session; if the report is ok, print the deploy command from `spec.json` → `stack.deploy.cmd` and **wait for the human to type "deploy"**; run it; smoke-test the live URL with the `persona` agent; tag `release/<tag>`. |
| `report [tag]` | Print `.pipeline/ship/<tag>/summary.md`. |

## Rules

- A red checklist row is never softened, skipped or explained away in this
  stage. Fix the cause elsewhere (`/jg-build`, `/jg-spec`) and come back.
- The deploy runs only after the human types "deploy". Never push, never
  deploy on your own initiative.
- A failed smoke run is reported loudly and does not undo a deploy; the human
  decides.
