# The project execution system: plan for the finished thing

Revision 3, 2026-09-18. Revisions 1 and 2 were reviewed cold by context-free
reviewers (`docs/REVIEW-2026-09-18.md`, `docs/REVIEW-2026-09-18-r2.md`); this
revision answers every structural finding of both. It is written to be read by someone with
no prior context.

## 1. What the system is for

One person, with Claude Code, takes an idea to a shipped product that then
improves itself, with the human deciding at a small number of named points
and a script (never a model) making every keep-or-throw-away decision. The
system must never poison the projects it builds or the Claude configuration
it runs in: nothing installs itself, nothing touches `main`, every
third-party description is screened before it is stored, every unattended
run has a cost cap and a kill switch.

## 2. Principles

1. A script decides, a model proposes. Keep/undo, done/not done, ship/not,
   go/no-go are checks a program runs over files. Where a decision needs
   judgment, a model produces a structured answer and a script validates its
   shape and applies it; the model never computes the verdict.
2. Fresh eyes at every hand-off. The reviewer never saw the plan, the persona
   never saw the code, the judge never saw the case. Every stage writes a
   file; the next stage reads that file with a new context.
3. Nothing the loop scores can be edited by the thing being scored. Scorer
   code, thresholds and labelled corpora are pinned by a manifest and denied
   to executors, or live outside the repo.
4. The cheapest model that can do the job. Fable plans and reviews, Sonnet
   builds, Opus for the hard ones, Haiku for classification and chores.
5. Nothing installs itself, nothing touches `main`. Human gates: validation
   verdict, spec signed, every install, milestone merge, token approval,
   first screenshot baseline, deploy. Each gate is enforced by a script that
   refuses to proceed without the gate's artifact.
6. Money has a ceiling: cap, live meter, kill switch that reaches child
   processes.
7. If it cannot be measured it becomes a question for the human, not a guess.
8. A defect found in a real run becomes a test before its fix merges.
9. Transcripts are never quoted into the repo. A fact from a transcript is a
   count.

## 3. What exists (with the evidence, corrected)

| Part | Where | Proven on |
|---|---|---|
| ARD (`rdx`): resource discovery index, five funnels (4 marketplaces, MCP registry, GitHub, npm), sanitizer, silent gate, install tiers | `discovery/` | 36,178 rows; gate 21 true fires, 7 misses, 95 true silences on a 124-case labelled corpus (precision 1.0, recall 0.75); 353 unit tests; poison test 22 malicious rows stored, none reachable. Indexes tools, not data sources. |
| Nightshift: the unattended improvement loop | `loop/` | Kept improvements: rdx 85.1→94.9, prettytable 97.8→99.7, yaml 96.8→97.2. On Pocket Notes the one iteration was reset as flat (+0.15, below the 0.5 floor): the loop ran and did no harm; it did not improve the product. Cost meter vs bill: within 5% on Pocket Notes, 8–32% on this repo across three runs; the dry-run tolerance is −10%/+35%. |
| Pipeline: interview → tools → build → look & feel → gates → ship → feedback on one spine, `spec.json` | `pipeline/` | Pocket Notes (Vite app, no backend): interview $1.48, three milestones $12.22, four persona walkthroughs (judge 8–10/10), ship report 7/8 rows green, feedback 4 rows; total $18.45 |
| Commands, agents | `commands/`, `agents/` | used above |

Contracts: `pipeline/README.md`, `loop/README.md`, `discovery/README.md`.

Known weaknesses: one real project, one stack; scorers measure what the
builders were told to hit; persona findings are noisy; macOS unproven; the
Opus planner is most of the cost; lexical retrieval misses synonyms; GitHub
search is unreachable from cloud sessions (the npm funnel covers OSS there);
no headless child has ever fetched a web page (the inspiration stage
reported no web access); this repo's own Nightshift scores one of three test
suites and can edit its own ARD judge (see 4.0).

## 4. What is left, in build order

### 4.0 Lock the judges (first, half a day)

