---
id: goal-verification
tier: principle
enforce: claude-md
deployed-to: CLAUDE.md
---

# Goal-driven verification

Non-trivial work starts from an approved plan; progress context a later
session must know is recorded per the issue-workflow rule.

## Define success criteria, loop until verified

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

Strong success criteria let you loop independently. Weak criteria ("make it
work") require constant clarification.

## Write tests, then run them before marking complete

**New code isn't done until it has tests, and nothing is "done" until tests
run green.**

Write:

- New function/module/endpoint → write tests covering the main path and at
  least one failure path.
- Bug fix → write a test that reproduces the bug first, then fix.
- Prefer writing tests before or alongside the implementation, not after
  everything "works".
- Exception: throwaway scripts, one-off experiments, and pure config changes
  may skip tests - but say so explicitly instead of silently omitting them.

Run:

- `npm test`, `pytest`, `cargo test`, whatever the project uses - run it.
- If tests pass, report results. If they fail, fix and re-run.
- No test setup? At minimum, verify the project builds/compiles.
- Run tests proactively, before the owner signals "끝", "완료", "다 됐어" -
  not after.

This is the step LLMs skip most often. Treat it as non-negotiable.
