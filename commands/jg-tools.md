---
description: Stage 2 — the tooling plan. From the spec's needs to a plan of pinned tools the human approves; nothing installs itself.
---

Kit: `~/Claude_Upgrade/pipeline/` (`KIT`), Nightshift kit `~/Claude_Upgrade/loop/`
(`LOOP`). Argument: `$ARGUMENTS`.

| Argument | Do this |
|---|---|
| *(empty)* or `plan` | Steps 1–3 below. |
| `verify` | `python3 $LOOP/assess.py > .pipeline/assess.json && python3 $KIT/tools_plan.py --verify --spec spec.json --assess .pipeline/assess.json --plan .pipeline/tooling-plan.json`; print the table; exit 3 means a required gap is still open — say which. |
| `mark <need> <slug>` | `python3 $KIT/tools_plan.py --mark <need> <slug> --plan .pipeline/tooling-plan.json`. |

## Plan

1. `python3 $LOOP/assess.py > .pipeline/assess.json` (create `.pipeline/` if needed).
2. `python3 $KIT/tools_plan.py --spec spec.json --assess .pipeline/assess.json --out .pipeline/tooling-plan.json`
   and print its table. For each gap whose candidates came only from the
   built-in table, you may add candidates you know to be current by editing
   the plan file's `candidates` list (slug, kind, version, `source: "web"`,
   one-line why). Do not remove the built-in ones.
3. Show the human one line per gap: need · chosen candidate · kind · pinned
   version · why. **Ask the human to approve each install.** For an approved
   `npm-dev` or `pip-dev` candidate, add it to the manifest with the pinned
   version (`package.json` devDependencies / `requirements.txt`) and run the
   project's install command (`npm install`, `pip install -r`). For an `rdx`
   candidate, `rdx install <slug>` only after the human confirms the tier;
   never `-y`. For a `note` candidate, print the note; it is work, not an
   install. Record each approval with `mark`.
4. Run `verify`. Stop when it exits 0, or tell the human exactly which need
   is still unresolved and what would close it.

## Rules

- Nothing installs without a human "yes" naming the slug. Red-tier rdx
  candidates require the human to retype the slug.
- Pin every version. Never install to satisfy a need the spec does not list.
- If assess.py already reports a need as ready, do not touch it.
