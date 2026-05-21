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

## Design System

> This is the project's authoritative source of UI ground truth. Fill it in before doing meaningful UI work, then keep it current. The global CLAUDE.md rule "Before writing any UI code, read … any ## Design System section in the project's CLAUDE.md" pulls this into every UI session.

**Tokens source file:** <e.g. `tailwind.config.ts`, `app/globals.css`, `src/styles/tokens.css`, `lib/design-tokens.ts`>

**Color tokens** (token name → actual value → how to reference):
- Primary: <e.g. `--color-primary` → `#7C3AED` → `bg-primary` / `text-primary` / `theme.colors.primary`>
- Secondary: <e.g. `--color-secondary` → `#0EA5E9` → `bg-secondary`>
- Background: <e.g. `--color-bg` → `#0B0B0F` (dark) / `#FFFFFF` (light) → `bg-bg`>
- Text: <e.g. `--color-fg` → `#E5E7EB` (dark) / `#0F172A` (light) → `text-fg`>
- Error: <e.g. `--color-error` → `#EF4444` → `bg-error` / `text-error`>

**Typography:**
- Display font: <e.g. Geist Sans, declared in `app/layout.tsx` via `next/font/google`>
- Body font: <e.g. Geist Sans, same import>
- Mono font: <e.g. Geist Mono, declared alongside>

**Spacing scale:** <e.g. 4px-base via default Tailwind; never use raw `px` values, always `p-2`, `gap-4`, etc.>

**Component library:**
- Location: <e.g. `components/ui/`>
- Existing components that MUST be reused (don't recreate):
  - <e.g. `<Button variant="primary" | "secondary" | "ghost">` — `components/ui/Button.tsx`>
  - <e.g. `<Card>` — `components/ui/Card.tsx`>
  - <e.g. `<Input>` — `components/ui/Input.tsx`>

**Hard rules:**
- Never hardcode colors, font names, or spacing values — always reference a token.
- Never create a new component that overlaps with an existing one in the component library — extend the existing one (add a `variant` prop, etc.).
- If a needed token or component doesn't exist, add it to the source (tokens file or component library), then reference it. Do not inline a one-off.

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
