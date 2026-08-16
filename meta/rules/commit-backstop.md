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
  with both tips and every offending SHA, and routed to the owner. The report
  prescribes no surgery: recovery lives in this rule (below), because a
  procedure printed into an agent's context carries assumptions about the
  repository that the hook cannot check, and rewinding a protected branch is
  the owner's call under the plan-deviation rule.
- New unpublished non-merge commits reachable from `HEAD` must satisfy the
  Conventional Commits header; violations are reported with advice that
  prescribes only what it can verify — amend a listed commit when the command
  that just ran created it and it is `HEAD`, otherwise leave history alone,
  hand the SHAs to the owner and hold off pushing. When a protected-branch
  report accompanies it the amend clause is dropped and this lane routes only —
  the hook does not check whether the amend target is entangled with the branch
  violation, and it does not prescribe what it cannot verify.
  Merge commits are excluded
  structurally (`--no-merges` filters by parent count before any subject is
  read); separately, three subject prefixes git generates (`Revert "`,
  `fixup! `, `squash! `) are exempt from the header check, matched by prefix
  regardless of who wrote them.

Each violation is normally reported once — a protected-branch advance by its
recorded tip, a header violation by the `checked` SHA list, both kept in
`<git-common-dir>/atom-commit-backstop.json`; the module docstring's non-claims
list the paths where a report repeats. Reports carry SHAs and reasons
only — commit subjects are never echoed (prompt-injection surface). All
failure paths are fail-open; repositories without a remote are skipped
entirely. `ATOM_COMMIT_OVERRIDE=1` in the command suppresses evaluation for
that one call while still recording tips. Known non-claims (commit+push in a
single command, out-of-cwd repositories, pre-first-observation history) are
listed in the module docstring — that layer belongs to server-side branch
protection.

## Recovering a protected branch (owner decides; the agent does not)

First decide whether the report was noise. The hook compares only against
remote `main`/`master` refs that exist locally, so its view is incomplete when
none is present or the project publishes under another name (`--single-branch`
or pruned clones, `git pull <URL>`, a differently named default branch —
`git remote show <remote>` prints it as "HEAD branch"). Whether a reported
commit is actually published is decidable with one fetch:

    git fetch <remote> <branch>        # works in a --single-branch clone too
    git merge-base --is-ancestor <sha> FETCH_HEAD

Exit 0: the commit is already on the remote's `<branch>` — the report was the
hook's local blind spot, nothing needs recovering, and prefixing the next
advancing command with `ATOM_COMMIT_OVERRIDE=1` silences the repeat. Exit 1:
the commit is not published there — a real advance for the owner to judge,
however the clone is configured. A branch that was simply never pushed is the
case the hook exists to catch, not an exemption from it.

Otherwise, using the SHAs from the report:

1. Preserve the work first — skipping this loses the commits. On the branch
   now: `git checkout -b <type/short-description>`. Otherwise:
   `git branch <type/short-description> <current tip>`.
2. `git branch -f <branch> <previous tip>`. If that fails with `cannot force
   update ... used by worktree at <path>`, and `<path>` is the current
   directory, step 1 was skipped — go back to it; if `<path>` is another
   worktree, run `git reset --keep <previous tip>` there. Older git says
   `Cannot force update the current branch.` for that first case.
3. `git switch <type/short-description>` unless step 1 already left you there,
   then continue on the rescue branch and merge via PR.
