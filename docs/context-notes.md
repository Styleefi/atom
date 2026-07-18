# Context notes — decisions and reasoning

Append-only log so any later session (human or agent) can pick up without re-deriving decisions. Newest entries at the bottom.

## 2026-07-19 — foundation design (structured interview)

- **Goals were re-derived from real pains, not kept from the old README.** Six recurring pains were collected (corrections trapped in memory, setup repetition, unverified "done", lost session context, overengineering, config fragmentation across home/work machines). The old "Core Objectives" were rewritten into the 5 pain-driven goals now in README. Agent Pipelines survived because a real, positive experience existed (implement → arch-review → test-runner → doc-writer), not on aspiration.
- **Key insight driving goal 3:** pains 3/4/5 recurred *despite* rules already written in the personal global CLAUDE.md — written advice alone does not change behavior; deterministic enforcement (hooks, checkers, CI) is required. Hence the first artifact with teeth is the rules checker, and it runs in CI so the checker itself is not optional.
- **Rule system:** `meta/rules/` is the SSOT; a rule counts only when deployed to one of three vessels (claude-md / skill / hook), declared via frontmatter. Undeployed rules are worse than none (illusion of enforcement).
- **Propagation is pure git.** Canonical config lives inside the repo; children clone and pull from upstream. Symlinks and `~/.claude/` global placement were considered and rejected (per-machine setup cost, unwanted global side effects, name-conflict risk). The user identified project-scoped `.claude/` + clone as sufficient — design simplified accordingly.
- **Clone recipe, not fork/template:** GitHub cannot fork into the same account; "Use this template" severs history and kills `git pull upstream main`.
- **Personal `~/.claude/CLAUDE.md` is out of scope.** Atom is responsible only for its children. Do not touch the personal global file until legacy rules (§1–11) are migrated into `meta/rules/` (it is the only live copy until then); afterwards slim it to avoid double loading.
- **Role catalog and pipelines intentionally undecided.** The 8 home-grown roles are explicitly *not* presumed to be the answer; focused review tracked in Issues. `.claude/agents/`·`.claude/skills/` are not created until content is decided (no speculative structure).
- **Folder layout:** meta layer isolated in `meta/` so child projects' roots stay clean for their product layer; fixed-path files (root CLAUDE.md, `.claude/`, `.mcp.json`) stay put; one subpackage per harness with its own `tests/` (+`__init__.py` against pytest name collisions).
- **Stack:** Python >= 3.12 + pytest for the meta layer only (local 3.14.4 verified); child product stacks are declared per-project via the template's Toolchain section. Codified as the `python-stack` rule in PR 2.
- **Backlog lives in GitHub Issues** (not a committed BACKLOG.md) so atom's own to-dos do not pollute child projects; committed docs/ session artifacts are cleared by children at bootstrap (template instructions).
- **Documents in English; commits English (Conventional Commits); conversation and code comments Korean** (per personal global guidelines).
