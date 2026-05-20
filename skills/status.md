You are writing a project status summary. The goal is that someone coming back to this project after weeks away can get fully up to speed in under 60 seconds.

## Cover exactly these sections:

**Project**
One sentence on what this project does and what problem it solves.

**Current Version**
What version are we on and when was it last updated.

**What's Working**
Bullet list of what is fully functional right now.

**What's Not Working**
Bullet list of what is broken, incomplete, or unstable. Be honest.

**Performance**
If the project tracks performance metrics, show the most recent benchmark with version and timestamp. Show the best performing version if different from current. If no metrics exist, write N/A.

**Open Risks**
Anything that could cause this project to fail or stall — technical debt, unvalidated assumptions, external dependencies, known bugs that haven't been fixed.

**Dead Ends**
Any approaches that have already been tried and ruled out, so we don't revisit them.

**Next Best Moves**
Top 3 next steps ranked by impact. Be specific — not "improve the bot" but "add stop loss logic to prevent drawdown during high volatility."

## Rules:
- Scannable over comprehensive. Use bullets not paragraphs.
- Be honest about what isn't working. Don't sugarcoat status.
- If CHANGELOG.md exists, use it to inform the summary.
- If DEAD_ENDS.md exists, pull from it for the dead ends section.
