# Checklist — foundation work

Working checklist for the current effort (plan: 2026-07-19 interview → foundation + first rules + first checker). Backlog items beyond this effort live in GitHub Issues.

## PR 1 — `docs/foundation` (this PR)

- [x] README rewritten: pain-driven 5 goals, layout, clone recipe (no fork button / no template repo)
- [x] Root CLAUDE.md: identity, current state, repo-specific rules, placeholders for Rules/Commands
- [x] `meta/templates/CLAUDE.template.md`: inherited section + placeholders + usage instructions
- [x] `docs/design.md`: rule system, propagation, layout decisions
- [x] `docs/context-notes.md`: decision log with reasoning
- [ ] Backlog Issues created (5) — after PR creation
- [ ] Branch protection on `main` proposed to owner

## PR 2 — `feat/rules-checker` (next, same session)

- [ ] `meta/rules/rule-deployment.md` (invariant rule; `enforce: claude-md`)
- [ ] `meta/rules/python-stack.md` (meta-layer Python conventions; scope-limited)
- [ ] `meta/rules/README.md` (schema + revision procedure)
- [ ] `meta/harness/rules_checker/` + `tests/` (pytest; edge cases: skip README, broken YAML as violation, repo-root-relative paths)
- [ ] `pyproject.toml` (`requires-python >= 3.12`, testpaths) + `.gitignore`
- [ ] `.github/workflows/harness.yml` (CI: pytest + checker on every PR)
- [ ] Root CLAUDE.md and template: placeholder comments → real `@meta/rules/` imports, Commands filled
- [ ] Verify: `pytest` green; checker passes on real rules; intentionally broken frontmatter fails

## Definition of done

Both PRs merged by the owner; CI green on main; every box above checked.
