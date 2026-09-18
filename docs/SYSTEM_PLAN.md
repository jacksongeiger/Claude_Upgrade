# The project execution system: plan for the finished thing

Status: 2026-09-18. Eight of nine stages exist and were run end to end on a
real project with real spend. This document is the plan for what is left and
the contract the remaining parts are built against. It is written to be read
by someone with no prior context.

## 1. What the system is for

One person, with Claude Code, takes an idea to a shipped product that then
improves itself, with the human deciding at a small number of named points
and a script (never a model) making every keep-or-throw-away decision. The
system must never poison the projects it builds or the Claude configuration
it runs in: nothing installs itself, nothing touches `main`, every
third-party description is screened before it is stored, every unattended
run has a cost cap and a kill switch.

## 2. Principles (non-negotiable, inherited from what is built)

1. A script decides, a model proposes. Keep/undo, done/not done, ship/not,
   and now go/no-go are checks a program runs over files.
2. Fresh eyes at every hand-off. The reviewer never saw the plan, the persona
   never saw the code, the judge never saw the pitch. Every stage writes a
   file; the next stage reads that file with a new context.
3. The cheapest model that can do the job. Fable plans and reviews, Sonnet
   builds, Opus for the hard ones, Haiku for chores.
4. Nothing installs itself, nothing touches `main`. Human gates: spec signed,
   every install, milestone merge, token approval, first screenshot baseline,
   deploy, and (new) the validation verdict.
5. Money has a ceiling: cap, live meter, kill switch that reaches child
   processes.
6. If it cannot be measured it becomes a question for the human, not a guess.
7. A defect found in a real run becomes a test before its fix merges.

## 3. What exists (with the evidence)

| Part | Where | Proven on |
|---|---|---|
| ARD (`rdx`): resource discovery index, five funnels (4 marketplaces, MCP registry, GitHub, npm), sanitizer, silent gate, install tiers | `discovery/` | 36,178 rows; gate precision 1.0 / recall 0.75 on a 124-case labelled corpus; 353 unit tests; poison test 22 malicious rows stored, none reachable |
| Nightshift: the unattended improvement loop (pick, plan, build in worktrees, review, merge with tests floor, score, keep/undo) | `loop/` | rdx 85.1→94.9, prettytable 97.8→99.7, yaml 96.8→97.2, Pocket Notes 97.8→97.9; cost meter agrees with the bill within 5% |
| Pipeline: interview → tools → build → look & feel → gates → ship → feedback on one spine, `spec.json` | `pipeline/` | Pocket Notes (Vite app): interview $1.48, three milestones $12.22, four persona walkthroughs (judge 8–10/10), ship report 7/8 rows green, feedback 4 rows; total $18.45 |
| Commands | `commands/jg-*.md`, `commands/ard.md` | used above |
| Agents | `agents/` (exec-sonnet, exec-opus, reviewer, chore, test-assessor, persona, persona-judge) | used above |

Contracts: `pipeline/README.md` (the pipeline), `loop/README.md`
(Nightshift), `discovery/README.md` (ARD). Changelog: `CHANGELOG.md`.

Known weaknesses, stated by the builder:

- One real project, one stack (Vite, no backend, no auth, no database, no
  LLM). The `db`, `auth`, `llm`, `evals` and `deploy` paths have only run
  with fake commands in the synthetic test.
- Scores are high because the scorers measure what the builders were told to
  hit; Nightshift has little headroom on a project the system built.
- Persona findings are noisy; stale rows are now closed by a clean
  walkthrough, but that rule has not seen real data.
- Everything ran in a Linux container. macOS (Chromium path, `claude`
  binary permission prompts, launchd, osascript) is unproven.
- Cost: $18 for a toy app. The Opus planner is most of it.
- Lexical retrieval in ARD misses synonyms; local embeddings are the known
  fix, not built.
- GitHub search is unreachable from cloud sessions (proxy scoped to one
  repo). The npm funnel covers OSS from anywhere; the GitHub funnel needs the
  Mac and has never run live.

## 4. What is left to build

### 4.1 Stage 0: validate (`/jg-validate "<idea or URL>"`)

Purpose: before the interview, establish with evidence whether the idea is
worth building, at a conviction tier that scales with the build's cost.

Roles:

- The model judges. It writes the claims, decides what evidence would settle
  each, where to get it, the number that kills it, and, after fetching, what
  the evidence means. Evidence sources vary per project (a crypto service
  plans on-chain activity and exchange volume; a CLI tool plans issue threads
  and download curves; a personal tool plans a two-week usage log) and are
  chosen at run time.
