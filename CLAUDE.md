# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Atom is the meta-repository (SSOT) for a personal ecosystem of Claude-driven projects. Child projects are created by cloning it (see README "Getting started") and receive revisions via `git pull upstream main`. Everything committed here — rules, templates, harnesses — is inherited by every child project, so prefer general, explicit conventions over one-off fixes.

## Current state

Foundation phase: documents and structure only. The first rules (`meta/rules/`) and the rules checker (`meta/harness/rules_checker/`) land in the next PR. The work backlog lives in GitHub Issues (`gh issue list`), not in this file.

## Commands

<!-- Added when the harness lands: `pytest` (all harnesses), rules checker invocation. -->

## Rules

<!-- Replaced with @meta/rules/ imports (always-on rules only) once the first rules land. -->

## Repo-specific rules

- The meta layer lives in `meta/` (rules SSOT, templates, harnesses — one subpackage per harness). Never move root `CLAUDE.md`, `.claude/`, or `.mcp.json` into it; Claude Code only recognizes them at fixed locations.
- Every rule file in `meta/rules/` MUST declare frontmatter: `id` (= filename), `enforce: claude-md | skill | hook`, `deployed-to`. A rule without a deployment target is not a rule — it is a wish.
- Never create a file named exactly `CLAUDE.md` outside the repo root: Claude Code auto-loads it when reading files in that directory. Templates use the name `CLAUDE.template.md`.
- `meta/templates/CLAUDE.template.md` is the CLAUDE.md scaffold for child projects. When editing it, keep the "INHERITED FROM ATOM" section in sync with this file's Rules/Commands sections.
- Work on feature branches and merge via PR only; never commit directly to main. Do not merge PRs yourself.
