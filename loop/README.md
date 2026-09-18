# Nightshift — contracts

This file is the source of truth for every script in `loop/`, every agent in
`agents/`, and `commands/jg-loop.md`. If code and this file disagree, the code
is wrong.

The one idea: **a script decides, never a model.** Fable plans and reviews;
Sonnet/Opus build; deterministic scorers measure; arithmetic decides whether the
product got better. Fresh context every iteration. Nothing touches `main`.

Runtime: `python3` (3.9+, stdlib only — no venv, no pip), `bash`, `git`, `jq`.

## Directory layout

```
~/Claude_Upgrade/loop/            the kit (this dir) — outside every project worktree
  run.sh                          the driver. bash. spawns one fresh `claude -p` per iteration
  init.py                         bootstrap: assess → config → agents → settings → baseline
  assess.py                       stack / tests / bench / evals / UI detection → JSON
  pick.py                         objective-function task picker → target.json
  check_plan.py                   plan validator: row ids, disjoint files, import-disjoint, acceptance
  score.py                        runs enabled scorers, verifies manifest, appends scores.jsonl
  merge.sh                        merges approved task branches into the loop branch with the tests floor
  tail.py                         reduces the child's stream-json: live spend, phase, agents, stalls
  map.py                          import graph → map.json + ARCHITECTURE.md + tree text
  report.py                       plain-language digest → report.md + report.html
  statusline.sh                   the 3-line statusLine renderer
  pricing.json                    per-model $/Mtok, used only as the live estimate
  scorers/{tests,perf,evals,lighthouse,cmd}.py
  scorers/kit_eval.py             this repo's own scorer: shell suites + retro corpus + stream replay
  hooks/{guard.sh,require-report.sh,events.sh,budget-gate.sh}
  prompts/iterate.md              the planner prompt (rendered by run.sh)
  tests/                          pytest for every script

~/Claude_Upgrade/agents/          subagent definitions copied into <project>/.claude/agents/ by init
~/Claude_Upgrade/commands/jg-loop.md
~/Claude_Upgrade/retro/           collect.py, report.py, redact.py, corpus_run.py, corpus/ — the kit's own retro (retro/README.md)

~/.claude/nightshift/<slug>/      per-project config — OUTSIDE the worktree, executors cannot reach it
  config.json
  goal.md
  manifest.sha256                 sha256 of config.json + every scorer file + every pinned in-repo judge, verified in the worktree before every score

<project>/.loop/                  runtime state
  backlog.yaml                    committed; the one human-editable steering surface
  state.json                      current run state (gitignored)
  scores.jsonl                    one row per scored iteration (committed on the loop branch)
  events.jsonl / events.log       machine / human event streams (gitignored)
  heartbeat                       touched by run.sh at every step (gitignored)
  questions.md                    what the loop needs a human for
  iterations/N/                   plan.json, target.json, tasks/<id>/report.json, summary.md
  run/                            loop.pid, loop.log, stream-N.jsonl, STOP  (gitignored)
  wt/                             worktrees: loop/ and <task-id>/            (gitignored)
  map.json, ARCHITECTURE.md
```

`<slug>` = `<basename>-<first 8 hex of sha256(realpath)>`.

## Models

| Role | Model | Where |
|---|---|---|
| planner + triage | fable | the child `claude -p --model fable` itself |
| reviewer | fable, fresh subagent | `agents/reviewer.md` |
| executor | sonnet (floor) | `agents/exec-sonnet.md` |
| executor, hard | opus | `agents/exec-opus.md` |
| chores | haiku | `agents/chore.md` — run scorers, annotate map. Never writes product code |
| test assessor | sonnet | `agents/test-assessor.md` — init only |
| ui auditor | sonnet + chrome-devtools | `agents/ui-auditor.md` — UI projects only |

## config.json  (`~/.claude/nightshift/<slug>/config.json`)

```json
{
  "version": 1,
  "project_dir": "/abs/path",
  "slug": "name-1a2b3c4d",
  "main_branch": "main",
  "setup_cmd": "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
  "test_cmd": "cd discovery && ./venv/bin/python -m pytest -q",
  "cap_usd": 25.0,
  "per_iter_max_usd": 6.0,
  "min_iter_usd": 1.5,
  "hours": 6,
  "max_flat": 3,
  "max_fanout": 3,
  "max_files_per_iteration": 25,
  "hypothesis_max_turns": 30,
  "child_max_turns": 80,
  "child_timeout_min": 45,
  "opus_allowed": true,
  "ui": { "enabled": false, "serve_cmd": null, "urls": [], "port_base": 4100 },
  "scorers": [
    { "name": "tests", "weight": 0.5, "runs": 1, "eps": 0.5, "target": 95,
      "cmd": "...", "coverage_cmd": "...", "coverage_file": "..." },
    { "name": "perf",  "weight": 0.3, "runs": 3, "eps": 1.2, "target": 90, "bench_cmd": "..." }
  ],
  "regress_eps": 1.0
}
```

