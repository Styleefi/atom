---
id: rule-deployment
tier: principle
enforce: claude-md
deployed-to: CLAUDE.md
---

# Rule deployment invariant

An approved rule is in force only when deployed to exactly one vessel:

- `claude-md` — imported into the root `CLAUDE.md` via `@meta/rules/<file>` (always loaded; keep these few and terse)
- `skill` — loaded on demand by relevance
- `hook` — deterministic enforcement outside the context window

This list is closed: adding a vessel is an amendment to this principle. A store that is not committed — agent memory, for one — is not a vessel, whatever it holds. (Origin: with this rule in context, the agent proposed keeping behavioral guidance in agent memory, 2026-09-02.)

Every file in `meta/rules/` MUST declare frontmatter with `id` (= filename stem), `tier`, `enforce`, and `deployed-to`. A rule without a deployment target is not a rule — it is a wish. The rules checker (`meta/harness/rules_checker/`) mechanically verifies that the declared target actually carries the rule; vessels whose verification is not implemented yet are **rejected, never silently passed**.

Every rule also carries a rank, orthogonal to its vessel: `tier: principle` (constitutional — wins conflicts with conventions; amending one requires explicit owner approval) or `tier: convention` (operational detail; evolves through normal PRs).
