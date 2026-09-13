---
id: review-loop-trailer-check
tier: convention
enforce: hook
deployed-to: .claude/settings.json
blocking: false
---

# Review-loop trailer check (mechanical re-supply of the trailer bullets)

The `meta/harness/review_loop_trailer_check/` PostToolUse hook looks at up
to fifty non-merge commits that reached HEAD since its last observation,
leaving out those already on `main`/`master`. A commit carrying a
`Review-loop:` trailer is checked for what the review-loop rule's trailer
bullets require: a `Prose:` trailer valued `attacked` or `none`, no message
body beyond the trailers, and — unless that trailer says `attacked` — no
added prose line. Which added lines count as prose, and which edits
(relocation, reflow, clause deletion, backticked-identifier renames) do not,
is fixed by the fixtures in its `tests/test_prose_lines.py`. On a branch
whose commits off `main`/`master` include a `Review-loop:` commit, a commit
that this Bash call's own `git commit`, `git revert` or `git cherry-pick`
created without that trailer is reported as well.

Nothing is blocked: findings reach the model as one line of constant reasons
keyed by short SHA — never a trailer value, a subject or a line of prose —
and each is appended to the `blocklog` ledger as a `report` event.
`report` reasons: `missing-review-loop-trailer`, `missing-prose-trailer`,
`malformed-review-loop`, `malformed-prose`, `none-with-new-prose`,
`body-beyond-trailers`. Every failure path fails open, and a SHA the state
file remembers as checked is not reported again.

Outside its reach: a loop commit that omits both trailers while no other
loop commit exists on its branch, and sentences — the check is per line, so
counting or judging prose stays with the next review pass.