Rules enforced by `init.py` before writing: at least two enabled scorers, OR the
goal text names the single one (keyword match: test|coverage|perf|speed|latency|
eval|accuracy|lighthouse|a11y). Each scorer's `eps` = `max(floor, 2 × CV)` from
3 baseline runs; a scorer whose spread exceeds its eps is refused until `runs`
is raised.

A scorer entry may carry `"script": "cmd"` to run a generic scorer under a
dimension name of its own (`{"name":"rdx-eval","script":"cmd","cmd":"..."}`);
the file run is `scorers/<script>.py`, default `scorers/<name>.py`.

## Scorer contract

```
python3 loop/scorers/<name>.py --config '<json of that scorer entry>' --workdir <dir>
```
Prints exactly one JSON line and exits 0 (always — a failing scorer reports, never crashes):
```json
{"name":"tests","value":72.5,"ok":true,"error":null,"raw":{"passed":310,"failed":0,"coverage_pct":45.0}}
```
`value` is 0–100, higher is better. `ok:false` + `error` means "could not measure"
→ score.py sets composite `null` and the driver stops with `score infra broken`.
Never renormalise around a missing scorer.

`tests`: `value = 100 × pass_rate × (0.5 + 0.5 × coverage_pct/100)` when coverage
is available, else `100 × pass_rate`. `raw` must include `n_tests`, `passed`,
`failed`, `coverage_pct` (or null), and `failing` (list of test ids) — merge.sh
needs the id list for the hard floor.

## scores.jsonl row

```json
{"iter":7,"ts":"2026-09-16T03:12:00Z","commit":"abc123","composite":71.4,
 "dims":{"tests":{"value":72.5,"ok":true,"raw":{...}}},
 "cost_usd":2.31,"duration_s":412,"task_id":"bl-014","outcome":"kept|reset-flat|reset-regressed|baseline",
 "label":"loop-2026-09-16.7"}
```
`composite = Σ weight_i × value_i / Σ weight_i` over enabled scorers, `null` if any `ok:false`.
Row 0 is the baseline written by init (`outcome: baseline`, `iter: 0`).

## backlog.yaml

```yaml
rows:
  - id: bl-014
    title: "parse_header() in ingest.py has no tests"
    dimension: tests            # a scorer name, or "none" (never picked at rung 1)
    est: S                      # S | M | L
    source: coverage-gap        # coverage-gap | bench | lighthouse | eval | issue | todo | thread | hypothesis | rdx | human | planner
    status: open                # open | done | failed | frozen | needs-human | blocked
    rung: 1                     # minimum rung at which this row is eligible
    attempts: 0
    iter_added: 0
    note: ""
```
Rules: rows with `source: planner` get `rung: 2` and are eligible only when
`iter_added ≤ current_iter − 2`. Rows whose title matches
`refactor|rename|extract|clean ?up|abstract|move|reorganis` get `rung: 2`.
`attempts ≥ 2` → `status: frozen`. When a dimension reaches its target, its
open rows are closed with `note: "dimension at target"`. The planner child may
change only `status`, `attempts`, `note` on existing rows, and may append rows;
`check_plan.py --backlog-diff` rejects anything else.

## target.json  (written by pick.py)

```json
{"iter":7,"rung":1,"dimension":"tests","headroom":13.8,
 "task_ids":["bl-014"],"lockout":[],"mode":"task|harvest|hypothesize|stop",
 "reason":"tests has the largest weighted headroom (0.5 × 27.5)",
 "candidates_considered":[{"id":"bl-014","dimension":"tests","est":"S"}, ...]}
```
`mode: harvest` (rung 2) and `mode: hypothesize` (rung 3) tell the planner to
add rows, not build. Exit codes: 0 task chosen · 3 nothing selectable (driver
stops `needs-human`).

## plan.json  (written by the planner child)

```json
{"iter":7,"task_ids":["bl-014"],
 "subtasks":[{"id":"t-014a","row":"bl-014","goal":"...","acceptance_cmd":"cd discovery && ./venv/bin/python -m pytest tests/test_ingest.py -q",
              "owned_paths":["discovery/rdx/ingest.py","discovery/tests/test_ingest.py"],
              "hard":false,"model":"sonnet","worktree":"/abs/.loop/wt/t-014a","branch":"loop/2026-09-16/t-014a"}],
 "decisions":["..."]}
```
`check_plan.py` exits 2 (planner must fix) when: any subtask row ∉ target.task_ids;
`owned_paths` overlap between subtasks; any file in one subtask has an import
edge (either direction, from map.json) to a file in another; a subtask lacks
`acceptance_cmd`; more than `max_fanout` subtasks; any path under `.claude/`,
`.loop/`, `.git/`. `--verify <subtask-id>` after execution: `git diff --name-only
base..branch` must be ⊆ owned_paths, else exit 2.

