---
description: Retired. Idea validation is /jg-validate; this command only points there.
---

`/jg-feasibility` is retired. It asked "can this be built" and answered from
the model's own reading; the validation stage asks "should this exist" and
answers from evidence with sources, kill numbers written before the evidence
is read, and a verdict a script computes.

Run instead:

    /jg-validate --build-usd <N> "<idea or URL>"

The contract is `pipeline/README.md`, "Stage 0 — /jg-validate". A NO-GO still
lands in `DEAD_ENDS.md`; a GO carries its claims and kill numbers into
`spec.json`, where `/jg-spec` refuses to sign without them.
