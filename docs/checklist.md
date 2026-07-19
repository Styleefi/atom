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

## PR 3 — `feat/issue-workflow` (this PR)

- [x] Working tree normalized (CRLF churn from a Windows-side tool discarded; content-identical verified) + `.gitattributes` (`* text=auto`) against recurrence
- [x] Issues registered with pre-creation duplicate search: #8 (this work), #9 (glab real-instance verification), #10 (dual-platform audit)
- [x] `meta/harness/issue_duplicate_guard/` + `tests/` (token-stream detection, fail-open invariant, gh/glab adapters, `ATOM_DUP_REVIEWED=1` override)
- [x] Rules checker: hook vessel verification (settings JSON references `harness.<id>`; harness package exists); skill still rejected
- [x] `meta/rules/issue-workflow.md` (claude-md) + `meta/rules/issue-duplicate-guard.md` (hook) + `.claude/settings.json` wiring
- [x] Root CLAUDE.md import + template sync (`.claude/` added to take-upstream conflict rule)
- [x] Docs: rules README hook convention, design.md checker note, this checklist, context-notes
- [ ] Verify: pytest green, checker green on 6 rules, manual hook stdin cases, latency measurement
- [ ] PR opened (`Closes #8`, `Refs #4`); merge is the owner's call
- [ ] Post-merge (tracked in issues): glab adapter real verification (#9), dual-platform audit (#10)

## Definition of done

All PRs merged by the owner; CI green on main; every box above checked.