## report.json  (written by the executor at `.loop/iterations/N/tasks/<id>/report.json`)

```json
{"id":"t-014a","status":"done|ambiguous|blocked|failed","commit":"sha","branch":"...",
 "files":["..."],"test_output_tail":"...","question":null,"decisions_made":[]}
```
`decisions_made` must be empty — an executor that made a design decision has
violated its contract; the reviewer rejects.

## state.json  (written by run.sh; the statusline reads only this + scores.jsonl)

```json
{"run":"loop/2026-09-16","iter":7,"phase":"PICK|PLAN|EXEC|REVIEW|MERGE|SCORE|DECIDE|CLOSE|STOPPED",
 "started":"...","deadline":"...","cap_usd":40,"spent_usd":18.4,"live_spend_usd":0.9,
 "score":71.4,"best":71.4,"delta":2.1,"flat":0,"rung":1,"task":"bl-014",
 "agents":[{"id":"...","type":"exec-sonnet","task":"t-014a","since":"..."}],
 "stop_reason":null,"denies":0,"step":"EXEC"}
```

## events.jsonl / events.log

One JSON object per line; the `.log` twin is one human line per event, and it is
what `Monitor` and `/jg-loop watch` grep. Event names: `iteration_start`,
`agent_start`, `agent_stop`, `deny`, `merged`, `scored`, `kept`, `reset`,
`ASK`, `REGRESSION`, `STOP`. `STOP` and `ASK` lines must appear in `.log` for
every stop path — including the crash trap.

## Stop conditions (all set `stop_reason`, append `STOP <reason>` to events.log, notify)

cap · regression · regression-drift · flat · needs-human · hours · manual ·
score-infra-broken · safety-trip · two-failed-iterations · permission-stall ·
crashed-at-<STEP>

## Cost accounting

Primary: the child's stream-json `result` event `total_cost_usd`. Live: tail.py
prices each assistant message id once (the stream re-emits a message per
content block) plus `system/thinking_tokens` deltas at the main model's output
rate, via pricing.json → `live_spend_usd` (approximate; labelled as such).
Calibrated by replay: `retro/corpus/streams/` holds redacted real streams and
`loop/scorers/kit_eval.py` replays them, so a price or meter change that drifts
from the bill shows up as a score drop. On kill/timeout/missing result the iteration
is charged `max(live_spend, per_iter_max_usd)`. `run.sh` refuses to start an
iteration when `spent + min_iter_usd > cap`, spawns the child with
`--max-budget-usd min(per_iter_max, cap − spent)`, and kills the child's process
group when `spent + live_spend ≥ cap`. The dryrun asserts the signed
`(live − result) / result` is within [−0.10, +0.35] (on 25 replayed real
streams the estimate landed in [−0.10, +0.33]).

## Permissions (what the child may run at all)

