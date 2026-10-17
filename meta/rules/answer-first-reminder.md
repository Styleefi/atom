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
- **Read-request shape** — the text is split at . ! 。 ！ ? ？ and newline
  characters; trailing whitespace and the characters ' " ’ ” ‘ “ ( ) [ ] < > …
  ~ are stripped from each piece; the last non-empty piece is the clause. That
  clause ends with a request ending (해 줘, 해줘, 주세요, 하자, 보자, 도록, …)
  and the last five characters of what precedes the ending, whitespace before
  the ending removed, contain a read-verb stem (설명, 보고, 확인, 검토, 추천,
  판단, …). The ending match is anchored to the end of the clause and can take
  a syllable from the stem: "답해 주세요" fires, "답해 줘" does not (the match
  해 줘 takes the 해). 답해 stays in the list for fidelity to the census. The
  lists are the #149 census classifier's 17 endings and 21 stems, plus five
  stems (제시 판단 추천 찾아 받아) absent from the census list that the
  2026-10-16 measurement (#37) found on at least two read-requests each;
  one-off verbs are left out.

Deliberate limits, all fail-open. The hook sees shape only, so whatever has
the shape gets the reminder even when it asks for nothing to read: a
question-shaped go-request ("푸시할까?"), a go-request whose last clause
carries a listed stem before the ending ("에러 로그 보고 고쳐 줘", "PR 받아줘"),
and a statement ending in 할게 or bare 해 with a stem in the window ("내가
확인할게", "나도 그렇게 생각해"). Excluding go-requests is the model's call
under the principle; that is why the reminder is an observation ("looks like
a question or a read-request"), not an assertion. Whatever lacks the shape
is missed even when it asks for something to read: markless ~야/~지 and -나
(indistinguishable from declaratives such as 하나, 만나), a read-request
outside the last clause, a verb not in the stem list, a stem outside the five
characters that remain before the stripped ending ("설명을 좀 자세히 해 줘": the
five are 좀 자세히), an ending not in the ending list ("설명해줘요", "부탁드려요",
"바랍니다"), or an ending followed by characters the noise strip does not
remove ("확인해 줘ㅋㅋ").

Nothing is ever blocked and every failure path fails open: a false positive
costs one injected line, and a false negative degrades to the claude-md
status quo. The stdout injection channel carries only the constant reminder,
never payload content. The intent-level rule stays in the answer-first
principle — this hook only moves recognition from probabilistic recall to
deterministic re-supply at the moment the message arrives.