- `loop/score.py --manifest-write` also pins every file a scorer's `cmd`
  imports and every corpus it reads: for this repo `discovery/rdx/loopscore.py`,
  `evalharness.py`, `retrieve.py`, `config.py`, `discovery/corpora/*.yaml`.
  These pins are **repo-relative and verified at `<workdir>/<path>`** at
  score time, i.e. in the loop worktree the executors and merges touch, not
  in the kit checkout. That hash check is the defence that does not depend
  on the planner's honesty; the deny rule and the hook below are extra.
  A mismatch stops the run, as today.
- `loop/check_plan.py` denies any `owned_paths` entry matching a pinned
  file, and `hooks/safety` trips on an edit to one.
- This repo's Nightshift config: `main_branch: main`, three tests scorers
  (`discovery`, `loop/tests`, `pipeline/tests`), re-baselined.
- `loop/init.py` and `pipeline/mkconfig.py` pin the same set for every
  project that names an in-repo scorer.

### 4.1 Fetch smoke test (one hour, before anything else in validation)

Decided now: the fetcher is a script, `pipeline/fetch.py` (URL → extracted
value or text, `extracted_by`, body hash, date), never `WebFetch`; only a
script gives the referee something to re-run. `fetch.py --probe` runs a
fixed host list (registries, GitHub API, Reddit, Hacker News, Product Hunt,
a search endpoint, two forums) from a `claude -p` child through
`pipeline/child.sh` and writes `.pipeline/validate/reachable.json`. Run
from the cloud and from the Mac. Egress here is per host: measured today
the cloud reaches registries and the GitHub API and refuses forums, Hacker
News, Product Hunt and search engines at the proxy. The reachable set, not
a pass/fail, is the output, and the referee reads it (4.2, step 7).

### 4.2 Stage 0: validate (`/jg-validate "<idea or URL>"`)

Purpose: before the interview, establish with evidence whether the idea is
worth building, to a tier that scales with the build's cost. It replaces
`/jg-feasibility`; interview round 4 reads `verdict.json` instead of asking
its own feasibility question.

Invocation: `/jg-validate --build-usd N "<idea or URL>"`. The budget is
named up front because the spec does not exist yet. The slug is the first
8 hex of sha256(idea text). `verdict.json` stores the idea text and
`validated_budget_usd`. `spec_check.py` gains the gate: exit 3 when no
verdict exists or the verdict is NO-GO without `overruled` (unless
`--no-validate` is passed and logged), and exit 2 when
`budget.build_usd > validated_budget_usd` (validate again at the higher
tier). Today `spec_check.py` reads neither; this is new code.

Roles, each a separate context, each writing a file the next reads:

1. **Author** (Fable): from the idea, writes `claims.json`: 3–6 claims, one
   marked `core: true` (the single assumption the project depends on, as
   CLAUDE.md requires). Each claim: who, pain, statement, and the metric
   that would settle it. No numbers yet.
2. **Setter** (Fable, fresh, sees only `claims.json` and the build budget):
   writes `plan.json`: per claim one or more measures (name, unit,
   direction), the source kind and where for each, and a kill number per
   measure. Never sees the author's reasoning.
3. **Skeptic** (Fable, fresh, sees `claims.json` plus the setter's measure
   names, units and directions with the numbers redacted by the driver):
   writes `skeptic.json`: per claim its own kill number on each of the
   setter's measures, any measures it adds (with numbers and sources), and
   two sources that would disconfirm the claim, plus `skeptic.md`, the case
   against.
4. **Driver freeze** (`validate.py freeze`): merges on the shared measure
   set. Per measure the stricter kill number wins; a kill number whose
   measure is not in the claim's measure set is rejected; the skeptic's
   added measures and disconfirming sources become required ledger rows.
   Writes `plan.frozen.json` with its sha and `frozen_at`. From here the
   plan is read-only to every child.
