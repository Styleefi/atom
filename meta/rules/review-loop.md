---
id: review-loop
tier: convention
enforce: skill
deployed-to: .claude/skills/review-loop/SKILL.md
---

# Review loop protocol

A fix→review→fix loop on agent-written code does not terminate on its own:
every fix commit is new code, and an adversarial review essentially always
finds something on a non-trivial diff. This protocol gives the loop a
declared severity bar, a ledger, divergence tripwires, and an observed — not
declared — exit. (Origin: PR #40 converged 10→5→4→2→1→0; PR #46 diverged
1→3→7→4 and was reverted. Proposed in #41, sharpened by IgorGanapolsky's
comment there.)

## Scope

Applies to every PR review loop — any review whose findings lead to fix
commits on the PR, regardless of review method (multi-agent or single
reviewer). One loop per PR at a time. The loop's review procedure must
include a verification step; "verified" below means a finding passed it
(test reproduction is not required). This rule binds the agent running the
loop, never the owner.

## Severity bar

- Before round 1, declare in the ledger the concrete defect classes that
  count as above-bar for this PR, plus one or two below-bar examples.
  Restating an abstract formula is not a declaration. Guide: "a finding with
  a realistic scenario reproducing the defect class this PR exists to fix,
  or an equally harmful malfunction."
- Floor: the declared classes MUST include failure of the PR's purpose (the
  issue it closes, or the PR body's stated goal).
- Scale the bar to the diff's behavior surface, not its line count.
  (Measured: a +1128-line PR, #54, converged in one round while a +210-line
  one, #75, took five — rounds are driven by the inference surface touched
  and by prose-precision findings, not by size.) For a diff that changes
  no runtime behavior (rule, skill, CLAUDE.md and CLAUDE.template.md text
  specifies agent behavior, so a diff to it never qualifies),
  prose-precision findings are above-bar only when they would mislead a
  later session about behavior. For a diff that changes no runtime
  behavior, this behavior gate takes precedence over the purpose-failure
  floor: a prose finding that would not mislead a later session about
  behavior is below-bar even when it is a failure of the PR's purpose.
- Triage = matching: a verified finding is above-bar iff its failure
  scenario matches a declared class, subject to the accident test below.
  The bar applies at triage only — never narrow the reviewers' search
  with it.
- When matching a finding whose failure scenario requires a hypothetical
  repo state that a guard artifact (a checker, hook, test, or CI step) is
  expected to defend against — as opposed to a defect that manifests in
  the reviewed code as written — ask first whether that state can arise
  by accident: typo, bad merge resolution, mirrored copy-paste, forgotten
  partial edit. A state producible only by a deliberate, PR-visible act
  is a candidate for a declared scope boundary (Divergence trigger
  section): propose it to the owner instead of triaging it above-bar —
  unless the declared bar explicitly includes deliberate acts, or the
  guard's own documentation names that specific act as in scope to
  detect. Until the owner decides, the finding is recorded in the ledger
  as pending owner decision — counted in the above-bar total, not fixed
  in-PR, and blocking the exit observation as an unresolved case; on
  acceptance the recorded count is corrected downward and later
  trigger-(a) comparisons use the corrected number. (Measured: PR #86
  round 4 nearly added guard code against CI-disabling conditionals — a
  state no accident produces.)
- Raising the bar (adding a class) needs only a ledger entry through
  round 2 (an interrupted pass does not consume a round); from round 3 on
  it requires owner approval. A
  serious finding matching no class is handled by adding its class, under
  the same approval requirement; a raise pending owner decision blocks
  the exit observation as an unresolved case. On every raise,
  re-match the ledger's past triage records — findings that now match return
  to the above-bar lane (they join the fix backlog but never the divergence
  count). Lowering the bar requires owner approval. Uncertain matches
  escalate to the owner.
- If a review ran before any declaration, that pass counts retroactively as
  round 1, but the bar and ledger must exist before the first fix commit,
  with round 1's records back-filled.

## Rounds

- A round = one review pass (discovery → verification → triage) over the
  current scope. A pass whose above-bar findings get fixed is a fix round —
  one round no matter how many commits the fix splits into. A pass with zero
  above-bar findings is the exit observation; there is no separate ceremony.
- Scope: round 1 reviews the full PR diff; round N reviews the diff since
  the HEAD of the last completed pass. A completed pass moves this baseline
  even when its exit observation was invalidated; an interrupted pass does
  not. After a rebase or force-push, the next pass reviews the full PR diff
  again (round and checkpoint counters keep running).
- Reviewers may read anything; a finding enters the loop iff its causal
  chain includes the new diff — including new code that triggers or exposes
  a latent defect ("the root cause is pre-existing" is not an exemption).
  Whether a fix commit actually removed its target finding is always in
  scope for the next pass. Defects unrelated to the new diff are filed as
  issues immediately, even above-bar-grade ones, and do not block this PR.
