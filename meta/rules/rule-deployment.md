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

Every file in `meta/rules/` MUST declare frontmatter with `id` (= filename stem), `enforce`, and `deployed-to`. A rule without a deployment target is not a rule — it is a wish. The rules checker (`meta/harness/rules_checker/`) mechanically verifies that the declared target actually carries the rule; vessels whose verification is not implemented yet are **rejected, never silently passed**.