5. **Fetcher** (Sonnet, fresh, read-only plan): for every planned source,
   calls `pipeline/fetch.py` and appends a ledger row: claim, measure,
   source `{kind, url|cmd, extracted_by, body_hash}`, value, unit, date,
   author or origin (for independence), note. It may add sources it finds;
   it may not remove planned ones. A source it cannot reach becomes a row
   with `value: null` and `reason: egress | 404 | paywall | timeout`. It
   never writes an estimate.
6. **Judge** (Sonnet, fresh, sees plan + ledger, fixed rubric): one question
   per ledger row: does the source say what the row claims, 0/1/2, with the
   quoted line. Nothing else.
7. **Referee** (`validate.py verdict`, script): re-runs every re-runnable
   source through `fetch.py` and compares the **extracted value**, not the
   body: drift is when the value is absent on re-fetch or has crossed the
   kill number; live counters that moved but still clear the number are
   fine, and both hashes are stored for the record. Assigns tiers; drops
   rows the judge scored 0; counts tier-1 rows only when their origins are
   distinct; computes per claim supported / killed / below tier /
   unobtainable against the frozen numbers; applies the tier table. If a
   required (skeptic) source is unobtainable for a network reason and
   `reachable.json` confirms the host is blocked here, the verdict is
   infra (exit 4, "run this on the Mac"), never NO-GO. Writes
   `verdict.json` and `VERDICT.md` with every source linked.
8. **Human**: reads `VERDICT.md`. A NO-GO may be overruled in writing; the
   overrule is stored as a claim with its own kill number and
   `/jg-feedback` re-checks it after ship.

Evidence tiers (four, as the reviewer proposed):

| tier | what counts | rule |
|---|---|---|
| 0 | nothing, unobtainable, unsourced, drifted, judge-scored 0 | never counts |
| 1 | anecdotal: strangers describing the pain with links, a named person's message | three independent tier-1 rows make one tier-1 point |
| 2 | primary data: query + result + date, re-runnable | one row is a point |
| 3 | observed use: a prototype used more than once, payment, a usage log | one row is a point |

Provisional tier table, by the budget named at invocation (the human can
change it; the referee reads it from `pipeline/validate.toml`). The core
claim is never satisfied by tier 1: anecdotes have no value a kill number
can test, and any real pain has three forum posts. Tier 2 costs one query.

| build budget | required | roles that run |
|---|---|---|
| under $20 | core at tier 2 (one re-runnable row clearing its kill number) | author → fetcher → referee; the author sets the one measure and number, the referee applies it |
| $20–100 | core at tier 2, every other claim at tier 1 (three independent rows) | all eight |
| over $100 | every claim at tier 2, plus one tier-3 row from an experiment (a landing page, a concierge run, a personal usage log) before milestone 2 | all eight |

Verdict rules: core claim killed or below its required tier → NO-GO (exit
2). Any non-core claim killed or below its required tier → PIVOT (exit 5,
returns to the author with the ledger; a second PIVOT on the same idea is
NO-GO). All required tiers met and nothing killed → GO (exit 0).
Needs-human (an unmatched measure at freeze, an overrule pending) → exit
3, as everywhere in the pipeline. Infra (a required source blocked here,
the referee cannot re-fetch) → exit 4. The verdict is arithmetic; the prose
is written afterwards from it.

Files: `.pipeline/validate/<slug>/{claims.json, plan.json, skeptic.json,
skeptic.md, plan.frozen.json, ledger.jsonl, judge.json, verdict.json,
VERDICT.md}`. GO carries `anchors` (the claims, verbatim) and the kill
numbers into `spec.json`; NO-GO appends to `DEAD_ENDS.md`.

Budget: `validate.usd` in `pipeline/validate.toml`, default $5 for the
eight-role path (measured estimate $3–4: three Fable contexts at $0.2–0.5,
the fetcher $1–2, the judge under $1) and $1.5 for the under-$20 path,
metered like every other child. ARD is not part of this stage: it indexes
tools, not data sources. A data-source funnel is a possible later project.

