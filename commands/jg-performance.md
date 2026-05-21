You are running a performance benchmark on the current project. Follow this flow.

## 1. Metric selection
- Identify the metrics that actually matter for *this* project. Examples by project type:
  - Trading bots: PnL, win rate, drawdown, slippage, latency-to-fill.
  - Data pipelines: throughput (rows/sec), end-to-end latency, peak memory, cost per run.
  - Web apps: p50/p95 latency, error rate, time to first byte, bundle size.
  - CLI tools: wall-clock time on a representative input, peak memory.
  - ML models: held-out accuracy/F1/AUC plus inference latency.
- Do **not** force metrics that don't apply. If the project has no meaningful performance dimension, say so explicitly and stop here — write "Performance: N/A for this project" and return.
- If `CHANGELOG.md` shows performance has been benchmarked before, reuse those metrics. Consistency across versions matters more than picking a new "better" metric.

## 2. Run the benchmark
- Run whatever scripts, harnesses, or test runs produce the metrics.
- Capture raw output (don't paraphrase numbers — copy them).
- Note the conditions: input size, hardware, date/time, any flags or environment differences from the last run.

## 3. Compare against last entry
- Read the most recent `CHANGELOG.md` entry that included a `**Performance:**` line.
- Show the comparison side-by-side: metric, previous value (with version + date), current value.
- If this is the first benchmark, say so and skip the comparison.

## 4. Stamp the results
- Tag the current run with the project's current version (from CHANGELOG.md or `git describe --tags --always`) and today's date+time.
- Format the stamp as the global CLAUDE.md's CHANGELOG schema: `[metric value] @ v[X.X] [YYYY-MM-DD HH:MM TZ]`.

## 5. Verdict
- Give a one-line verdict: **improved**, **regressed**, or **no change**.
- Back it up with specifics: which metric moved by how much in which direction. "Improved 12% on p95 latency (340ms → 300ms), no change on throughput, regressed 5% on peak memory."
- If the change is within noise, say "no change (within noise)" rather than overclaiming.

## 6. Update CHANGELOG.md
- Use the same format `/jg-changelog` produces (version header, **What changed**, **Why**, **Result**, **Performance**, **Breaking changes**, **Dependencies**). Consistency across entries matters.
- Either: prepend a new version entry if this run accompanies code changes,
- Or: append the benchmark result to the existing current-version entry's `**Performance:**` line if the code didn't change since the last entry.
- Never silently overwrite an old benchmark — versions and timestamps are how we tell runs apart.

## 7. Next optimization
- Recommend exactly one highest-impact optimization to pursue next. Not three, not "a few options" — one.
- Justify it: which metric it would move, by roughly how much, and at what cost (code complexity, risk, time).

## Output
End with the standard format from the global CLAUDE.md:
- Summary: verdict + the single most important number
- Next best move: the one recommended optimization
- Git push recommendation: push if CHANGELOG.md was updated; otherwise hold
