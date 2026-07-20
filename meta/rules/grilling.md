---
id: grilling
tier: convention
enforce: skill
deployed-to: .claude/skills/grilling/SKILL.md
---

# Grilling — decision-tree interview protocol

When a plan, design, or idea still has unresolved decisions, run this
interview before building.

Interview the owner relentlessly about every aspect of it until you reach a
shared understanding. Walk down each branch of the decision tree, resolving
dependencies between decisions one-by-one. For each question, provide your
recommended answer.

Ask the questions one at a time, waiting for the owner's feedback on each
question before continuing. Asking multiple questions at once is bewildering.
Prefer plain prose questions in the conversation over structured option
dialogs (AskUserQuestion).

If a *fact* can be found by exploring the environment (filesystem, tools,
etc.), look it up rather than asking. The *decisions*, though, are the
owner's — put each one to them and wait for their answer.

Do not act on it until the owner confirms a shared understanding has been
reached.

Adapted from [mattpocock/skills](https://github.com/mattpocock/skills)
`grilling` (commit 9603c1c) —
[MIT](https://github.com/mattpocock/skills/blob/9603c1cc8118d08bc1b3bf34cf714f62178dea3b/LICENSE),
Copyright (c) 2026 Matt Pocock.
