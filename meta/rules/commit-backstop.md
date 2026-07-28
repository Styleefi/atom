---
id: commit-backstop
tier: convention
enforce: hook
deployed-to: .claude/settings.json
blocking: true
---

# Commit backstop (exact post-execution detection behind commit-guard)

The `meta/harness/commit_backstop/` PostToolUse hook is the exact safety net
behind the best-effort commit-guard (#52). After every Bash call it judges the
repository's **actual** state with commit-graph set arithmetic — no command
text or reflog inference:

- A protected branch (`main`/`master`) may only advance by commits that already
  exist on a remote `main`/`master`. Any other advance — direct commit, local
  merge of a (pushed or unpushed) branch, cherry-pick, plumbing — is reported
  with ordered, absolute-SHA recovery steps.
- New unpublished non-merge commits reachable from `HEAD` must satisfy the
  Conventional Commits header; violations are reported with an amend
  instruction. Git-generated subjects (`Merge`, `Revert "`, `fixup! `,
  `squash! `) are exempt.

Each violation is reported once (the evaluated tip becomes the recorded tip in
`<git-common-dir>/atom-commit-backstop.json`). Reports carry SHAs and reasons
only — commit subjects are never echoed (prompt-injection surface). All
failure paths are fail-open; repositories without a remote are skipped
entirely. `ATOM_COMMIT_OVERRIDE=1` in the command suppresses evaluation for
that one call while still recording tips. Known non-claims (commit+push in a
single command, out-of-cwd repositories, pre-first-observation history) are
listed in the module docstring — that layer belongs to server-side branch
protection.