- A fix that changes a documented behavior or semantic — or the wording
  that describes one — must, in the same commit, align every live copy
  of that description repo-wide. The sweep is a grep, not a full read:
  search the repository for the description's key phrases and a few
  likely variants, in every natural language the repo writes in (e.g.
  English rules, Korean docstrings), including non-markdown vessels
  (docstrings, comments, skills, templates), then align the hits that
  are copies of that description — historical records (changelogs,
  release notes) and vendored third-party code are exempt. A surviving
  sibling copy is a recurrence waiting to happen. (Measured: PR #86
  fixed one README line while a second copy in the same file returned as
  the next round's finding.)

## Ledger

One ledger comment per PR, created before round 1 and updated every round.
It is the loop's single source of truth — a later session resumes from it
alone. It records: the bar declaration; the review procedure in use
(changing it needs owner approval, recorded here); per round, the verified
findings → matched class → assigned lane, the above-bar count, and the class
names each fix addresses; owner-accepted trade-offs (one-line rationale,
recorded at decision time); links to filed issues.

Trade-off acceptance is the owner's decision — the agent only proposes. A
finding matching a recorded trade-off is closed at triage (not counted, not
filed) unless it carries new evidence, which makes it a normal finding.

## Triage lanes

Only above-bar findings re-enter the loop as in-PR fixes, with one narrow
exception: a below-bar prose finding may ride an above-bar fix commit
when the fix independently requires touching the finding's subject —
same function, same comment block, same table row or list item;
everything else waits for round-end disposition. A ridden finding is
recorded in the ledger under its fix, not filed as an issue. Bundled
prose is part of the next round's review surface, and unrelated bundling
slows convergence. (Measured: a comment bundled into a PR #86 fix commit
became the next round's finding.) Below-bar findings
with a verified failure scenario or a concrete improvement are filed as
issues per the issue-workflow rule — bundled by defect class, at round end,
with provenance (PR, round) and the verified scenario. Style preferences and
speculation stay in the round report. Duplicate prevention is the
issue-duplicate-guard hook's job.

## Divergence trigger

Stop before the next fix commit and escalate to the owner when either:

- **(a) quantity** — a fix round's above-bar count does not decrease versus
  the previous comparable fix round. A comparison is valid only when the
  round's scope is equal to or narrower than its predecessor's; a
  scope-widening round (post-rebase full diff, resumption after a revert)
  restarts the comparison instead of being judged by it. Findings returned
  by a bar raise do not count here.
- **(b) recurrence** — a defect class recorded as fixed in the ledger is
  found again in a later round.

The escalation report follows the checkpoint format plus: a classification
of what grew (defects introduced by the fix / previously masked defects now
exposed / a bar raise changing the counting basis) and a fix-altitude
diagnosis (repeat instance fixes / class-level defense / structural
redesign / declared scope boundary). In that diagnosis, "write the prose
more precisely" counts as a repeat instance fix, not a class defense (PR
#75 rewrote prose four rounds in a row, each recurrence one layer down;
the class closed only when the convention moved into a test). A declared
scope boundary applies when the defect class lies outside what the
artifact under review is meant to defend — because the artifact cannot
detect or prevent the class by its own means, or because reaching it
requires a deliberate, PR-visible act (Severity bar, accident test); the
class may still be reachable in the world. Closing it means proposing a
trade-off (the owner decides, per the Ledger section) and, once
accepted, writing the boundary into that artifact's own documentation
(docstring, README entry), citing the accepting decision (PR or issue
link) — prose exactly as wide as the actual defense. Later findings of
the class close at triage on the Ledger section's terms only when that
citation is present; new evidence makes them normal findings again.
(PR #86 chased one class down four layers — regex boundary → token →
shell control flow → YAML conditionals — and closed it only this way.)
The trigger mandates the diagnosis, never a particular remedy.

## Checkpoint

When a pass yields above-bar findings after three fix rounds have already
run — it would start a fourth — stop before its first fix commit and report
to the owner: the per-round
above-bar trend, remaining above-bar findings, filed issues, rough cost so
far, and the options — continue (optionally scoped, e.g. "fix these two and
stop"), stop and file the residue, or rethink the fix altitude — with a
recommendation. After a "continue", every subsequent fix round needs the
same approval before its fixes begin. If the divergence trigger fires in the same round, merge
both into one report.

## Ending the loop

Exactly two endings:

1. **Observed exit** — never declared. A pass scoped per the Rounds
   section — so every fix commit has been covered by a completed pass — run
   with the procedure recorded in the ledger, finds zero above-bar findings,
   with no unresolved borderline or pending-decision case and no above-bar
   backlog (including findings returned by a bar raise). The observation is
   valid only if the reviewed HEAD is still the PR's HEAD when the pass
   completes — any new commit, whoever pushed it, invalidates the
   observation (not the pass) and requires a new pass. Below-bar findings
   from this pass are filed, then the loop ends.
2. **Owner decision** — at any point, typically in response to a checkpoint
   or escalation report.

## Tooling

The protocol is tool-agnostic: run each pass with the project's review tool
(e.g. `/code-review`) — the same one throughout the loop, as recorded in
the ledger.
