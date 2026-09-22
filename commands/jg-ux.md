---
description: Stage 4 — look and feel. A fresh-eyes persona tries the app, a judge grades the trail, findings become numbers; token extraction and inspiration on request.
---

Kit: `~/Claude_Upgrade/pipeline/` (`KIT`). Argument: `$ARGUMENTS`. The serve
command and port come from `spec.json` → `stack.serve` (`{"cmd","port"}`);
refuse with a clear message if they are missing.

| Argument | Do this |
|---|---|
| *(empty)* or `persona` | Start the serve command in the background (Bash `run_in_background`), wait for the port, then follow `$KIT/prompts/ux.md` with `MODE=persona` in this session: for each `persona` acceptance check spawn the `persona` agent, then the `persona-judge` agent, then `ux_score.py`. Print the summary. Stop the server. |
| `persona --unattended [--budget USD]` | `bash $KIT/child.sh --project . --prompt $KIT/prompts/ux.md --stage ux --budget <USD, default 6> --sub MODE=persona --sub PROJECT_DIR=$PWD --sub KIT=$KIT --sub-file SPEC=spec.json --sub PERSONA_CHECKS="$(jq -c '[.features[] | {id, checks: [.acceptance[] | select(.type=="persona")]} | select(.checks|length>0)]' spec.json)" --sub SERVE_CMD=... --sub PORT=... --sub BASE_URL=http://127.0.0.1:<port> --sub UX_DIR=$PWD/.pipeline/ux --sub TOKENS=<design-tokens.json or none>` after starting the server yourself. |
| `tokens <url>` | Prefer `/jg-ui direction --from <url>`: it measures the page properly and authors what extraction can't (colour roles for light and dark, contrast-checked scales, motion, tokens.css, DESIGN.md). The raw extractor is still `python3 $KIT/tokens_extract.py --url <url> --out .pipeline/ux/design-tokens.proposed.json`; adopt only on the human's yes. |
| `inspire` | Superseded by `/jg-ui inspire <what>`, which measures references into numbers the build reads. Run that. |
| `score` | Print every `.pipeline/ux/*/score.json` as one line each. |

## Rules

- The persona agent gets the URL, the task and the persona. Nothing else:
  not the feature name, not the code, not what "should" happen.
- The judge is never the agent that did the walkthrough.
- Findings that aren't numbers become backlog rows (`dimension: persona`);
  do not paraphrase them in chat as if they were decisions.
- Never modify product code in this stage. Never copy assets from a reference.
- Never overwrite an existing `design-tokens.json` without the human's yes.
