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

## Comment claims

**Comments state immutable facts - rationale, provenance, pointers.
Guarantees live in tests.**

Prose is never machine-verified. A comment asserting a mutable global
property rots the moment an edit elsewhere changes that property, and the
next reader inherits a false premise.

- Say why the code exists and which failure or decision produced it -
  history is append-only, so no future edit can falsify it.
- Don't state guarantees ("always", "on every platform", "before any
  early return"), outcome predictions, or pin claims ("this ordering is
  pinned by the masking tests").
- A verifiable claim belongs in a test. Reference direction is one-way:
  the test cites the code site it pins; code comments never name tests.
- One causal narrative lives in exactly one place; other sites point to
  it instead of restating it.
- The write-time test: "Can an edit elsewhere falsify this sentence?"
  If yes - move it to a test, make it a pointer, or delete it.

When fixing prose flagged under this rule, delete or relocate - don't
reword. A rewrite is a new claim. (Origin: one overclaim class recurred
six times across the PR #93/#95 review loops, 2026-08-09..14, surviving
two rounds of careful rewording; adopted as a rule per #96.)

## Read errors, don't guess

**Read the actual error/log line. Don't pattern-match from memory.**

- Read the full error message and stack trace.
- Check the actual log output, not what you assume it should say.
- Don't apply a "common fix" before confirming the cause.
- If unclear, add a print/log to verify state - then fix.

This is the step LLMs skip most often after "run tests". They guess from error
keywords and apply the most-recent-pattern fix. That's how a one-line bug
becomes a three-file refactor.