- A script referees. It never reads the idea. It checks: the evidence plan
  with kill numbers was frozen before the first fetch (a plan edited after
  evidence is flagged); every record has a re-runnable source (URL, API call,
  command) or is tier 0; the verdict is consistent with the model's own
  pre-written thresholds (a GO over a killed claim is a flagged
  contradiction); the reported conviction tier is the one the ledger
  supports.
- A fresh-context judge (fixed rubric, reads only plan + ledger) answers the
  two qualitative questions: does each cited source really describe the
  claimed pain, and does the case against outweigh the case for.
- A skeptic (fresh context) writes the strongest case against before the
  plan is frozen.
- ARD finds evidence sources the model cannot reach with a browser (a chain
  explorer, an exchange API, a registry); the human approves any install.
- The human reads the verdict with every source linked and decides. A no-go
  can be overruled in writing; the reason is stored with the verdict.

Files:

```
.pipeline/validate/<slug>/
  claims.json        3–6 claims: id, who, pain, statement, kill_metric, kill_value, direction
  plan.json          per claim: measure, source (kind, where), kill number; frozen_at, sha
  skeptic.md         case against, written before plan freeze
  ledger.jsonl       {claim, measured, source:{kind,url|cmd}, value, unit, date, tier, note}
  judge.json         rubric scores + evidence
  verdict.json       {verdict: GO|PIVOT|NO-GO, tier, contradictions:[], overruled:{by,reason}?}
  VERDICT.md         readable, every source linked
```

Evidence tiers: 5 paid or retained use · 4 used a prototype more than once ·
3 a named person said yes · 2 primary data with the query · 1 strangers
describing the pain, with links · 0 estimates, unsourced numbers, the
model's belief. "Unobtainable" is a valid ledger entry; the stage never
fills a gap with an estimate.

Outputs: GO carries concept anchors and the kill numbers into `spec.json`
(`anchors`, `success` lines) and later `/jg-feedback` checks them against
real use; NO-GO appends to `DEAD_ENDS.md`; PIVOT returns to claims with the
reason.

Exit codes: 0 go · 2 no-go · 3 pivot / needs-human · 4 infra · 1 usage.

Budget: a cap per validation (number TBD by the human), metered like every
other child.

Open questions for the human: conviction threshold per build size;
overruling policy; interviewing the human for tier 3–5 evidence first; paid
sources; budget; whether the Mac is the primary machine.

### 4.2 The retro loop (the system improving itself)

- `retro/collect.py` (deterministic): from ledgers, events logs,
  `scores.jsonl`, acceptance records, ship reports, gate stats
  (`rdx stats`), persona findings, DEAD_ENDS and transcripts (`rdx mine`
  already parses them) → `retro/facts.json`: first-try acceptance rate, cost
  per accepted milestone, nights ended flat, gate false fires, human
  overrides, time to green.
- `retro/propose` (fresh model): each proposal cites one fact; lands as a
  backlog row on this repo with `source: retro`.
- Nightshift runs on this repo against its scorers: the three test suites,
  `rdx eval` (gate precision, discovery, safety), the pipeline integration
  test, and a new scorer, cost per accepted milestone across built projects.
- Human merges. "Fine-tuning" means thresholds, prompts and rules against
  labelled corpora, never model weights.

### 4.3 The ponytail ladder in executors

Fold the six lower rungs of the ponytail ruleset (reuse what exists, stdlib
before custom, native before dependency, installed dependency before new, one
line before fifty, minimum that works; never cut validation/error handling)
into `agents/exec-sonnet.md` and `agents/exec-opus.md`. Rung one ("should
this exist") is omitted: executors do not decide scope. Measure with the
retro's cost-per-accepted-milestone scorer.

### 4.4 Small additions

- Ship checklist: an AgentShield row (scans hooks, MCP config, permissions,
  secrets) when ECC is installed; skipped cleanly otherwise.
- `spec_check`: accept `anchors` and validation-derived success lines.
- `feedback.py`: check inbox items against the kill numbers and report.

### 4.5 On the Mac (human-run)

- First live GitHub funnel sync; `rdx schedule` for the nightly refresh.
- A Nightshift dry run with ECC enabled, to check hook coexistence.
- Remote Control on the primary local session; cloud sessions for
  closed-laptop work and throwaway sandboxes.

## 5. Build order

1. Validation contract page in `pipeline/README.md`; ponytail ladder in the
   executor prompts (half a day together).
2. `pipeline/validate.py` (referee + verdict + handoffs) with tests using a
   fake fetcher; `prompts/validate.md`; `agents/skeptic.md`,
   `agents/evidence-judge.md`; `commands/jg-validate.md`.
3. One real validation run on an idea of the human's, with real fetching.
4. Retro collector and proposer; one retro run on the data this system has
   already produced.
5. Second real project on a stack with a backend, a database and an LLM.
