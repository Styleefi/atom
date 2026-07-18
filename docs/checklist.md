# Checklist — foundation work

Working checklist for the current effort (plan: 2026-07-19 interview → foundation + first rules + first checker). Backlog items beyond this effort live in GitHub Issues.

## PR 1 — `docs/foundation` (this PR)

- [x] README rewritten: pain-driven 5 goals, layout, clone recipe (no fork button / no template repo)
- [x] Root CLAUDE.md: identity, current state, repo-specific rules, placeholders for Rules/Commands
- [x] `meta/templates/CLAUDE.template.md`: inherited section + placeholders + usage instructions
- [x] `docs/design.md`: rule system, propagation, layout decisions
- [x] `docs/context-notes.md`: decision log with reasoning
- [x] Backlog Issues created (#2–#6)
- [x] Branch protection on `main` applied (PR required, admins included)

## PR 2 — `feat/rules-checker` (this PR)

- [x] `meta/rules/rule-deployment.md` (invariant rule; `enforce: claude-md`)
- [x] `meta/rules/python-stack.md` (meta-layer Python conventions; scope-limited; uv environment isolation)
- [x] `meta/rules/plan-deviation.md` (deviations require options — codified from a live correction)
- [x] `meta/rules/answer-first.md` (answers may not be substituted by actions, plans, or approval dialogs — codified from live corrections; vessel review confirmed claude-md for both behavioral rules)
- [x] `meta/rules/README.md` (schema + revision procedure)
- [x] `meta/harness/rules_checker/` + `tests/` (pytest; edge cases: skip README, broken YAML as violation, repo-root-relative paths; **strengthened: unverifiable vessels (hook/skill) are rejected, never silently passed**)
- [x] `meta/pyproject.toml` + `uv.lock` (meta as self-contained uv project; root left free for product) + `.gitignore`
- [x] `.github/workflows/harness.yml` (CI via setup-uv: pytest + checker on every PR)
- [x] Root CLAUDE.md and template: placeholder comments → real `@meta/rules/` imports, Commands filled
- [x] Verify: `pytest` green (10/10); checker passes on real rules; failure paths covered by tests

## Definition of done

Both PRs merged by the owner; CI green on main; every box above checked.
