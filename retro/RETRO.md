# Retro — v3.3

Generated 2026-09-18T01:57:59Z from 5 project(s). Every number is a count over files the drivers wrote; no transcript was read.

## Totals

| what | value |
|---|---|
| validation runs | 3 (NO-GO 2, PIVOT 1) |
| milestones accepted first try / after retry / blocked | 3 / 0 / 0 |
| first-try acceptance rate | 1.00 |
| cost per accepted milestone | $4.07 |
| Nightshift iterations kept / flat / regressed | 2 / 1 / 0 |
| executor denies | budget 1, safety 1 |
| persona walkthroughs / findings | 12 / 11 |
| human overrides | none |
| total spend recorded | $44.31 |

## Per project

| project | validation | build (first/retry/blocked, $/accepted) | nightshift (kept/flat/regressed, stop) | persona | ship failures | overrides |
|---|---|---|---|---|---|---|
| pocket-notes | 0 run(s)  $0.00 | 3/0/0, $4.07 | 0/1/0, dryrun-complete | 12/12 done, 11 findings (0 deduped) | none | none |
| validate-small | 2 run(s) NO-GO 1, PIVOT 1 $4.09 | 0/0/0, — | 0/0/0, — | 0/0 done, 0 findings (0 deduped) | none | none |
| validate-mid | 1 run(s) NO-GO 1 $4.07 | 0/0/0, — | 0/0/0, — | 0/0 done, 0 findings (0 deduped) | none | none |
| yaml | 0 run(s)  $0.00 | 0/0/0, — | 1/0/0, dryrun-complete | 0/0 done, 0 findings (0 deduped) | none | none |
| prettytable | 0 run(s)  $0.00 | 0/0/0, — | 1/0/0, dryrun-complete | 0/0 done, 0 findings (0 deduped) | none | none |

## What to look at

- safety denies happened: an executor tried to leave its worktree; read `.loop/events.log`
- more ideas failed validation than passed: good, if the ledgers read true; spot-check one VERDICT.md
