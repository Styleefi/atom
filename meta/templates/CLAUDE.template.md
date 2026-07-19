<!--
  CLAUDE.md template for child projects of atom.

  How to use (in your new project cloned from atom):
    1. Copy this file over the root CLAUDE.md, replacing atom's own.
    2. Fill in every {{PLACEHOLDER}} below; delete optional sections that stay empty.
    3. Delete these instruction comments. Keep the INHERITED FROM ATOM section as-is.
    4. Replace atom's docs/design.md with your own: durable rationale lives in curated docs, progress context in issue comments (issue-workflow rule).

  On `git pull upstream main` conflicts: keep YOURS for CLAUDE.md / README.md / docs/,
  take UPSTREAM'S for meta/, .github/, and .claude/.
-->
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

{{ONE_PARAGRAPH: what this project is, for whom, and its current phase}}

<!-- ===== INHERITED FROM ATOM — do not edit; revised via `git pull upstream main` ===== -->
## Rules (inherited)

Principles (constitutional — win conflicts with conventions; higher bar to amend):

@meta/rules/rule-deployment.md
@meta/rules/plan-deviation.md
@meta/rules/answer-first.md
@meta/rules/coding-discipline.md
@meta/rules/goal-verification.md

Conventions:

@meta/rules/python-stack.md
@meta/rules/issue-workflow.md
@meta/rules/korean-output.md
@meta/rules/commit-discipline.md

## Meta harness (inherited)

- `uv run --directory meta pytest` — run all meta harnesses (their tests)
- `uv run --directory meta python -m harness.rules_checker` — verify every rule is deployed as declared
<!-- ===== END INHERITED ===== -->

## Commands

- Build: {{BUILD_COMMAND}}
- Lint: {{LINT_COMMAND}}
- Test (all): {{TEST_COMMAND}}
- Test (single): {{SINGLE_TEST_COMMAND}}

## Toolchain & conventions

- Language & version: {{LANGUAGE_AND_VERSION}}
- Test runner: {{TEST_RUNNER}}
- Formatter / linter: {{FORMATTER_AND_LINTER}}
- Package manager: {{PACKAGE_MANAGER}}

## Architecture

{{BIG_PICTURE_ONLY: structure that requires reading multiple files to understand — data flow, module boundaries, key invariants. No file-by-file listing.}}

## Code style delta

{{ONLY_RULES_THAT_DIFFER: from language defaults and inherited rules. Delete this section if empty.}}

## Gotchas

{{NON_OBVIOUS: required env vars, environment quirks, known traps. Delete this section if empty.}}
