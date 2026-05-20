# CLAUDE.md — <NEXTJS_PROJECT_NAME>

## Project
- **Name:** <NEXTJS_PROJECT_NAME>
- **Description:** <ONE_OR_TWO_SENTENCE_DESCRIPTION>
- **Framework:** Next.js (App Router) + TypeScript

## Setup

```bash
# install dependencies
npm install

# dev server
npm run dev

# production build
npm run build && npm start

# lint / typecheck
npm run lint
npx tsc --noEmit
```

## Folder Structure

```
<project-root>/
  app/            # App Router routes, layouts, pages
  components/     # Reusable React components
  lib/            # Utilities, API clients, helpers
  public/         # Static assets (images, fonts, etc.)
  styles/         # Global CSS / Tailwind config
  .env.local      # secrets (gitignored)
  .env.example    # template with required keys
  package.json
  README.md
  CHANGELOG.md
```

## Tooling Notes
- **No global Next/TypeScript/Vite CLIs.** Always use the project-local versions via `npx` or `npm run`.
- Node is managed via Homebrew (not nvm).

## Project-Specific Rules
- <e.g. server components by default; mark `"use client"` only when necessary>
- <e.g. all data fetching goes through lib/api/>
- <e.g. shared UI components live in components/ui/>

## Dependencies
- Pin versions in `package.json` (no `^` / `~` if reproducibility matters).
- Commit `package-lock.json`.

## Secrets
- **Always use `.env.local`** for secrets in Next.js (Next reads `.env.local` automatically and gitignores it by default).
- Prefix client-exposed vars with `NEXT_PUBLIC_` — everything else is server-only.
- Provide a `.env.example` listing all required keys with no real values.

## Known Gotchas
- `.env` vs `.env.local` — use `.env.local` for secrets; `.env` is committed in some setups.
- Anything imported into a client component bundles to the browser — keep secrets and heavy deps in server code only.
- Server components cannot use React hooks or browser APIs.
