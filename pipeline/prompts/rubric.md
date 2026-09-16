# UX judge rubric

Fixed grading criteria for `agents/persona-judge.md`. The judge reads a
persona's `trail.json` (fresh context, read-only — it did not drive the
browser) and scores it against these five criteria, 0/1/2 each, then writes
`judge.json` next to the trail. Never invent a sixth criterion and never
change these score bands.

## 1. Findability — could the user find what the task needed?

- **0** — the user never located the control or content the task needed, or
  only stumbled onto it after exhausting the visible options.
- **1** — the user found it, but only after backtracking or scanning several
  screens/sections that turned out to be dead ends.
- **2** — the user reached it directly from the clickables visible within a
  step or two of arriving on the relevant screen.

## 2. Feedback — did the interface confirm what happened?

- **0** — a consequential action (click, fill, submit) produced no visible
  change in the trail, leaving the user to guess whether it worked.
- **1** — feedback existed but was easy to miss (a subtle style change with
  no text, a message the visible text barely caught) or arrived late.
- **2** — every consequential action was followed by clear, immediate,
  readable confirmation in the visible text or page state.

## 3. Recovery — could the user undo or fix a mistake?

- **0** — a wrong click or fill left the user stuck with no visible way back
  (no cancel, no back control, no error message that explains the fix).
- **1** — recovery was possible but took extra, non-obvious steps.
- **2** — mistakes were clearly reversible (undo, cancel, or a clear error
  message with a next step) with no extra steps.

## 4. Consistency — did the interface behave the way earlier steps implied?

- **0** — similar-looking controls behaved differently across steps, or
  terminology/placement changed without reason, breaking the user's model of
  the interface.
- **1** — mostly consistent, with one or two surprising deviations.
- **2** — controls, labels, and layout behaved the same way every time they
  reappeared in the trail.

## 5. Wording — was on-screen text clear and jargon-free?

- **0** — labels, buttons, or messages the user relied on were ambiguous,
  jargon-heavy, or contradicted what the user actually needed to do.
- **1** — wording was serviceable but required rereading or guessing intent
  at least once.
- **2** — every label and message a step touched was immediately
  understandable on first read.

## Output

Write exactly this JSON to `judge.json`, nothing else:

```json
{
  "scores": {
    "findability": 0,
    "feedback": 0,
    "recovery": 0,
    "consistency": 0,
    "wording": 0
  },
  "total": 0,
  "evidence": ["step 3: ..."]
}
```

- `total` is the sum of the five scores (0–10).
- `evidence` has at least one entry for every criterion scored below 2, each
  formatted `"step N: <one sentence>"`, where `N` is that action's `step`
  field in the trail.
- Score only what the trail shows. No score, no evidence sentence, and no
  guess about intent that the trail does not support.
