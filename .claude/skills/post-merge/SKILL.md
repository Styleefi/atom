---
name: post-merge
description: Load when a PR/MR has just been merged and cleanup should run — explicit /post-merge, or natural-language merge-completion messages such as "머지 완료했어", "merge done", or a cleanup request right after a merge. Verifies the merge, checks CI on the merge commit, syncs the base branch, deletes the merged topic branch (default-deny), audits linked issues, and reports the unblocked backlog.
---

# post-merge

Post-merge cleanup checklist. Functional skill — this file is the whole
procedure. Operates on the repository in the current directory, on either
forge (gh for GitHub, glab for GitLab).

## Safety contract (applies to every step)

- Never merge a PR/MR — merging stays with the owner (commit-discipline).
- Pin every gh/glab call to the **origin** repository explicitly
  (`-R <origin-slug>`): child projects carry two same-forge remotes
  (origin + upstream), and default repo resolution can silently pick
  upstream's PR with the same number.
- If any git command fails, read the actual error and report it. Never
  escalate to a force flag — the single exception is `-D` in step 6, and
  only after the forge confirms MERGED. The worst outcome of any unlisted
  failure must be "stop + report".
- Note the branch you started on before switching anything. On any abort,
  the report must name the current position and the original branch, and
  offer to return — never leave the owner stranded somewhere unexpected
  without saying so. (No automatic rollback: returning is itself a
  checkout, so propose it, don't assume it.)
- Never write to issues; report in chat only.

## Checklist

1. **Identify the merged PR/MR.** From conversation context; otherwise the
   most recent of `gh pr list --state merged` / `glab mr list --merged`.
   If several candidates are plausible, ask instead of guessing.
2. **Verify it is actually merged.**
   `gh pr view <n> --json state,mergeCommit,baseRefName,headRefName,closingIssuesReferences`
   / `glab mr view <n>`. If not MERGED, stop.
3. **Check CI on the merge commit** — read-only, before any state change;
   one-line status, no polling. Query by the merge commit SHA
   (`gh run list -c <mergeCommit>` / GitLab: the pipeline for that SHA),
   never by branch — a branch query can pick up an unrelated run.
   - Terminal state other than success (failed, cancelled, …): surface it,
     skip steps 5–6 ("cleanup skipped pending CI"), propose fixing first.
   - Run exists but is not terminal (queued, in_progress, waiting for a
     manual approval): report the status and ask the owner whether to
     proceed with cleanup now or re-check later. Still no polling.
   - Zero runs: distinguish "pending" (the repo has previous runs, so the
     workflow is likely still being created — report in-progress) from
     "no CI records at all" (e.g. no runner configured — report; not a
     failure).
4. **Check working tree and repo state.** Dirty tree → stop and ask; never
   stash silently. Merge/rebase/bisect in progress → stop. If the current
   branch is neither the merged head branch nor the base branch, tell the
   owner before switching away — never silently change their working
   position.
5. **Sync the base branch.** `git checkout <baseRefName>` then
   `git pull --ff-only`. Do not assume main — use the PR's actual base.
   If fast-forward is impossible (local divergence), stop and report:
   this skill must never create a merge commit.
6. **Delete the merged branch — default-deny.** Only if the head branch
   name matches `<type>/...` with type in the commit-discipline set
   {feat, fix, refactor, test, docs, chore, build, perf, style}. Any other
   name (develop, release/*, …) is presumed long-lived: leave it and
   report; delete it only if the owner explicitly confirms in this
   conversation that the branch is disposable. The deny is a default, not
   a ban — but the call is the owner's, never the skill's.
   - Local: if the branch exists, `git branch -d <head>`; absence is fine
     (work done on another machine). If `-d` refuses while the forge says
     MERGED, the history differs because of a squash/rebase merge — `-D`
     is allowed then, and only then.
   - Remote: check `git ls-remote --heads origin <head>` first (a fork
     PR's branch never lives on origin; auto-delete may already have
     removed it). Delete only if present:
     `git push origin --delete <head>`. Then `git fetch --prune`.
7. **Audit linked issues — expectation vs actual, per reference.**
   - Expected-to-close set: from the forge's own linkage data
     (`closingIssuesReferences` from step 2 / GitLab
     `glab api projects/:id/merge_requests/:iid/closes_issues`) — do not
     re-implement closing-keyword parsing from the body.
   - Each expected-to-close issue must now be CLOSED. Still OPEN → report
     the likely cause (e.g. merged into a non-default branch) instead of
     silently passing.
   - Non-closing mentions in the PR body (Refs #n): the issue should still
     be OPEN. Surface the issue and its recent comments so the owner can
     confirm the remaining scope is recorded — do not claim that verdict
     yourself.
   - No linked issue at all: flag it (issue-workflow expects completed
     work to link its issue) — a flag, not an error.
8. **Report.** Remaining backlog (`gh issue list` / `glab issue list`) and
   which issues this merge unblocked.

Every step must be individually idempotent: run against an already-cleaned
merge, each step verifies and passes (branch absent, issue already closed)
rather than failing or repeating destructive work.
