---
description: Stage 0 — validate the idea before the interview. Claims, frozen kill numbers, evidence with sources, a verdict a script computes. Replaces /jg-feasibility.
---

Kit: `~/Claude_Upgrade/pipeline/` (`KIT`). Argument: `$ARGUMENTS`.

| Argument | Do this |
|---|---|
| `--build-usd N "<idea or URL>"` | `bash $KIT/validate.sh --project . --idea "<idea>" --build-usd N` and print the last line of the run's `VERDICT.md` plus its path. Exit 0 GO · 2 NO-GO · 3 needs-human · 4 infra (blocked here: run it on the Mac) · 5 PIVOT (the author was re-run once; the human decides). |
| `show` | Print `.pipeline/validate/*/VERDICT.md`, newest first. |
| `overrule <slug> --measure M --kill-value V --direction min\|max "<reason>"` | `python3 $KIT/validate.py overrule --dir .pipeline/validate/<slug> --by "$(git config user.name)" --reason "<reason>" --measure M --kill-value V --direction <dir>` then `validate.py verdict --dir ... --no-refetch`. The overrule is stored with the verdict and `/jg-feedback` re-checks its number after ship. |
| `handoff <slug>` | `python3 $KIT/validate.py handoff --dir .pipeline/validate/<slug> --spec spec.json`: writes the claims as `anchors`, the kill numbers as success lines, and the `validation` block the spec gate reads. `/jg-spec`'s round 4 does this itself; use this only to re-stamp after a re-run. |
| `experiment <slug> <claim> <measure> <value> <unit> "<where>"` | Record a tier-3 row from an experiment the human ran (a usage log, signups, a payment): `python3 $KIT/fetch.py get "<where>" --extract text --source-kind experiment --run-dir .pipeline/validate/<slug> --ledger .pipeline/validate/<slug>/ledger.jsonl --claim <claim> --measure <measure> --origin human` then edit that row's `value` to the number and re-run `validate.py verdict`. Large-band builds need one before any milestone with dependencies. |

## What the driver does

Eight roles, each a fresh `claude -p` child, each writing a file the next
reads; the script between them does the redaction, the freeze, the budget
and the verdict. Under a $20 build budget only four run (author, setter,
fetcher, referee), because anecdotes never satisfy the core claim there and
the skeptic's numbers would go unused.

1. author (Fable): 3–6 claims, one core, no numbers
2. setter (Fable, fresh): measures, sources, kill numbers
3. skeptic (Fable, fresh, numbers redacted): stricter numbers, added measures, two disconfirming sources per claim
4. freeze (script): the stricter number wins; sha recorded
5. fetcher (Sonnet, read-only plan): `fetch.py` for every source; a ledger row each
6. judge (Sonnet, files only): does the body say what the row claims, 0/1/2 with the quote
7. referee (script): re-fetch, tiers, kill numbers, the verdict, `VERDICT.md`
8. you: read it; overrule in writing if you must

## Rules

- Never paste evidence into the ledger by hand except through the
  `experiment` path above, which marks the row as yours.
- Never edit `plan.frozen.json`; the sha will catch it and the run is void.
- A NO-GO already lives in `DEAD_ENDS.md`. Do not re-run the same idea to
  shop for a better number; change the idea or overrule with a reason.
- Blocked hosts are an infra result, not a NO-GO: the cloud cannot reach
  forums or search engines, the Mac can.
