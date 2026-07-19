---
id: commit-guard
tier: convention
enforce: hook
deployed-to: .claude/settings.json
---

# Commit guard (mechanical enforcement of commit-discipline)

The `meta/harness/commit_guard/` PreToolUse hook enforces the machine-checkable
core of the commit-discipline rule on every `git commit`:

- Blocks direct commits on `main`/`master` — work happens on feature branches.
- Validates the commit message header against Conventional Commits (type
  whitelist, no uppercase start, no trailing period, subject ≤ 50 chars).

All failure paths are fail-open (never block unrelated Bash), and every block
message includes the `ATOM_COMMIT_OVERRIDE=1` re-run escape for deliberate
exceptions. Non-mechanical guidance (semantic units, branch naming, PR-only
merges, no pushes to main) lives in the commit-discipline rule.
