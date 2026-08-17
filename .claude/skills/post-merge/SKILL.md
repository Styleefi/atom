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
- Pin every gh/glab call to the **origin** repository explicitly: child
  projects carry two same-forge remotes (origin + upstream), and default
  repo resolution can silently pick upstream's PR with the same number.
  gh and glab commands: `-R <origin-slug>`. `glab api` has no `-R` and
  default-resolves the repo — pin it by writing the URL-encoded origin
  project path into the endpoint itself: from `git remote get-url origin`
  (never a default-repo guess) take everything after the host, drop the
  trailing `.git`, keep every path segment (subgroups included), and
  encode each `/` as `%2F` — `git@host:group/sub/proj.git` and
  `https://host/group/sub/proj.git` both yield `group%2Fsub%2Fproj`,
  used as `projects/group%2Fsub%2Fproj/...`; never a bare `projects/:id`.
- If any git command fails, read the actual error and report it. Never
  escalate to a force flag — the only force-spelled flags this skill
  ever uses are step 6's two deletions (local `-D`, remote
  `--force-with-lease`), each allowed only after the forge confirms
  MERGED **and** the tip being deleted equals the forge's recorded
  head SHA. The worst outcome of any unlisted failure must be
  "stop + report".
- Note the branch you started on before switching anything. On any abort,
  the report must name the current position and the original branch, and
  offer to return — never leave the owner stranded somewhere unexpected
  without saying so. (No automatic rollback: returning is itself a
  checkout, so propose it, don't assume it.)
- Never write to issues unprompted; report in chat only. Recording
  follow-up scope on an issue is the owner's call (issue-workflow) — do
  it only when the owner asks.

## Checklist

1. **Identify the merged PR/MR.** From conversation context; otherwise
   the most recently merged:
   `gh pr list --state merged --limit 50 --json number,title,mergedAt`
   (pick the largest `mergedAt` — gh orders by creation, so a
   recently-merged old PR can even fall off a short page) /
   `glab mr list --merged -o merged_at -S desc` (first row). A
   fallback-identified PR is a guess: name it and get the owner's
   confirmation before proceeding. If several candidates are plausible,
   ask instead of guessing.
2. **Verify it is actually merged.**
   `gh pr view <n> --json state,mergeCommit,baseRefName,headRefName,headRefOid,body,closingIssuesReferences`
   / `glab mr view <iid> --output json` (plain `mr view` prints text and
   exposes none of these fields), reading `state`, `merge_commit_sha`,
   `squash_commit_sha`, `sha`, `source_branch`, `target_branch`,
   `description`. If not MERGED, stop. Record two values for the later
   steps:
   - `<head-sha>` — the head tip the forge merged: gh `headRefOid` /
     glab `sha`. Step 6 compares every deletion against it.
   - `<ci-sha>` — the commit step 3 CI-checks: gh — the `.oid` of the
     `mergeCommit` object in the response (`mergeCommit` is an object,
     not a SHA; the `--json` field name stays `mergeCommit`); glab —
     `merge_commit_sha`, null on a squash merge → `squash_commit_sha`,
     both null (fast-forward merge) → `sha`, which is then itself the
     target-branch tip.
3. **Check CI on the merge commit** — read-only, before any state change;
   one-line status, no polling. Query by the merge commit SHA
   (`gh run list -c <ci-sha>` /
   `glab api "projects/<url-encoded-project-path>/pipelines?sha=<ci-sha>"`),
   never by branch — a branch query can pick up an unrelated run.
   - Terminal state other than success (failed, cancelled, …): surface it,
     skip steps 5–6 ("cleanup skipped pending CI"), propose fixing first.
   - Run exists but is not terminal (queued, in_progress, waiting for a
     manual approval): report the status and ask the owner whether to
     proceed with cleanup now or re-check later. Still no polling.
   - Zero runs, repo has previous runs (`gh run list --limit 1` /
     the pipelines endpoint without the `sha` filter): the run is
     likely still being created — same gate as the non-terminal row:
     report and ask the owner whether to proceed with steps 5–6 now or
     re-check later. Still no polling.
   - Zero runs, no CI records at all (e.g. no runner configured):
     report; not a failure — proceed.
4. **Check working tree and repo state.** Dirty tree → stop and ask;
   never stash silently. Merge/rebase/bisect in progress → stop.
   Detached HEAD → stop and ask; record the detached SHA as the starting
   position for the abort report. Current branch neither the merged head
   branch nor the base branch → stop and ask before switching away —
   same stop-and-wait gate as the dirty tree, never silently change the
   owner's working position.
5. **Sync the base branch.** `git checkout <baseRefName>` then
   `git pull --ff-only origin <baseRefName>` — name the remote and
   branch explicitly; dual-remote checkouts may track upstream. Do not
   assume main — use the PR's actual base.
   If fast-forward is impossible (local divergence), stop and report:
   this skill must never create a merge commit.
6. **Delete the merged branch — default-deny.** Only if the head branch
   name matches `<type>/...` with type in commit-discipline's type set
   (the rule is always in context — read it, don't re-derive the list).
   Any other name (develop, release/*, …) is presumed long-lived: leave
   it and report; delete it only if the owner explicitly confirms in
   this conversation that the branch is disposable. The deny is a
   default, not a ban — but the call is the owner's, never the skill's.
   - Local: if the branch exists, `git branch -d <head>`; absence is
     fine (work done on another machine). If `-d` refuses while the
     forge says MERGED, compare `git rev-parse refs/heads/<head>` (the
     full ref — a bare name resolves a same-named tag first) with
     `<head-sha>`:
     - equal → the refusal is a squash/rebase-merge artifact; `-D` is
       allowed then, and only then.
     - different → the local branch holds commits the forge never saw
       (extra unpushed work, or an unrelated same-named branch — fork
       PRs make that collision easy). Stop + report; never `-D`.
   - Remote: `git ls-remote origin refs/heads/<head>` — spell the full
     ref; a bare name tail-matches (`feat/foo` also hits
     `refs/heads/wip/feat/foo`). Absent is fine (a fork PR's branch
     never lives on origin; auto-delete may already have run). Present
     with the listed SHA equal to `<head-sha>` → delete atomically:
     `git push --force-with-lease=refs/heads/<head>:<head-sha> origin :refs/heads/<head>`
     — the lease re-checks the tip server-side, so a push landing
     between check and delete fails the delete instead of being
     destroyed. Listed SHA different, or the lease rejects → the ref
     moved since the merge (reused name, new push) → stop + report.
     Then `git fetch origin --prune` — name the remote; a bare fetch
     may follow the checked-out branch's upstream instead.
7. **Audit linked issues — expectation vs actual, per reference.**
   - Expected-to-close set: from the forge's own linkage data
     (`closingIssuesReferences` from step 2 /
     `glab api "projects/<url-encoded-project-path>/merge_requests/<iid>/closes_issues"`
     — `<iid>` is a literal substitution like `<n>`; glab does not
     auto-fill `:iid`) — do not re-implement closing-keyword parsing
     from the body. Both responses are arrays of issue objects, not
     numbers: take each object's `number` (gh) / `iid` (glab).
   - Each expected-to-close issue must now be CLOSED. Still OPEN → report
     the likely cause (e.g. merged into a non-default branch) instead of
     silently passing.
   - Non-closing mentions in the PR body (Refs #n — the body is step 2's
     `body` / `description`): the issue should still be OPEN. Surface the
     issue and its recent comments so the owner can confirm the remaining
     scope is recorded — do not claim that verdict yourself.
   - No linked issue at all: flag it (issue-workflow expects completed
     work to link its issue) — a flag, not an error.
8. **Report.** Remaining backlog (`gh issue list` / `glab issue list`) and
   which issues this merge unblocked.

Every step must be individually idempotent: run against an already-cleaned
merge, each step verifies and passes (branch absent, issue already closed)
rather than failing or repeating destructive work.
