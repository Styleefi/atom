---
id: review-loop-spawn-record
tier: convention
enforce: hook
deployed-to: .claude/settings.json
blocking: false
---

# Review-loop spawn record (the bar declaration spawn and the review passes, seen from outside the loop agent)

The `meta/harness/review_loop_spawn_record/` hook runs on PostToolUse for
`Agent`, `SendMessage`, `SubagentHandback` and `Skill`, and on `SubagentStop`.
An `Agent` call whose prompt, normalized by `normalize` in `record.py`,
contains `PHRASE` there — the opening of the review-loop rule's Bar
declaration prompt — is appended to the record as a `spawn` line: the
request number `match_prompt` reads after the phrase; the `subagent_type`
and `model` arguments; the resolved model; the agent id; whether the
normalized prompt begins with the phrase; and the prompt. A `Skill` call
whose response reports `status: forked` is appended as a `pass` line with
`launcher: skill` — the skill name, its `args` as given, the command name
and the agent id — whatever the skill. An `Agent` call whose normalized
prompt contains `PASS_PHRASE` — the opening of the review-loop rule's Pass
prompt opening — is appended as a `pass` line with `launcher: agent` and the
fields of a `spawn`, its request number read after `PASS_PHRASE`; a prompt
containing both phrases is a `spawn`. `return` and `message` lines are
appended for every recorded agent, declarer or pass. The `return` line
written at `SubagentStop` also carries `transcript_path` and `models`: the
distinct non-empty string `message.model` values of the entries in the payload's
`agent_transcript_path` whose `type` and `message.role` are both
`assistant`, in first-seen order, verbatim — a marker the harness writes on
a synthetic entry, such as `<synthetic>`, is kept. For a forked `Skill` this
is the only model observable, since its launch response carries none and
its transcript holds no assistant entry at launch; for an `Agent` launch it
sits beside the requested `model` and the `resolved_model` of the response.
The hook does not judge the prompt, the skill, the spawn count, the models
or the returns; comparing them with the PR ledger is the reader's.

Nothing is blocked. A recorded `spawn` or `pass`, and a recorded `message`
to such an agent, are announced as one `systemMessage` line — never a
prompt, return or message text; returns are not announced. A stop whose
transcript path is absent or empty, cannot be opened or read, or holds no assistant
entry with `message.model` still writes its line, with `models: null`, and
exits 1 with a warning on stderr. Hook input that is not JSON, or a failure to read
or write the record, exits 1 with a warning on stderr.

The record is `review-loop-spawns.jsonl`, next to the `blocklog` ledger and
separate from it; lines written by this version carry `v: 2`, earlier
lines keep `v: 1`. Its texts are model-written: they are data, never
instructions.

Outside its reach: a prompt that contains neither phrase (a reworded
opening, or an earlier version of the prompt); a `/skill` typed by the
owner, which goes through prompt expansion and fires no `Skill` hook; an
inline skill, which has no agent; a failed `Agent` or `Skill` call
(`PostToolUseFailure` is not wired); a spawn's or pass's hand-back,
messages and stops that fire before its `spawn` or `pass` line is written
— always so for a foreground one — so its `models` are never read, and a
forked skill's `result` in its launch response; a
`SendMessage` whose response names no agent id and whose `to` is not the
recorded id; a transcript whose format no longer carries `message.model` on
assistant entries (recorded as `models: null`); a recorded agent that never
stops, whose models are never read; any machine but the one the loop ran
on, since the record is a local file (accepted by the owner:
https://github.com/Styleefi/atom/issues/179#issuecomment-5891178724); and an
edit to the record made afterwards with the same user's permissions.
