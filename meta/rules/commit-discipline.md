---
id: commit-discipline
tier: convention
enforce: claude-md
deployed-to: CLAUDE.md
---

# Commit discipline

**Commit when one logical change is complete. Don't wait for the owner to ask.**

- The test: "Can I describe this commit in one sentence?" If yes, commit. If
  no, the changes are still mixed - split them.
- Message format - **Conventional Commits, English only** (commits are English
  even when comments and chat are Korean):
  - Header: `type(scope): subject` - e.g. `feat(core): add lazy log context builder`
  - Types: feat, fix, refactor, test, docs, chore, build, perf, style
  - Scope: the package/domain touched (e.g. `core`, `collector`); omit if repo-wide
  - Subject: imperative mood, lowercase start, no trailing period, ≤50 chars
  - Body (optional, English): explain *why*, not what - the diff shows what
- Good: `feat(auth): add jwt middleware`. Bad: `update auth and fix ui and fix
  bug` (split into 3).
- **Branching & merging - never commit or push directly to main/master:**
  - Work on feature branches named `type/short-description` (e.g.
    `feat/logging-module`, `fix/collector-timeout`).
  - Merge to main only via PR, even solo - the PR diff is the review gate,
    especially for agent-written code.
  - Never merge a PR yourself unless the owner asks; open it and hand over for
    review.
- Don't accumulate unrelated edits and lose the ability to roll back
  individually. Don't commit just to commit - meaningful units only.

Note: For solo prototypes or throwaway scripts, group commits loosely if it
slows you down. The point is reversibility, not ceremony. The machine-checkable
core (main/master block + header format) is enforced by the commit-guard hook rule.
