# Design — rule system, propagation, and layout

Decisions confirmed on 2026-07-19 through a structured interview (see context-notes.md for the reasoning trail). This document is the reference for *why* the repository is shaped the way it is.

## Rule system: registry + three deployment vessels

- `meta/rules/` is the rule SSOT. Rules are revised only through PRs.
- **Invariant (the first rule):** an approved rule is not in force until it is deployed to exactly one of three vessels:
  | Vessel | Loading | Use for |
  |---|---|---|
  | `claude-md` | every session (via `@meta/rules/` import in root CLAUDE.md) | few, always-relevant behavioral rules |
  | `skill` | on demand, by relevance | situational domain knowledge & workflows |
  | `hook` | deterministic, outside context | actions that must happen every time |
- Every rule file declares frontmatter: `id` (= filename stem), `enforce` (vessel), `deployed-to` (actual location). The rules checker (`meta/harness/rules_checker/`) verifies this mechanically.
- Rationale: written-but-undeployed rules are worse than no rules — they create the illusion of enforcement. Pains 3/4/5 (unverified "done", lost context, overengineering) recurred *despite* written rules, proving advice alone does not work.
- Checker v1 verifies the deployment reference only for the `claude-md` vessel (target file exists and imports/mentions the rule). Reference semantics for `hook`/`skill` vessels are deliberately undefined until the first such rule lands.
- Context budget: `claude-md` rules are loaded every session in full. When migrating legacy rules (global §1–11), re-classify most into `skill`/`hook`; do not default everything to `claude-md`.

## Propagation: git is the whole mechanism

- Canonical config is committed **inside** this repository (`meta/`, and later `.claude/`, `.mcp.json`). Child projects get it by cloning; revisions arrive via `git pull upstream main`. No symlinks, no machine-global (`~/.claude/`) placement, no per-machine setup.
- Child project creation uses the clone recipe (README "Getting started") because GitHub cannot fork into the same account (E1) and "Use this template" severs history, breaking upstream pulls (E2).
- Upstream-pull conflicts on `CLAUDE.md`/`README.md`/`docs/` are **by design** (children replace those files). Resolution convention: keep the child's version for those paths; take upstream for `meta/` and `.github/`.
- The personal `~/.claude/CLAUDE.md` is outside atom's scope. Atom is responsible only for its children; other repositories may copy files manually. Until the legacy rules are migrated into `meta/rules/`, the personal global file remains the only live copy of those rules — do not touch it before migration completes.

## Layout: meta layer isolated in `meta/`

- `meta/` holds everything that exists to run the Claude ecosystem (rules, templates, harnesses), so a child project's root stays clean for its product layer (`services/`, `infra/`, `libs/`, …).
- Files with fixed-path semantics stay at their required locations: root `CLAUDE.md`, `.claude/`, `.mcp.json`.
- Harnesses grow as one subpackage each under `meta/harness/<name>/`, with their own `tests/` (each `tests/` contains `__init__.py` to avoid pytest module-name collisions between harnesses).
- Never name a file exactly `CLAUDE.md` outside the repo root — Claude Code auto-loads child-directory `CLAUDE.md` files on demand, which would make template/reference content leak into sessions unpredictably.

## Scope decisions (2026-07-19)

- Materialized now: foundation documents, first two rules (`rule-deployment`, `python-stack`), rules checker harness with CI.
- Deferred to GitHub Issues: agent role catalog & standard pipelines (the 8 home-grown roles are *not* presumed to be the answer), legacy rule migration, hooks seed, `.mcp.json` canonicalization, personal global file slim-down.
- `.claude/agents/` and `.claude/skills/` are intentionally **not** created yet: structure ahead of decided content is speculative design.
- Python (>= 3.12, pytest) is the stack for the meta layer only; each child project declares its own product stack in its CLAUDE.md.
