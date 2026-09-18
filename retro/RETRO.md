# Retro — v3.3-inbox

Generated 2026-09-18T03:54:18Z from 6 project(s). Every number is a count over files the drivers wrote; no transcript was read.

## Totals

| what | value |
|---|---|
| validation runs | 4 (GO 1, NO-GO 2, PIVOT 1) |
| milestones accepted first try / after retry / blocked | 5 / 1 / 0 |
| first-try acceptance rate | 0.83 |
| cost per accepted milestone | $5.17 |
| Nightshift iterations kept / flat / regressed | 3 / 2 / 0 |
| executor denies | budget 1, safety 1 |
| persona walkthroughs / findings | 12 / 11 |
| human overrides | security-confirmed 1, validation-overrule 1 |
| total spend recorded | $76.63 |

## Per project

| project | validation | build (first/retry/blocked, $/accepted) | nightshift (kept/flat/regressed, stop) | persona | ship failures | overrides |
|---|---|---|---|---|---|---|
| pocket-notes | 0 run(s)  $0.00 | 3/0/0, $4.07 | 0/1/0, dryrun-complete | 12/12 done, 11 findings (0 deduped) | none | none |
| validate-small | 2 run(s) NO-GO 1, PIVOT 1 $4.09 | 0/0/0, — | 0/0/0, — | 0/0 done, 0 findings (0 deduped) | none | none |
| validate-mid | 1 run(s) NO-GO 1 $4.07 | 0/0/0, — | 0/0/0, — | 0/0 done, 0 findings (0 deduped) | none | none |
| yaml | 0 run(s)  $0.00 | 0/0/0, — | 1/0/0, dryrun-complete | 0/0 done, 0 findings (0 deduped) | none | none |
| prettytable | 0 run(s)  $0.00 | 0/0/0, — | 1/0/0, dryrun-complete | 0/0 done, 0 findings (0 deduped) | none | none |
| inbox-triage | 1 run(s) GO 1 $4.71 | 2/1/0, $6.27 | 1/1/0, dryrun-complete | 0/0 done, 0 findings (0 deduped) | none | security-confirmed 1, validation-overrule 1 |

## Trend

| label | first-try rate | $/accepted milestone | kept share | findings per walkthrough | overrides | spend |
|---|---|---|---|---|---|---|
| v3.3 | 1.00 | $4.07 | 0.67 | 0.92 | 0 | $44.31 |
| v3.3-inbox | 0.83 | $5.17 | 0.60 | 0.92 | 2 | $76.63 |

## What to look at

- safety denies happened: an executor tried to leave its worktree; read `.loop/events.log`
- more ideas failed validation than passed: good, if the ledgers read true; spot-check one VERDICT.md
