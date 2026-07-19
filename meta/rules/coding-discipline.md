---
id: coding-discipline
tier: principle
enforce: claude-md
deployed-to: CLAUDE.md
---

# Coding discipline

Behavioral rules to reduce common LLM coding mistakes. They bias toward
caution over speed; for trivial tasks, use judgment.

## Think before coding

**Don't assume. Don't hide confusion. Surface tradeoffs.** Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## Simplicity first

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## Surgical changes

**Touch only what you must. Clean up only your own mess.**

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.
- Remove imports/variables/functions that YOUR changes made unused; don't
  remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## Read errors, don't guess

**Read the actual error/log line. Don't pattern-match from memory.**

- Read the full error message and stack trace.
- Check the actual log output, not what you assume it should say.
- Don't apply a "common fix" before confirming the cause.
- If unclear, add a print/log to verify state - then fix.

This is the step LLMs skip most often after "run tests". They guess from error
keywords and apply the most-recent-pattern fix. That's how a one-line bug
becomes a three-file refactor.
