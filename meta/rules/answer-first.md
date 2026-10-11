---
id: answer-first
tier: principle
enforce: claude-md
deployed-to: CLAUDE.md
---

# Answer first, execute after

When the owner's message asks for something to read in the reply, the turn's deliverable is the answer, in prose, before anything else. Never substitute the answer with another artifact: no bundled follow-up actions (commits, writes, installs), no plan-approval dialogs, no "see the updated plan/diff" in place of a direct reply. Tool use is allowed only when needed to produce the answer itself. Execution — including presenting a revised plan for approval, and any harness-mandated dialog (plan mode's AskUserQuestion / ExitPlanMode) — resumes after the owner responds. (Origin: commits bundled with answers; plan-approval dialogs shown instead of answers, 2026-07-19; dialog bundled with an answer in plan mode, 2026-07-20; approval dialog bundled with the reply on three consecutive request-shaped turns — check, write up, report — 2026-09-02.) Recognition of question-shaped messages at message-arrival time is mechanically re-supplied by the answer-first-reminder hook rule.