Tests: `validate.py` against a fake fetcher and hand-written ledgers: the
stricter-number merge, the disconfirming-source requirement, freeze
immutability (a child that edits the frozen plan is caught by sha), drift,
the tier table, every verdict rule, the spec_check gate, the DEAD_ENDS
write. Then one real run on an idea of the human's.

### 4.3 Judgment where rules are doing judgment's job (Haiku behind a script)

- `feedback.py`: dimension by a Haiku call returning one of the allowed
  dimensions, validated by the script; duplicates by a Haiku "same
  complaint?" call over normalized titles, validated to an id list. Keyword
  mapping stays as the fallback when the model is unavailable.
- Persona findings deduplicated and a clean walkthrough's "resolved"
  decision made by a Haiku call over the finding and the trail, validated
  to row ids, **in `pipeline/persona_run.py`**, which is driver-side and
  metered through the ledger. `ux_score.py` stays pure: it runs inside
  `score.py`, outside the cap, the live meter and the kill switch, and
  nothing below `score.py` may call a model. The hash rule stays as the
  fallback.
- Not changed: the ARD intent gate (regex, 3 ms, precision 1.0), keep/undo,
  done/not, go/no-go, screenshot gates.

### 4.4 The retro (a report and a corpus, not proposals)

- Instrument first: `build.sh` records `attempts` per milestone; merge, ship
  and validate drivers log `human_override` events; `run.sh` already logs
  flat nights and stop reasons.
- `retro/collect.py` (deterministic, schema in `retro/facts.schema.json`):
  first-try acceptance rate, cost per accepted milestone, flat nights, gate
  false fires and misses, overrides, time to green, validation verdicts
  later contradicted by feedback. Transcript-derived facts are counts only.
- `retro/report.py`: trends per version label, as CLAUDE.md's benchmarking
  rule asks. Cost per accepted milestone lives here, never in a scorer.
- Each fact that names a mistake becomes a labelled regression case in
  `retro/corpus/`: a plan `check_plan` should have rejected, a stale persona
  finding, a prompt the gate should have fired on, a verdict feedback
  overturned. A `kit-eval` scorer runs the fixture suites end to end plus a
  replay of recorded stream logs through `tail.py` asserting cost agreement,
  plus the pass rate on that corpus. That is what Nightshift climbs on this
  repo. The corpus is pinned (4.0). Recorded streams enter the corpus only
  through `retro/redact.py`, which keeps event types, usage, costs and hook
  events and drops every text field; a test asserts no text survives
  (principle 9).
- Kit edits that change a pinned scorer file mark every project's manifest
  stale; `run.sh` says so at start and `score.py --manifest-write` re-signs.

### 4.5 The ponytail ladder in executors

Fold the six lower rungs of the ponytail ruleset into `agents/exec-sonnet.md`
and `agents/exec-opus.md` (rung one, "should this exist", omitted:
executors do not decide scope). Measured by the reviewer's `scope_ok` rate
and diff size per accepted subtask across the next real project, reported by
the retro; not a scorer.

### 4.6 Housekeeping

- Retire `commands/jg-feasibility.md` (points at `/jg-validate`); rename the
  strategic review to `/jg-review-approach` so `skeptic` means one thing.
- Annotate the `DEAD_ENDS.md` entry on custom subagents as superseded, with
  the reason (agents became the unit every stage dispatches).
- No AgentShield row until ECC is installed on the Mac.

### 4.7 On the Mac (human-run)

First live GitHub funnel sync and `rdx schedule`; a Nightshift dry run with
ECC enabled to check hook coexistence; Remote Control on the primary local
session.

### 4.8 Second real project

A stack with a backend, a database and an LLM, through every stage including
validation. Most remaining unknowns are there.

## 5. Open questions for the human

The tier table's numbers; the overrule policy (kept as "in writing, stored,
re-checked"); paid sources (proposed: never without a per-source approval and
a cost line); the validation budget default; whether the Mac is primary.
