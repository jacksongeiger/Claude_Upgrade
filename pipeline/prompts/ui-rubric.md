# UI craft rubric

Fixed criteria for `agents/ui-judge.md`: the visual counterpart of
`rubric.md`, which grades behaviour. The judge sees screenshots, the
project's direction and a census of the page's elements. It never sees the
measured findings first: a detector's output anchors judgment, so the craft
read comes before the numbers (impeccable's rule, adopted). Five
dimensions, 0–3 each. Never invent a sixth; never output a 0–100 score —
human raters disagree by two points out of ten on the same screen (UICrit),
so a finer number would be false precision.

## The five dimensions

**1. Hierarchy — is there one place to look first?**
- 0 — no focal point; everything the same size, weight and colour.
- 1 — a focal point exists but competes (two primaries, a louder secondary).
- 2 — first, second and third reads are clear; size alone carries it.
- 3 — size, weight and colour work together; the order is obvious without reading.

**2. Rhythm — does the spacing say what belongs together?**
- 0 — spacing is uniform or random; groups can't be seen.
- 1 — groups exist but gaps inside and between them are close.
- 2 — tight within groups, generous between; headings sit with what follows.
- 3 — the spacing alone explains the structure, at every width shown.

**3. Alignment — do things share edges?**
- 0 — ragged left edges, off-centre rows, controls that don't line up.
- 1 — mostly aligned with a few stray edges or baselines.
- 2 — shared edges and baselines; rows vertically centred.
- 3 — a visible grid; nested radii concentric; nothing a pixel off.

**4. Restraint — does every element earn its place?**
- 0 — decoration everywhere: gradients, glows, badges, several accents.
- 1 — one or two decorative choices that add nothing.
- 2 — one accent used sparingly, one depth strategy, little ornament.
- 3 — nothing to remove; the accent marks only what matters.

**5. Fit — does it look like this product's recorded direction?**
- 0 — contradicts the direction (a toy look for a dense tool, or the reverse).
- 1 — generic: an unrelated product could ship it unchanged.
- 2 — follows the direction's density, type and colour.
- 3 — specific: it could only be this product, in this direction.

Without a recorded direction, grade fit against the product's job as the
census and screenshots show it, and say so in `not_reviewed`.

## Findings

At most 7. Each one:

- names one dimension and one severity: `major` (costs the user or reads
  as generated), `minor`, or `nit`;
- points at a real element: `sel` must be copied from the census for that
  screenshot, with its `route` and `viewport` — a finding that points at
  nothing is dropped by the script;
- says what is wrong and the fix in one line each, as a problem, not a
  redesign.

**Taste is not a defect.** A deliberately dense trading screen is not
failing for being dense; a bold choice working as the direction intends is
not a finding. If you cannot say why a finding costs the user or reads as
generated, cut it. Hover, focus, motion and empty/error states are not
judged from screenshots — the measured check owns them; list them under
`not_reviewed`.

## Output

Write exactly this JSON to the `out` path, nothing else:

```json
{
  "scores": {"hierarchy": 0, "rhythm": 0, "alignment": 0, "restraint": 0, "fit": 0},
  "findings": [
    {"dimension": "hierarchy", "severity": "major", "sel": "main > div.bar > button.btn",
     "route": "/", "viewport": "1440x900",
     "title": "Three buttons share the primary style; the eye has no first stop",
     "fix": "Keep one primary (New invoice); make Filter and Export secondary"}
  ],
  "not_reviewed": ["hover and focus states", "motion"]
}
```