The child runs with `--permission-prompts none`, so anything not on the
allowlist is a deny for the planner and every executor alike. `allowlist.py`
is the single source: a fixed read-only set, the kit's own scripts, common
runners, and the first word of every segment of every configured command
(`setup_cmd`, `test_cmd`, each scorer's `cmd`/`coverage_cmd`), plus the
repo-root spelling of a `cd X && ./bin` runner. `run.sh` hands it to the child
via `--settings`; `init.py` writes the same rules into the project's
`.claude/settings.json`; `check_plan.py` refuses any `acceptance_cmd` an
executor could not run. No `git push`, no bare `git`, no `Bash(*)`. Commands
containing `$VAR`, `$(...)` or backticks are denied by Claude Code regardless
of the allowlist; the prompts say so and check_plan treats them as denied.

## Guards (subagent-scoped hooks on the executor definitions)

`hooks/guard.sh` (PreToolUse, matcher `Bash|Write|Edit|MultiEdit`):
- Bash deny, `safety`: `git push|git checkout/switch (main|master)|git branch -[fDd]|git merge|git rebase|git reset --hard|git worktree (add|remove|prune|move|lock|unlock)|gh pr merge|GIT_DIR=|GIT_WORK_TREE=`
- Bash deny, `scope`: `git worktree …|git -C …` (read-only wandering)
- Bash deny, `install`: `rdx install|npm i(nstall)?|pip install <pkg>|uv add|brew |cargo add|curl .*\| *(ba)?sh|sudo |rm -rf (/|~|\$HOME|\.\.)|claude plugin|claude mcp`, except the project's own pinned manifest (`pip install -r <file>`, `npm ci`, `uv sync`, `go mod download`, `cargo fetch`)
- Write/Edit deny: realpath(file_path) not under `$NIGHTSHIFT_WORKTREE`, or under
  `.claude/`, `.loop/`, `.git/` within it.
- Every deny appends `{"event":"deny","kind":...}` to events.jsonl. Kinds:
  `safety` (push, main/master, merge/rebase/reset --hard, worktree add/remove,
  GIT_DIR redirects, writes outside the worktree) → the driver stops with
  `safety-trip` after the child exits; `scope` (read-only wandering: `git -C`,
  `git worktree list`) and `install` → denied and logged, no trip. The trip
  looks only at events written during the current iteration.

**Kit upgrades and the manifest.** `manifest.sha256` pins `config.json`,
every scorer script, and every in-repo file a scorer's `pins` globs name
(`repo:<path>` lines). Changing a scorer in the kit (a bug fix included) makes
every project's manifest mismatch and the next run stop with
`score-infra-broken` — by design: a scorer change is a scoring change. After
pulling a kit update, re-sign each project once:
`python3 $KIT/score.py --manifest-write ~/.claude/nightshift/<slug>/manifest.sha256 --config ~/.claude/nightshift/<slug>/config.json`.

**Pins: the judge is not the executor's to edit.** A scorer that lives in the
repo (an eval harness, its thresholds, a labelled corpus, a bench script) is
the judge of the code around it. `scorers[].pins` lists those files as
repo-relative globs; `score.py` hashes them **in the worktree it scores**, so
an executor's edit, a merge that touches one, or a new file matching a
corpus glob is a manifest mismatch and the run stops. `check_plan.py` refuses
a plan that owns a pinned path, and the guard denies a write to one (by tool
or by shell redirect) as a safety trip. `init.py` and `mkconfig.py` derive a
conservative default (the named script or module, a `corpora`/`fixtures`/
`evals` directory beside it); a project that knows better names its own.
Measured need: on this repo, three loop iterations raised coverage and left
the ARD eval untouched, and the cheapest way to move that eval was to edit
its corpus from inside the worktree.

`.loop/run/live.json` (inside the loop worktree, written by tail.py on every
stream line): `{live_spend_usd, budget_usd, remaining_usd}`. The planner reads
it before any revision re-dispatch; approved work is merged first, revisions
only with ≥ $2.5 remaining. `merge.json` is cumulative within an iteration.

`hooks/budget-gate.sh` (PreToolUse on Agent, child-wide): refuses an executor or
chore spawn when `spent + live + min_iter_usd ≥ cap`; a reviewer (or
test-assessor / ui-auditor) spawn is refused only when `spent + live ≥ cap`, so
work the executors already produced can still be reviewed and merged.

`hooks/require-report.sh` (Stop): block until `$NIGHTSHIFT_REPORT` exists and is
valid JSON with `status`; give up (exit 0) after 3 blocks and let the driver
treat the task as failed.

`hooks/events.sh` (SubagentStart/SubagentStop, project settings, async): append to
events.jsonl + events.log; no-op unless `.loop/run/loop.pid` exists.

`hooks/budget-gate.sh` (PreToolUse matcher `Agent`, project settings): deny when
`state.spent_usd + state.live_spend_usd + min_iter_usd ≥ cap_usd`; no-op unless loop.pid exists.

Executors receive `NIGHTSHIFT_WORKTREE` and `NIGHTSHIFT_REPORT` via the task
text (absolute paths) and the hooks read them from the tool input's paths, not
from env (subagent hooks do not inherit a per-task env).

## Exit codes

`pick.py`: 0 chosen · 3 nothing selectable · 1 error.
`check_plan.py`: 0 ok · 2 reject (reason on stdout, one line per problem) · 1 error.
`score.py`: 0 scored (row appended) · 4 infra broken (row appended with composite null) · 1 error.
`merge.sh`: 0 merged · 5 tests floor violated (nothing merged, worktree reset) · 1 error.
`assess.py`, `map.py`, `report.py`, `tail.py`: 0 always unless the arguments are wrong.

## Testing

Every Python script has a test file in `loop/tests/` runnable with
`python3 -m pytest loop/tests -q` (pytest from the discovery venv is fine for
running tests; the scripts themselves import only stdlib). Bash scripts are
tested with `bash loop/tests/test_<name>.sh` (plain assertions, exit non-zero on
failure). Fixtures build a throwaway git repo under `tmp_path`.
