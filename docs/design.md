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
- Checker v1 verified the deployment reference only for the `claude-md` vessel; reference semantics for `hook`/`skill` were deliberately undefined until the first such rule landed. The first hook rule (`issue-duplicate-guard`, 2026-07-19) fixed the hook convention: deployed-to is the settings JSON, which must reference the harness module derived from the rule id (`harness.<id_with_underscores>`), and that harness package must exist. `skill` remains undefined and is rejected.
- Context budget: `claude-md` rules are loaded every session in full. When migrating legacy rules (global §1–11), re-classify most into `skill`/`hook`; do not default everything to `claude-md`.

## Propagation: git is the whole mechanism

- Canonical config is committed **inside** this repository (`meta/`, and later `.claude/`, `.mcp.json`). Child projects get it by cloning; revisions arrive via `git pull upstream main`. No symlinks, no machine-global (`~/.claude/`) placement, no per-machine setup.
- Child project creation uses the clone recipe (README "Getting started") because GitHub cannot fork into the same account (E1) and "Use this template" severs history, breaking upstream pulls (E2).
- Upstream-pull conflicts on `CLAUDE.md`/`README.md`/`docs/` are **by design** (children replace those files). Resolution convention: keep the child's version for those paths; take upstream for `meta/` and `.github/`.
- The personal `~/.claude/CLAUDE.md` is outside atom's scope. Atom is responsible only for its children; other repositories may copy files manually. Until the legacy rules are migrated into `meta/rules/`, the personal global file remains the only live copy of those rules — do not touch it before migration completes.

## Layout: meta layer isolated in `meta/`

- `meta/` holds everything that exists to run the Claude ecosystem (rules, templates, harnesses), so a child project's root stays clean for its product layer (`services/`, `infra/`, `libs/`, …).
- `meta/` is a **self-contained uv project** (own `pyproject.toml`, committed `uv.lock`, own `.venv`). The repo root deliberately has no `pyproject.toml` — that slot belongs to a child project's product. Meta and product environments are fully separate, so their dependency versions (e.g. pytest) can never conflict, no matter how far they drift. venv (manual multi-env, no lockfile) and Docker (overkill for second-long unit tests) were considered and rejected.
- Files with fixed-path semantics stay at their required locations: root `CLAUDE.md`, `.claude/`, `.mcp.json`.
- Harnesses grow as one subpackage each under `meta/harness/<name>/`, with their own `tests/` (each `tests/` contains `__init__.py` to avoid pytest module-name collisions between harnesses).
- Never name a file exactly `CLAUDE.md` outside the repo root — Claude Code auto-loads child-directory `CLAUDE.md` files on demand, which would make template/reference content leak into sessions unpredictably.

## Scope decisions (2026-07-19)

- Materialized now: foundation documents, first three rules (`rule-deployment`, `python-stack`, `plan-deviation`), rules checker harness with CI.
- Checker strengthening (owner decision): vessels whose deployment verification is not implemented (`hook`, `skill`) are **rejected, never silently passed** — adding the first hook/skill rule requires implementing its verification first. This closes the "declared but unverifiable" loophole.
- `plan-deviation` rule origin: during this work, an unplanned tool install (`uv`) was attempted without presenting options — corrected and codified as a rule the same day (the rule system's first live use).
- Deferred to GitHub Issues: agent role catalog & standard pipelines (the 8 home-grown roles are *not* presumed to be the answer), legacy rule migration, hooks seed, `.mcp.json` canonicalization, personal global file slim-down.
- `.claude/agents/` and `.claude/skills/` are intentionally **not** created yet: structure ahead of decided content is speculative design.
- Python (>= 3.12, pytest, uv) is the stack for the meta layer only; each child project declares its own product stack in its CLAUDE.md. Dependency floors sit at the current major (e.g. `pytest>=9.0`): installs always resolve to the latest (no caps), while the floor states the oldest version the code is believed to work with.
