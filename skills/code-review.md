You are performing a thorough code review. Work through these passes in order:

## Pass 1: Architecture
- Is the overall approach correct for the problem?
- Is the code modular and easy to pivot or extend?
- Is anything overengineered for what is actually needed?
- Are responsibilities clearly separated?
- If the architecture is wrong, stop and flag it before reviewing anything else.

## Pass 2: Risk & Failure Modes
- What happens when things go wrong? Check for missing error handling.
- Are there edge cases that will cause failures?
- Are there any silent failures — errors that get swallowed without logging?
- Are API calls, database queries, and external dependencies properly handled?
- Are there any race conditions or timing issues?

## Pass 3: Security & Secrets
- Are any secrets, API keys, or credentials hardcoded?
- Is .env in .gitignore?
- Is user input validated and sanitized?

## Pass 4: Performance & Scale
- Are there any obvious performance bottlenecks?
- Any N+1 query problems or unnecessary loops?
- Will this break under higher load or larger data?

## Pass 5: Maintainability
- Is the code easy to read and understand?
- Are variable and function names clear?
- Is anything so clever it will be confusing later?
- Are dependencies pinned in requirements.txt or package.json?

## Output Format
- List issues by pass, ranked by severity within each pass
- For each issue: what it is, why it matters, and the recommended fix
- Fix anything straightforward immediately
- Flag anything that needs a decision from the user
- End with an overall assessment: ready to build on, or needs work first
