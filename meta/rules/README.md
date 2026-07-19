# meta/rules/ — rule SSOT

Every rule in this directory is one Markdown file with mandatory YAML frontmatter:

```yaml
---
id: <kebab-case, must equal the filename stem>
enforce: claude-md | skill | hook
deployed-to: <repo-root-relative path of the actual deployment location>
---
```

- `enforce: claude-md`: the rule is imported into the root `CLAUDE.md` (`@meta/rules/<file>`) and loads every session — keep these few and terse (context budget). The checker verifies the import line actually exists in the target.
- `enforce: hook`: the rule's enforcement lives in a harness package `meta/harness/<id with - → _>/`, wired into the deployed-to settings JSON. The checker verifies the settings file parses as JSON, references the `harness.<id_with_underscores>` module in a hook command, and that the harness package exists (v1: substring reference check).
- `enforce: skill`: deployment verification is not implemented yet, so the checker **rejects** such rules. Implement the verification first (see docs/design.md) — declared-but-unverifiable deployment never passes.
- This README is not a rule and is excluded from checking.

## Revision procedure

Rules change **only through PRs** — never edit on main. The rules checker (`meta/harness/rules_checker/`, run by `pytest` and CI) rejects rules with missing/invalid frontmatter, a missing deployment target, or a target that does not actually carry the rule.
