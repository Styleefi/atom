---
id: issue-duplicate-guard
tier: convention
enforce: hook
deployed-to: .claude/settings.json
---

# Issue duplicate guard

Creating an issue without searching first is mechanically blocked. A PreToolUse hook (`meta/harness/issue_duplicate_guard/`) intercepts `gh issue create` and `glab issue create`, searches open+closed issues by title with the same CLI, and blocks once, listing candidate duplicates. After reviewing the candidates, re-run the same command prefixed with `ATOM_DUP_REVIEWED=1` if the issue is genuinely new — the model keeps the judgment; the machine guarantees the search happened.

Limits: only the Bash path is guarded — MCP tools, `gh api`, and the web UI are not (covered by the `issue-workflow` conventions instead). Every failure mode (offline, unauthenticated, missing CLI, unparseable command) fails open: the guard never blocks unrelated commands.
