## Communication
- Be concise. Just do the work.
- Explanations should be brief and simple unless a deeper explanation is explicitly requested.
- Never be verbose or add fluff.

## Feasibility First
- Before writing any code for a new project or feature, always run a feasibility investigation unless explicitly told to skip it.
- The investigation should identify the core assumption the project depends on and validate it before anything is built.
- Research and present findings clearly, then give an explicit go/no-go recommendation with reasoning.
- Do not start building until the user confirms they want to proceed.
- Examples of what to investigate: market conditions, API availability, fee structures, data availability, whether a core edge or opportunity actually exists.
- If a feasibility investigation comes back no-go, log it in DEAD_ENDS.md with the idea, what was investigated, and why it was ruled out. This prevents revisiting the same dead ends.

## Code Style
- Default to Python unless another language is clearly better suited for the scope.
- Be adaptive — use whatever language/framework fits the project best.
- Write modular, loosely coupled code that is easy to adapt, extend, or pivot.
- Before implementing a large batch of changes, assess risk. If an early failure could cascade and waste time, test incrementally before proceeding.
- If something broken or improvable is noticed outside the current task, fix it.

## Overengineering
- If there is strong confidence that a solution is more complex than the problem requires, flag it clearly with reasoning. Never refuse to proceed, but always surface the concern so the user can decide.

## Secrets & Environment Variables
- Always use .env files for API keys and credentials. Never hardcode secrets.
- Always add .env to .gitignore before anything else.
- Organize .env files clearly with sections and comments so secrets are easy to find and understand.
- Provide a .env.example file with all required keys listed but no real values, so the project is easy to set up.

## Dependency Management
- Always create and maintain a requirements.txt (Python) or package.json (Node) with pinned versions.
- Keep dependencies minimal and intentional.

## Performance Benchmarking
- For projects where performance can be measured (bots, trading tools, data pipelines, etc.), always build in performance logging from the start.
- Log performance with timestamps and version labels so results can be compared across versions.
- Make it easy to score and compare performance across versions objectively.
- Do not force metrics on projects where performance benchmarking is not relevant.

## Git
- Never interrupt work to commit or push.
- At the end of every task summary, recommend whether it is a good time to push to git and why.
- If the user agrees, they will include "yes push to git" at the start of the next prompt and Claude should handle it before continuing with the next task.
- Never push automatically without this confirmation.

## Documentation
- Every project must have a README.md covering what the project does, current status, and key decisions made.
- Maintain a CHANGELOG.md in every project logging what changed in each version with timestamps, so progress is trackable and previous versions can be identified and compared.
- Maintain a DEAD_ENDS.md logging failed feasibility investigations so the same ground is never covered twice.

## Judgment & Scope
- When stuck or uncertain, make the best judgment call possible. Only ask the user if the situation is genuinely ambiguous and the wrong call would cause significant wasted work.

## Environment
- macOS (Apple Silicon), zsh
- Always available: Python 3, Node v24, npm, Homebrew, git, gh CLI, psql, sqlite3, jq, curl, make, Swift
- Node is managed via Homebrew, not nvm. No global TypeScript/Vite/Next CLIs — use per-project node_modules.

## On Completion
- Always end every task with a summary of what was done in a clean, easy to read format.
- Include a short section on what the next best move is to progress the project forward.
- Include a git push recommendation with reasoning.
