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

## Known Gotchas
- <e.g. service X has a 30 req/min rate limit>
- <e.g. config file Y must be kept in sync with deployment env>
- <e.g. migration Z is irreversible — back up the DB first>

## Secrets
- Use `.env` for all secrets (never hardcode).
- `.env` must be in `.gitignore`.
- `.env.example` must list all required keys with no real values.
