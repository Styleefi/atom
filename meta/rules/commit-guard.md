---
id: commit-guard
tier: convention
enforce: hook
deployed-to: .claude/settings.json
blocking: true
---

# Commit guard (best-effort prevention in front of commit-backstop)

The `meta/harness/commit_guard/` PreToolUse hook is the **best-effort
prevention** half of commit-discipline enforcement: it infers `git commit`
invocations from command text before execution and

- blocks direct commits on `main`/`master` — work happens on feature branches;
- validates the commit message header against Conventional Commits (type
  whitelist, no uppercase start, no trailing period, subject ≤ 50 chars).

Its claim is deliberately narrow (#52): text inference cannot decide shell
semantics, so its known detection gaps (#30 #44 #49 #50 #51 classes) are
**frozen, not fixed** — exact detection after execution is the commit-backstop
rule's job. All failure paths are fail-open (never block unrelated Bash), and
every block message includes the `ATOM_COMMIT_OVERRIDE=1` re-run escape for
deliberate exceptions. Non-mechanical guidance (semantic units, branch naming,
PR-only merges, no pushes to main) lives in the commit-discipline rule.
