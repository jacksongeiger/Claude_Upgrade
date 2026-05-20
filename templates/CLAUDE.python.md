# CLAUDE.md — <PYTHON_PROJECT_NAME>

## Project
- **Name:** <PYTHON_PROJECT_NAME>
- **Description:** <ONE_OR_TWO_SENTENCE_DESCRIPTION>
- **Python version:** 3.12+ (or pin specific version here)

## Setup

```bash
# create virtual environment
python3 -m venv venv

# activate
source venv/bin/activate

# install dependencies
pip install -r requirements.txt
```

## Running

```bash
# entrypoint
python src/main.py

# tests
pytest tests/
```

## Folder Structure

```
<project-root>/
  src/            # source code
  tests/          # pytest tests
  data/           # raw / processed data (gitignored if large)
  logs/           # runtime logs (gitignored)
  requirements.txt
  .env            # secrets (gitignored)
  .env.example    # template with required keys
  README.md
  CHANGELOG.md
```

## Project-Specific Rules
- <e.g. all I/O goes through src/io/, no scattered open() calls>
- <e.g. log every external API call with timestamp and latency>
- <e.g. all configs read from .env via a single config.py module>

## Dependencies
- Always pin versions in `requirements.txt` (e.g. `pandas==2.2.3`).
- Keep deps minimal — prefer stdlib when reasonable.

## Secrets
- **Always use `.env`** for API keys, DB URLs, and credentials.
- `.env` must be in `.gitignore` before any commit.
- Provide a `.env.example` with all required keys listed but no real values.

## Known Gotchas
- <e.g. dependency X breaks on Python 3.14 — pin to 3.12>
- <e.g. data/ has files >100MB, do not commit>
