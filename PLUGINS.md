# Claude Code Plugins

## Currently Installed
- frontend-design@claude-plugins-official — opinionated UI generation, anti-generic-AI aesthetic
- code-review@claude-plugins-official — 5-agent PR review pipeline with confidence scoring
- skill-creator@claude-plugins-official — create and test custom skills
- github@claude-plugins-official — GitHub integration (needs GITHUB_PERSONAL_ACCESS_TOKEN in env)
- context7@claude-plugins-official — live documentation lookup
- chrome-devtools-mcp — browser automation, screenshots, Lighthouse audits
- claude-mem (thedotmack) — persistent cross-session memory
- superpowers (obra) — parallel agents, systematic debugging, brainstorming, verification flows

## Install on a New Machine
Run these in Claude Code after install.sh:
/plugin install feature-dev@claude-plugins-official
/plugin install commit-commands@claude-plugins-official
/plugin install playwright@claude-plugins-official
/plugin install serena@claude-plugins-official
/plugin install code-simplifier@claude-plugins-official

## Recommended but Not Installed
- feature-dev — structured feature development with planning gates (89k installs, most popular)
- commit-commands — /commit and /push slash commands with structured messages
- playwright — browser automation and UI testing via MCP, pairs with chrome-devtools-mcp
- serena — semantic code search across large codebases
- code-simplifier — flags overengineered code, suggests simpler approaches

## Skip (not relevant to current stack)
- clangd-lsp, gopls-lsp, csharp-lsp — LSP plugins for C++/Go/C#
- laravel-boost — Laravel only
- terraform — infrastructure as code
- discord, telegram, imessage, fakechat — messaging integrations

## Notes
- Browse all plugins: /plugin > Discover tab or claude.com/plugins
- Official marketplace: claude-plugins-official (auto-available)
- Community marketplace: anthropics/claude-plugins-community
- Always read plugin README before installing
- Too many plugins = context bloat and command collisions — install only what fills a real gap
