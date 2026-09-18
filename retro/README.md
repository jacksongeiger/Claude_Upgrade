# retro/ — does the kit get better?

The kit judges projects; this directory judges the kit. Every number is a
count over files the drivers already write (`.pipeline/`, `.loop/`); no
transcript is ever read, and nothing here calls a model.

```
collect.py      facts from one or more project dirs → history/<date>.json
report.py       one or more facts files → RETRO.md (tables; a trend across labels)
redact.py       a stream-json transcript → the same lines with every text field removed
                (numbers, ids, model names and event types survive; exit 2 if text leaks)
corpus_run.py   runs corpus/cases.json through the real scripts: each case is a
                mistake the system once made, turned into a check it must now pass
corpus/cases.json         the labelled regression corpus (kinds: check_plan, ux_supersede,
                          freeze, verdict, gate_silent, gate_fire)
corpus/streams/*.jsonl    redacted real streams + .meta.json (billed cost); replayed
                          through loop/tail.py by the kit-eval scorer
history/<date>.json       facts snapshots, one per retro
RETRO.md                  the latest report
```

## The loop

1. A project runs (validate, build, ux, ship, Nightshift). The drivers write
   ledgers, state files, events and verdicts as they go.
2. `collect.py --project <dir>... --out history/<date>.json --label vX.Y`
   counts them: first-try acceptance, cost per accepted milestone, kept /
   flat / regressed nights, denies, persona findings, human overrides.
3. `report.py --facts history/*.json --out RETRO.md` renders the latest
   snapshot and a trend table across labels.
4. A mistake found in a run becomes a case in `corpus/cases.json` (with the
   date and a one-line note), and a real stream that exposed a meter defect
   becomes a redacted fixture in `corpus/streams/`.
5. `loop/scorers/kit_eval.py` scores the kit on the shell suites (0.5), the
   corpus pass rate (0.3) and stream replay agreement (0.2). It is a
   Nightshift scorer for this repo, so the loop that improves the kit is
   judged by the kit's own past mistakes. Its inputs are pinned in the
   repo's manifest; an executor cannot relabel a case or a fixture.

## What the first replay found (2026-09-18)

The live cost meter summed every assistant row; the stream re-emits one
message per content block, and the row usage omits reasoning output. On 25
real streams the estimate landed anywhere from 0.42× to 3.5× the bill.
Pricing each message id once, adding `system/thinking_tokens` deltas at the
main model's output rate, and correcting the Fable/Opus price row put every
stream in 0.90×–1.33×. Four of those streams are the replay fixtures.

## Rules

- Redaction is a test, not a promise: `loop/tests/test_retro.py` asserts no
  text survives and that a redacted stream replays to the same bill.
- Cost per accepted milestone is a report metric only. It never enters a
  keep/undo decision (CLAUDE.md, Performance Benchmarking).
- A retro reads files the drivers wrote. If a number is not in a file, it is
  not in the retro.
