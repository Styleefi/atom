# meta/rules/ — rule SSOT

Every rule in this directory is one Markdown file with mandatory YAML frontmatter:

```yaml
---
id: <kebab-case, must equal the filename stem>
tier: principle | convention
enforce: claude-md | skill | hook
deployed-to: <repo-root-relative path of the actual deployment location>
blocking: true | false   # hook rules only — selects the canonical wrapper template
---
```

- `tier: principle`: constitutional rank — when a principle and a convention conflict, the principle wins; amending a principle requires explicit owner approval, not a routine PR. `tier: convention`: operational detail that evolves through normal PRs. Tier is orthogonal to `enforce` (a principle may deploy as any vessel).
- `enforce: claude-md`: the rule is imported into the root `CLAUDE.md` (`@meta/rules/<file>`) and loads every session — admit only rules that must hold in every session (context budget). The checker verifies the import line actually exists in the target, and that the root import list matches the child template's INHERITED block (both directions).
- `enforce: hook`: the rule's enforcement lives in a harness package `meta/harness/<id with - → _>/`, wired into the deployed-to settings JSON. The checker verifies the settings file parses as a JSON object, that at least one hook command invokes the `harness.<id_with_underscores>` module via `-m`, that every referencing command byte-matches the canonical wrapper template selected by `blocking` (`true` → the 42→2 remapping wrapper, `false` → the never-blocking `|| exit 1` wrapper; #31), and that the harness package exists. A repo-level reverse sweep additionally requires every harness-invoking hook command — ruled or not, in `.claude/settings.json`/`settings.local.json` or any hook rule's deployed-to — to match a canonical template; non-harness commands are out of scope.
- `enforce: skill`: the rule deploys as a `SKILL.md` under `.claude/skills/`, loaded on demand by its description. The SKILL.md carries only a pointer — the rule body's SSOT stays here — and the checker verifies the deployed-to path shape and that the SKILL.md references `meta/rules/<file>` (v1: substring reference check).
  - Not every skill under `.claude/skills/` is a rule. Skills that encode a **working agreement** (collaboration norms, standards, protocols) are rules and follow this SSOT-plus-pointer pattern. **Functional** skills (plain tools/automation) are not rules — their body lives directly in the SKILL.md and evolves through ordinary PRs. The test: does changing the content change an agreement about how we work, or just improve a tool?
- Vessels without implemented verification are **rejected**, never silently passed.
- Every rule must also appear in the owner-facing inventory (`meta/README.md`), which the checker verifies in both directions — an unlisted rule and a listed-but-deleted rule both fail. Functional skills, harnesses, and infra stacks are covered by the same check under their own table there. That coverage check is deferred (and reported as deferred) on any run where a rule below is itself failing, since it reads frontmatter to do its job.
- This README is not a rule and is excluded from checking.

## Revision procedure

Rules change **only through PRs** — never edit on main. The rules checker (`meta/harness/rules_checker/`, run by `pytest` and CI) rejects rules with missing/invalid frontmatter, a missing deployment target, or a target that does not actually carry the rule.
