# rdx — agentic resource discovery

A local index of MCP servers, Claude Code plugins and skills, plus a
deterministic `UserPromptSubmit` hook that surfaces a relevant one **at the
moment you need it** — and stays silent the rest of the time.

## Status: Phase 1a complete, shadow mode

Built and tested. Not yet calibrated, so it currently injects nothing.

| Component | State |
|---|---|
| Schema + FTS5 index | done |
| Sanitizer + adversarial corpus | done, 36/36 |
| Ingest: 4 marketplaces + MCP registry + local scan | done |
| Retrieval + 9-condition gate | done |
| Eval harness (gate / discovery / safety / poison) | done |
| `UserPromptSubmit` hook + statusline + CLI | done |
| Threshold calibration | **not done — needs your machine** |
| Install runner (Phase 1b) | not built |
| GitHub / HN / Bluesky funnels (Phase 2) | not built |

Measured on a real index of 3,947 resources: retrieval **2ms**, silent-path
hook **~2.8ms**, safety corpus 36/36, discovery 9/9 (3 skipped pending the
GitHub funnel), poison test 22 malicious rows stored and none reachable.

## Why this exists

Claude will not go looking for tools. Skills are model-discretionary — there is
no deterministic trigger — which is exactly why a GitHub-search skill sits
unused. `UserPromptSubmit` runs on **every** prompt with no model discretion, so
the index comes to Claude instead of Claude going to the index.

Anthropic's first-party `SearchMcpRegistry` / `SearchPlugins` cover the
connector and commercial-plugin catalogue and are also discretionary. rdx calls
that ecosystem as one funnel; it does not rebuild it.

## Quick start

```bash
../install.sh --discovery     # venv, index, hook, statusline
rdx sync                      # build the index (~3 min full crawl)
rdx scan                      # exclude what you already have installed
rdx eval --all                # safety + discovery + poison
rdx search "is there an mcp for linear"   # see the exact would-be envelope
```

Then calibrate (below) before going live.

## Calibration — the speed-run

Thresholds start at `+inf`, so a fresh install is silent by construction. They
are set from **your own prompts**, not guesses:

```bash
rdx mine                      # extracts real prompts from ~/.claude/projects
$EDITOR corpora/gate.yaml     # correct the pre-labels: inject / silent
rdx eval --gate               # precision, recall, suppression histogram
```

Aim for **high precision, deliberately low recall**. A false positive on a
trivial prompt is what makes you turn the thing off, after which recall is zero
forever. `rdx stats` prints a `top_score` percentile table; start near p75–p90.

```bash
export RDX_MIN_SCORE=0.45 RDX_MIN_MARGIN=0.12 RDX_SHADOW=0
```

## Safety

Everything the sanitizer passes is injected into Claude's context on every
prompt, so it is the load-bearing component:

1. **Field whitelist** — README bodies and unknown keys are discarded uninspected.
2. **Normalize + strip** — NFKC, then delete C0/C1 controls, zero-width, bidi
   overrides, and `U+E0000–E007F` tag characters (invisible in every editor,
   legible to a model).
3. **Screen** — five blocking families (instruction override, role forgery,
   tool-call forgery, exfiltration, self-approval) → quarantine.
4. **Strip markup**, 5. **cap lengths**, 6. **assert the envelope invariant**:

```python
ENVELOPE_SAFE = re.compile(r"\A[^\x00-\x1f\x7f<>{}\[\]`$|\\]*\Z")
```

Content provably cannot contain `<`, `>` or `|`, and the envelope's delimiters
and columns are exactly those characters — so content cannot close the block or
forge a column. Every other defense is depth; this one is the proof. Verified
over 10,000 random byte strings and by an end-to-end poison test.

Advisory flags (`needs_secrets`, `truncated`) are **not** blocking — an early
version conflated "documents an API key" with "exfiltrates secrets" and
quarantined the first-party `context7` plugin. Needing a credential forces the
red install tier; it does not hide the resource.

Two hard rules that never bend:

- The hook's only output is text. **It can never trigger an install.**
- MCP installs default to `-s local`. Project scope requires an explicit flag,
  because a committed `.mcp.json` loads *without a trust prompt* in
  non-interactive sessions.

## Commands

| | |
|---|---|
| `rdx sync [--funnel F] [--limit N]` | fetch; `--limit` is a bounded dry run |
| `rdx scan` | record installed resources so they are never suggested |
| `rdx search "<prompt>"` | scores, gate decision, and the exact envelope |
| `rdx audit [--quarantined]` | browse the index / review quarantine |
| `rdx mine` | build the gate corpus from your transcripts |
| `rdx eval [--gate\|--discovery\|--safety]` | the harness; non-zero exit on failure |
| `rdx stats` | suppression histogram, latency, accept rate |

Kill switches, in order of bluntness: `touch ~/.claude/rdx/DISABLED`,
`RDX_DISABLE=1`, a `.rdx-off` file in a project, or
`../install.sh --discovery-uninstall`.

## Key decisions

- **State lives in `~/.claude/rdx/`, not the repo.** A WAL-mode SQLite index in
  a working tree means `git status` noise and accidental commits.
- **Zero runtime dependencies.** stdlib only; `pytest`/`PyYAML` are dev-only.
  The hook runs on every prompt and should carry no supply-chain surface.
- **Two processes.** A bash shim does the free checks (~2.8ms) and only execs
  Python when a suggestion is plausible, avoiding a ~40-60ms interpreter start
  on the >95% of prompts that are silent.
- **The gate is silent by default**, and `evaluate()` logs a named suppression
  reason for every prompt so thresholds are tuned from a histogram, not a guess.
- **Nothing is ever deleted.** Deprecated and quarantined rows persist for audit.
- **Raw prompts are never stored** — `injection.prompt_sha` is a digest.

## Known limitations

- **Lexical retrieval misses synonyms.** `serena` ("semantic code *analysis*")
  loses to tools whose text says "search". This is the cost of the BM25-only
  decision and the first real argument for local embeddings later.
- **No popularity signal yet.** Phase 1 funnels carry no stars or push dates, so
  `quality_score` is nearly constant and curation (funnel priority) does the
  ranking. The GitHub funnel fixes this.
- **The gate corpus is empty until you run `rdx mine`** on a machine with real
  transcript history.
