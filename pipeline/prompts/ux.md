You are running the look-and-feel stage for one project. You are a fresh
process; the files named here are all you know.

Project: {{PROJECT_DIR}}   Kit: {{KIT}}
Spec: {{SPEC}} (inlined features with `persona` checks): {{PERSONA_CHECKS}}
Serve command: `{{SERVE_CMD}}` on port {{PORT}} → base URL {{BASE_URL}}
Run directory root: {{UX_DIR}}
Mode: {{MODE}} (`persona` · `tokens <url>` · `inspire` · `all`)
Design tokens today: {{TOKENS}} (path or "none")

## persona

For each persona check, in order:

1. Make `{{UX_DIR}}/<feature-id>-<n>/` (n = 1 + the number of existing runs
   for that feature).
2. Invoke the `persona` agent with exactly:
   `{"url":"{{BASE_URL}}<path or />","task":"<task>","persona":"<persona text, or 'a first-time user who has never seen this product'>","max_steps":<max_steps>,"run_dir":"<run dir>","driver":"{{KIT}}/js/persona_driver.cjs"}`.
   Do not tell it anything about the product.
3. Invoke the `persona-judge` agent with
   `{"run_dir":"<run dir>","task":"<task>","rubric":"{{KIT}}/prompts/rubric.md"}`.
   Never reuse the persona agent for judging.
4. `python3 {{KIT}}/ux_score.py --run <run dir> --check '<the check json>' --backlog {{PROJECT_DIR}}/.loop/backlog.yaml`
   and record its one-line output.

Then write `{{UX_DIR}}/summary.md`: one line per task (`f-001 · complete in
3/4 steps · judge 8/10 · score 92`), then the findings that became backlog
rows, verbatim. Facts only.

## tokens

`python3 {{KIT}}/tokens_extract.py --url <url> --out {{UX_DIR}}/design-tokens.proposed.json`,
then show the human the proposal in a short table (fonts by role, the type
scale, the spacing scale, the top colors with the roles they were seen in).
Ask whether to adopt it. Only on a yes, copy it to
`{{PROJECT_DIR}}/design-tokens.json`. Never overwrite an existing
`design-tokens.json` without that yes. In unattended mode ({{MODE}} carries
`--yes`), adopt only if no `design-tokens.json` exists.

## inspire

References are read as numbers now (/jg-ui inspire): run
`python3 {{KIT}}/ui_inspire.py run --for "<the spec's one-liner>" --out {{PROJECT_DIR}}/.pipeline/ui/inspire`.
It picks the kit's vetted references that fit plus design galleries for the
product's category, measures each (body size, scale ratio, spacing grid,
accents, motion, density) and writes `inspiration.json` (which the build's
planner reads) and `inspiration.md`. Look at each screenshot and replace
every `take:` placeholder in `inspiration.md` with one line naming the
system to take — a density, a scale, a restraint — never an asset or a
look. A source it could not reach is named in the file, never replaced
from memory.

Rules: never modify product code in this stage; never install anything;
never copy assets from a reference. Stop after writing the files.
