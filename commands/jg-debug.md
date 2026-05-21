> Overlap note: This is a personal variant of `superpowers:systematic-debugging`. Differences: (1) enforces the project's "summary + next best move" output format from the global CLAUDE.md, (2) requires explicit root-cause statement *in writing* before any fix is proposed, (3) requires a regression-protection artifact (comment or log line) when the bug was non-obvious. If you do not need those three things, prefer `superpowers:systematic-debugging`.

You are debugging an issue. Follow this flow strictly — do not skip steps, do not reorder.

## 1. Reproduce
- Before touching any code, reproduce the issue and confirm you can see it happening.
- State what you ran, what you expected, and what actually happened.
- If you cannot reproduce, stop and ask for repro steps. Do not guess.

## 2. Root cause
- Identify the root cause — not just the symptom. A stack trace is a symptom; the cause is *why* that line was reached with that state.
- State the cause explicitly in writing before proposing a fix. Phrase it as: "The bug happens because X, which causes Y."
- If you only have a hypothesis, say "hypothesis:" and validate it before treating it as the cause.

## 3. Propose the fix
- Propose a fix and explain in one or two sentences *why* it addresses the root cause stated in step 2.
- If the fix only handles the symptom (e.g. catching the error rather than preventing it), say so explicitly and justify why that's the right call here.

## 4. Implement
- Implement the fix. Keep the change minimal — do not refactor surrounding code unless the cause requires it.

## 5. Verify
- Re-run the original repro and confirm the issue is gone.
- Run the existing test suite (or a relevant subset) and confirm nothing else broke.
- If verification isn't possible (no tests, can't run the app), say so explicitly rather than claiming success.

## 6. Regression guard
- If the bug was non-obvious — a subtle invariant, an ordering assumption, a silent type coercion, an off-by-one — add a comment, a log line, or (best) a test that would have caught it.
- If the bug was obvious in hindsight, skip this step; not every fix needs a monument.

## Output
End with the standard format from the global CLAUDE.md:
- Summary of what was done (one paragraph max)
- Next best move (one or two bullets)
- Git push recommendation with reasoning
