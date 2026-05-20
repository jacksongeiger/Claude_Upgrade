You are writing a CHANGELOG.md entry. The goal is that someone reading this in 6 months can understand exactly what changed, why, and how it performed — without reading any code.

## Every entry must include:
- Timestamp (date and time)
- Version label (v1.0, v1.1, etc — increment from the last entry)
- What changed: specific, not vague. Not "improved performance" — "reduced API calls from 3 to 1 per cycle"
- Why it changed: what problem was being solved or what was being tested
- Result: did it work? If performance is tracked, include the benchmark score and stamp it with version and date
- Any breaking changes or things that no longer work the way they did before
- Any dependencies added or removed

## Format:
---
### v[X.X] — [DATE TIME]
**What changed:** ...
**Why:** ...
**Result:** ...
**Performance:** [score/metric] @ v[X.X] [DATE] or N/A
**Breaking changes:** ... or None
**Dependencies:** ... or None
---

## Rules:
- Never be vague. Specific details only.
- If performance benchmarking exists in the project, always include the latest benchmark result with version and timestamp so versions can be compared objectively.
- If this is the first entry, create the CHANGELOG.md file and add a v1.0 entry.
- Always prepend new entries at the top of the file so the latest version is always first.
