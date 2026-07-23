---
id: answer-first-reminder
tier: convention
enforce: hook
deployed-to: .claude/settings.json
---

# Answer-first reminder (mechanical re-supply of answer-first)

The `meta/harness/answer_first_reminder/` UserPromptSubmit hook inspects each
incoming owner message with a lightweight question heuristic — question marks
(`?`/`？`, fenced code blocks stripped first), sentence-final Korean
interrogative endings (까/까요/나요/가요/은가/는가/인가/니/냐; markless
~야/~지 deliberately excluded as indistinguishable from declaratives), and
English interrogative sentence starters — and, only when question-shaped,
injects one constant line into context restating the answer-first contract:
the answer is this turn's deliverable; execution resumes after the owner
responds.

Nothing is ever blocked and every failure path fails open: a false positive
costs one harmless injected line; a false negative degrades to the claude-md
status quo. The stdout injection channel carries only the constant reminder,
never payload content. The intent-level rule stays in the answer-first
principle — this hook only moves recognition from probabilistic recall to
deterministic re-supply at the moment the message arrives.
