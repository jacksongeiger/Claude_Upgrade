# CLAUDE.md — <PROJECT_NAME>

## Project
- **Name:** <PROJECT_NAME>
- **Description:** <ONE_OR_TWO_SENTENCE_DESCRIPTION>
- **Status:** <e.g. prototype / active development / stable>

## Stack
- **Languages:** <e.g. Python 3.12, TypeScript>
- **Frameworks / libs:** <e.g. FastAPI, React, Next.js>
- **Runtime / infra:** <e.g. Docker, Postgres, deployed to X>

## Running the Project
```bash
# install deps
<install command>

# run dev / start
<run command>

# tests
<test command>
```

## Folder Structure
```
<project-root>/
  src/            # source code
  tests/          # tests
  scripts/        # one-off scripts and tooling
  docs/           # docs
  README.md
  CHANGELOG.md
```

## Project-Specific Rules & Conventions
- <e.g. all API responses must be JSON-serializable>
- <e.g. never call external APIs from inside model code>
- <e.g. all new modules require tests before merge>

## Design System

> Delete this section if the project has no UI. Otherwise fill it in before doing meaningful UI work; the global CLAUDE.md rule pulls this into every UI session.

**Tokens source file:** <where colors / fonts / spacing are declared — e.g. `tailwind.config.js`, `src/styles/tokens.css`, `app/globals.css`, `theme/index.ts`>

**Color tokens** (token name → actual value → how to reference):
- Primary: <e.g. `--color-primary` → `#7C3AED` → `bg-primary` / `var(--color-primary)`>
- Secondary: <e.g. `--color-secondary` → `#0EA5E9`>
- Background: <e.g. `--color-bg` → light + dark values>
- Text: <e.g. `--color-fg` → light + dark values>
- Error: <e.g. `--color-error` → `#EF4444`>

**Typography:**
- Display font: <e.g. Inter, declared at `src/styles/tokens.css`>
- Body font: <e.g. same>
- Mono font: <e.g. JetBrains Mono>

**Spacing scale:** <e.g. 4px-base; reference via Tailwind classes or `var(--space-2)` etc.>

**Component library:**
- Location: <e.g. `src/components/ui/`>
- Existing components that MUST be reused (don't recreate):
  - <e.g. `<Button>` — supports `variant="primary|secondary|ghost"` and `size="sm|md|lg">
  - <e.g. `<Card>`>
  - <e.g. `<Input>`>

**Hard rules:**
- Never hardcode colors, font names, or spacing values — always reference a token.
- Never create a new component that overlaps with an existing one in the component library — extend the existing one.
- If a needed token or component doesn't exist, add it to the source (tokens file or component library), then reference it. Do not inline a one-off.

## Known Gotchas
- <e.g. service X has a 30 req/min rate limit>
- <e.g. config file Y must be kept in sync with deployment env>
- <e.g. migration Z is irreversible — back up the DB first>

## Secrets
- Use `.env` for all secrets (never hardcode).
- `.env` must be in `.gitignore`.
- `.env.example` must list all required keys with no real values.
