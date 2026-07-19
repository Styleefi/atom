---
id: issue-workflow
enforce: claude-md
deployed-to: CLAUDE.md
---

# Issue workflow

The work backlog's SSOT is the project's issue tracker — GitHub Issues or GitLab Issues (the project's CLAUDE.md "Toolchain & conventions" declares which). Never mirror the backlog into files.

- Find work with `gh issue list` / `gh issue view <n>` (GitLab: `glab issue list` / `glab issue view <n>`).
- A PR/MR that completes an issue links it with `Closes #<n>` in its body — both platforms auto-close on merge, keeping work↔code traceable.
- Progress context a later session must know goes into issue comments, not chat history.
- Duplicate prevention on `issue create` is enforced deterministically by the `issue-duplicate-guard` hook rule; paths that hook cannot see (MCP tools, web UI) still require a manual search first.
