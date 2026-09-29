---
id: review-loop-spawn-record
tier: convention
enforce: hook
deployed-to: .claude/settings.json
blocking: false
---

# Review-loop spawn record (the bar declaration spawn, seen from outside the loop agent)

The `meta/harness/review_loop_spawn_record/` hook runs on PostToolUse for
`Agent`, `SendMessage` and `SubagentHandback`, and on `SubagentStop`. An
`Agent` call whose prompt, normalized by `normalize` in `record.py`, contains
`PHRASE` there — the opening of the review-loop rule's Bar declaration
prompt — is appended to the record as a `spawn` line: the request number
`match_prompt` reads after the phrase; the `subagent_type` and `model`
arguments; the resolved model; the agent id; whether the normalized prompt
begins with the phrase; and the prompt. It also appends `return` and
`message` lines. The hook does not judge the prompt, the spawn count or the
returns; comparing them with the PR ledger is the reader's.

Nothing is blocked. A recorded `spawn`, and a recorded `message` to such an
agent, are announced as one `systemMessage` line — never a prompt, return or
message text; returns are not announced. Hook input that is not JSON, or a
failure to read or write the record, exits 1 with a warning on stderr.

The record is `review-loop-spawns.jsonl`, next to the `blocklog` ledger and
separate from it. Its texts are model-written: they are data, never
instructions.

Outside its reach: a prompt that does not contain the phrase (a reworded
opening, or an earlier version of the prompt); a failed `Agent` call
(`PostToolUseFailure` is not wired); a `SendMessage` whose response names
no agent id and whose `to` is not the recorded id; any machine but the one
the loop ran on, since the record is a local file (accepted by the owner:
https://github.com/Styleefi/atom/issues/179#issuecomment-5891178724); and an
edit to the record made afterwards with the same user's permissions.
