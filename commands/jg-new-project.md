---
description: Bootstrap a new project — a five-minute brief, /jg-validate on it, then the folder, docs, .env, deps, hook and first commit. Stops at the verdict.
---

> Overlap note: The built-in `init` command only creates a `CLAUDE.md`. This command bootstraps an entire project skeleton — feasibility check, template selection, folder structure, README/CHANGELOG/DEAD_ENDS stubs, `.env`/`.env.example`, dependency manifest, pre-push hook, and initial commit. Use `init` if you only need the CLAUDE.md for an existing repo; use this for a brand-new project.

You are bootstrapping a new project. Walk through this flow in order — do not skip steps unless the user explicitly tells you to.

## 0. The brief (five minutes, before any evidence is bought)
A two-sentence idea is too thin to validate well: the author role has to
invent who it is for and what would count as failure. Ask the human these,
one at a time, and write the answers into a paragraph that becomes the idea
text for step 1. Keep their words; do not improve the idea.
1. **Who** is it for, and what do they do today instead? (a person or a role, not "users")
2. **What pain** does it remove, in their words? What does it cost them now (time, money, missed chances)?
3. **What would make you stop?** A number or a fact that, if true, means you should not build it (e.g. "fewer than N people have this problem", "an existing tool already does it for free", "the signal cannot be measured before the price moves").
4. **How would you know it worked** a month after shipping? One or two numbers.
5. **Budget**: what you expect to spend building it, in dollars. This picks the evidence band (< $20 light, $20–100 standard, > $100 an experiment is required before dependent work).
If the human already gave a full brief in the prompt, confirm it in one line
and move on. The paragraph is quoted back once before step 1 runs.

## 1. Validate
- Create the project folder first (step 3 moves up: confirm the path, `mkdir`, `cd`, `git init`), because validation writes its files under the project's `.pipeline/validate/`.
- Run `/jg-validate --build-usd <what the human expects to spend building it> "<the idea>"`. This replaces the old inline feasibility check: the stage writes claims, freezes kill numbers before any evidence is read, fetches evidence with sources, and a script computes GO, PIVOT or NO-GO.
- On NO-GO: the stage has already appended the idea and the ledger summary to `DEAD_ENDS.md` in the new folder; copy that entry to the kit's `DEAD_ENDS.md` (the parent location) if the folder will be deleted, and stop.
- On PIVOT: revise the idea with the human and run it again.
- On GO: continue. `/jg-spec` will read the verdict; do not skip to the spec.

## 2. Stack and template
- Default to **Python** per the global CLAUDE.md ("Default to Python unless another language is clearly better suited").
- Switch to **Next.js** if the project is clearly a web app, or **general** if the stack is genuinely undecided.
- Only ask the user if the right choice is genuinely ambiguous from context.
- Use the matching template from `~/Claude_Upgrade/templates/`:
  - Python → `CLAUDE.python.md`
  - Next.js → `CLAUDE.nextjs.md`
  - General → `CLAUDE.general.md`

## 3. Project folder
- Already created in step 1. Confirm you are in it.

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
