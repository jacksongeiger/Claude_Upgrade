> Overlap note: The built-in `init` command only creates a `CLAUDE.md`. This command bootstraps an entire project skeleton — feasibility check, template selection, folder structure, README/CHANGELOG/DEAD_ENDS stubs, `.env`/`.env.example`, dependency manifest, pre-push hook, and initial commit. Use `init` if you only need the CLAUDE.md for an existing repo; use this for a brand-new project.

You are bootstrapping a new project. Walk through this flow in order — do not skip steps unless the user explicitly tells you to.

## 1. Feasibility
- Per the global CLAUDE.md, run a feasibility investigation before writing code, unless the user has explicitly said skip it.
- Identify the core assumption the project depends on. Validate it (market conditions, API availability, data, fees — whatever applies).
- Present findings and give a GO / NO-GO recommendation.
- If NO-GO, log the idea and reasoning in `DEAD_ENDS.md` at the parent project location (or in this new project's `DEAD_ENDS.md` once it exists) and stop.
- Wait for explicit user confirmation before continuing.

## 2. Stack and template
- Default to **Python** per the global CLAUDE.md ("Default to Python unless another language is clearly better suited").
- Switch to **Next.js** if the project is clearly a web app, or **general** if the stack is genuinely undecided.
- Only ask the user if the right choice is genuinely ambiguous from context.
- Use the matching template from `~/Claude_Upgrade/templates/`:
  - Python → `CLAUDE.python.md`
  - Next.js → `CLAUDE.nextjs.md`
  - General → `CLAUDE.general.md`

## 3. Project folder
- Confirm the target path with the user (default: `~/<project-name>`).
- Create the folder and `cd` into it.
- Run `git init`.

## 4. CLAUDE.md
- Copy the chosen template into the project root as `CLAUDE.md`.
- Customize the placeholders: project name, one-or-two-sentence description, stack details, run commands. Do not leave any `<PLACEHOLDER>` tokens in the file when you're done.

## 5. Doc stubs
Create three stub files at the project root:
- `README.md` — title, one-line description, "## Status" section ("v0.1 — initial scaffold"), "## Setup" placeholder.
- `CHANGELOG.md` — header plus a v0.1 entry dated today documenting the scaffold.
- `DEAD_ENDS.md` — header line and an empty body.

## 6. Secrets
- Create `.env` with section headers as comments (e.g. `# === API keys ===`, `# === Database ===`) but no real values.
- Create `.env.example` mirroring `.env` with placeholder values (`YOUR_KEY_HERE`).
- Add `.env` to `.gitignore` (create `.gitignore` if it doesn't exist).

## 7. Dependencies
- Python: create `requirements.txt` with a comment header explaining pinning policy. Leave empty or seed with the project's known starting deps, pinned.
- Node: run `npm init -y`, then prune `package.json` to just the fields needed. Seed any known starting deps and pin versions.
- General: skip this step *for now*, but flag it explicitly in the final summary as an open item — the project should not write real code before a stack is chosen and the manifest exists.

## 8. Pre-push hook
- Run `~/Claude_Upgrade/install.sh --project <project-path>` to symlink the kit's pre-push hook into the new project's `.git/hooks/`.
- Verify the symlink resolves correctly.

## 9. Initial commit
- `git add` only the files you created (do not blanket `git add .` — review what's staged).
- Commit with the message exactly `Project bootstrap`.

## Output
End with the standard format from the global CLAUDE.md:
- Summary of what was created (file list, short)
- Next best move (one or two bullets — typically: write the first real code or set up CI)
- Git push recommendation: usually wait until there's a remote and at least one real feature commit before pushing
