---
id: review-loop-reminder
tier: convention
enforce: hook
deployed-to: .claude/settings.json
blocking: false
---

# Review-loop reminder (the pre-hand-over review obligation and its re-supply)

A PR is handed over to the owner for review only after a review loop (per
review-loop) has reached its exit observation on the PR's current HEAD.

An owner waiver is an explicit owner statement for that specific PR; the
agent records it in the PR body as one line (`Review loop: waived by owner —
<reason>`) before hand-over. A waiver covers one PR only and does not carry
to the next.

The obligation lives here, not in the root `CLAUDE.md`: an always-loaded
sentence would cost every turn for a once-per-PR event (owner, 2026-10-26).
commit-discipline's "open it and hand over for review" is unchanged. (Origin: every PR up to 2026-07-25 merged
without an independent review pass, and every loop since #112 ran from
memory — #27.)

The `meta/harness/review_loop_reminder/` PostToolUse hook re-supplies the
obligation at the one moment it can see: when a Bash command containing a
`gh pr create` (or `gh pr new`) segment without `--help`/`-h`/`--dry-run`
has exited 0 (a background call: has been launched), it injects one constant line (`REMINDER` in
`reminder.py`) as `additionalContext`, restating the obligation, that the
passes are fresh-context subagents on the model the owner names, that the PR
is reported to the owner as awaiting its review loop rather than ready for
merge, and that a waiver is recorded in the PR body. Nothing is blocked;
every failure path fails open.

Outside its reach (the module docstring is the SSOT for the list): a
hand-over in another session or many turns after creation; shell semantics —
`gh pr create` inside `bash -c`, a script, an alias or a non-Bash tool,
behind global flags or prefixes, in backticks, through `gh api`, with
`--help`/`-h`/`--dry-run` in a value position, after an unbalanced quote on
the same line, or after a comment line ending in a backslash (#52 freeze),
while text bash would not run (a comment, a heredoc body, a line inside a
quotation spanning lines, `false && gh pr create`) still fires, as does a
create whose `--help`/`-h`/`--dry-run` comes after an argument spanning
lines; a foreground command that exits
non-zero (PostToolUse does not fire — a failed create, or a create followed
by a failing segment, gets no reminder; a background call is reminded at
launch whatever it exits); `glab mr create` until #10; whether a loop
actually ran; `gh pr ready`.
