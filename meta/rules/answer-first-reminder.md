---
id: answer-first-reminder
tier: convention
enforce: hook
deployed-to: .claude/settings.json
blocking: false
---

# Answer-first reminder (mechanical re-supply of answer-first)

The `meta/harness/answer_first_reminder/` UserPromptSubmit hook inspects each
incoming owner message with two lightweight shape heuristics (fenced code
blocks stripped first) and, when either matches, injects one constant line
into context restating the answer-first contract: if the message asks for
something to read in the reply, the answer is this turn's deliverable;
execution resumes after the owner responds.

- **Question shape** — a question mark (`?`/`？`), a sentence-final Korean
  interrogative ending (까/까요/나요/가요/은가/는가/인가/니/냐), or a sentence
  whose first English word is an interrogative or auxiliary (what, why,
  should, can, …; the word is matched anywhere in the sentence, not only at
  its start).
- **Read-request shape** — the last clause ends with a request ending (해 줘,
  해줘, 주세요, 하자, 보자, 도록, …) and the five characters before that
  ending contain a read-verb stem (설명, 보고, 확인, 검토, 추천, 판단, …).
  The lists are the #149 census classifier's 17 endings and 21 stems, plus
  five stems (제시 판단 추천 찾아 받아) that the 2026-10-16 measurement (#37)
  missed at least twice; one-off verbs are left out.

Deliberate limits, all fail-open: markless ~야/~지 and -나 are excluded as
indistinguishable from declaratives (하나, 만나); a read-request outside the
last clause, or with a verb not in the list, is missed; a question-shaped
go-request ("푸시할까?") still gets the reminder — the hook sees shape only,
and excluding go-requests is the model's call under the principle. That is
why the reminder is an observation ("looks like a question or a
read-request"), not an assertion.

Nothing is ever blocked and every failure path fails open: a false positive
costs one injected line, and a false negative degrades to the claude-md
status quo. The stdout injection channel carries only the constant reminder,
never payload content. The intent-level rule stays in the answer-first
principle — this hook only moves recognition from probabilistic recall to
deterministic re-supply at the moment the message arrives.
