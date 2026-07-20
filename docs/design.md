# Design — rule system, propagation, and layout

Decisions confirmed on 2026-07-19 through a structured interview. This document is the curated reference for *why* the repository is shaped the way it is; per-task reasoning trails live in issue comments (issue-workflow rule) and git history.

## Rule system: registry + three deployment vessels

- `meta/rules/` is the rule SSOT. Rules are revised only through PRs.
- **Invariant (the first rule):** an approved rule is not in force until it is deployed to exactly one of three vessels:
  | Vessel | Loading | Use for |
  |---|---|---|
  | `claude-md` | every session (via `@meta/rules/` import in root CLAUDE.md) | few, always-relevant behavioral rules |
  | `skill` | on demand, by relevance | situational domain knowledge & workflows |
  | `hook` | deterministic, outside context | actions that must happen every time |
- Every rule file declares frontmatter: `id` (= filename stem), `tier` (rank), `enforce` (vessel), `deployed-to` (actual location). The rules checker (`meta/harness/rules_checker/`) verifies this mechanically, including a repo-level check that the root CLAUDE.md and child-template import lists stay identical (both directions).
- **Tier axis (2026-07-20):** rank is orthogonal to vessel. `principle` = constitutional — wins conflicts with conventions, amendments need explicit owner approval; `convention` = operational detail evolving through normal PRs. Introduced during the legacy-rule migration when the owner asked how "constitutional" status survives the move into a rule registry: the answer is that constitutionality is a property to declare and enforce (stability via PR review + checker, propagation via git), not a property of living in a special file.
- Rationale: written-but-undeployed rules are worse than no rules — they create the illusion of enforcement. Pains 3/4/5 (unverified "done", lost context, overengineering) recurred *despite* written rules, proving advice alone does not work.
- Checker v1 verified the deployment reference only for the `claude-md` vessel; reference semantics for `hook`/`skill` were deliberately undefined until the first such rule landed. The first hook rule (`issue-duplicate-guard`, 2026-07-19) fixed the hook convention: deployed-to is the settings JSON, which must reference the harness module derived from the rule id (`harness.<id_with_underscores>`), and that harness package must exist. The first skill rules (`file-header-comments`/`docstring-standards`, 2026-07-20) fixed the skill convention: deployed-to is a `SKILL.md` under `.claude/skills/` that references `meta/rules/<file>` — the SKILL.md is a loading entry point only, the rule body's SSOT stays in `meta/rules/` (no content duplication to drift).
- **Vessel-fit guidance** (from the vessel reviews of plan-deviation/answer-first and the issue-workflow split): `skill` fails for behavioral rules because the violator does not recognize the need for guidance at the violation moment — it fits knowledge with a clear task trigger (e.g. "writing code"). `hook` fits only machine-detectable violations; intent-level rules stay `claude-md`. When a rule mixes both, split it into a claude-md/hook pair (issue-workflow + issue-duplicate-guard; commit-discipline + commit-guard).
- **Skill dichotomy (2026-07-20):** not every skill under `.claude/skills/` is a rule. Skills encoding a *working agreement* (collaboration norms, standards, protocols) are rules — body SSOT in `meta/rules/`, SKILL.md as pointer; *functional* skills (plain tools/automation) are not rules and keep their body directly in the SKILL.md. The test: does changing the content change an agreement about how we work, or just improve a tool?
- The first interaction-protocol working-agreement skill is `grilling` (2026-07-20, adapted from mattpocock/skills, MIT) — unlike code-comments (an output standard), it packages an interview protocol. It fits the vessel-fit guidance above rather than being an exception: it is not a behavioral rule needing violation-moment awareness but knowledge with a clear task trigger (a plan/design presented with unresolved decisions).
- **Hook design invariants** (fixed by the first two guards): no false blocks — tokenize the whole command so quoted mentions can never sit in command position; every failure path fails open (hooks run on every Bash call and must never brick the tool), including the `command -v uv` shell guard; every block is recoverable in one retry via an explicit override marker that states the claim being made (`ATOM_DUP_REVIEWED=1`, `ATOM_COMMIT_OVERRIDE=1`).
- Context budget: `claude-md` rules are loaded every session in full, so the vessel admits only rules that must hold in every session. Migration outcome (2026-07-20, owner decision): consistency beats token savings — claude-md rule bodies keep their battle-tested original wording (paraphrasing during compression is itself a drift event); the budget is guarded by routing rules to `skill`/`hook` where fit allows, not by lossy rewording. Always-loaded volume is reported per PR while #6 (global slim-down) is pending.

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
- Deferred to GitHub Issues: agent role catalog & standard pipelines (the 8 home-grown roles are *not* presumed to be the answer), hooks seed, `.mcp.json` canonicalization, personal global file slim-down.
- `.claude/agents/` is intentionally **not** created yet: structure ahead of decided content is speculative design. (The same held for `.claude/skills/` until 2026-07-20, when the legacy-rule migration produced its first decided content — the `code-comments` skill — superseding that restriction for skills.)
- **Session-record artifacts retired (2026-07-20):** the legacy "plan + checklist.md + context-notes.md per task" practice (global §7) was retired with the owner's sign-off. Plan mode plus the goal-verification rule cover the plan mandate; progress context goes to issue comments (issue-workflow); durable rationale goes to this curated document, which is edited in place instead of growing append-only. `docs/context-notes.md` and `docs/checklist.md` were distilled into this file and deleted.
- `.gitattributes` (`* text=auto`) exists because a Windows-side tool once flipped the whole working tree to CRLF (WSL), creating 20 phantom-modified files; diagnosed with `git diff --ignore-cr-at-eol`.
- Python (>= 3.12, pytest, uv) is the stack for the meta layer only; each child project declares its own product stack in its CLAUDE.md. Dependency floors sit at the current major (e.g. `pytest>=9.0`): installs always resolve to the latest (no caps), while the floor states the oldest version the code is believed to work with.
