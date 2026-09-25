# Review tiers

The canonical review policy for every repository and every coding agent (Claude
Code, Codex, Cursor). A repository's own `AGENTS.md` may map its areas onto these
tiers, name its tooling, or add stricter requirements; it may not weaken them.

## Classify the committed change

Classify the **committed** diff against the base branch, not the working tree.
Record the tier, the reason, the approximate substantive changed-line count, and
the base and head commits in the pull request. Reassess after every fix or base
update. Size can raise the minimum tier; it never lowers a risk-based tier.

| Tier | When |
| --- | --- |
| **Routine** | Isolated, low-impact work with no sensitive boundary: copy, docs, config with no behavior change, a contained bug fix |
| **Substantial** | A new UI surface or route, cross-module behavior, many behavior-changing files, a member- or customer-facing flow, or roughly 200+ substantive changed lines |
| **High-stakes** | Regardless of size: access control, authentication, sessions, privacy or visibility, payments, data migrations or destructive data changes, deployment or release tooling, or subtle concurrency and race conditions |

Escalate a tier for novel or uncertain behavior, and record why.

## What each tier requires

| Tier | Independent review before the PR | Local cross-lineage preflight | After the PR |
| --- | --- | --- | --- |
| Routine | One independent reviewer of the committed diff | Not used | Required CI and advisory feedback; **no automatic cross-lineage review** |
| Substantial | A stronger independent reviewer | Optional, for novel or uncertain behavior | Cross-lineage review only for a documented trigger, or a measurement sample chosen before any feedback is seen |
| High-stakes | Strong independent review; two independent lineages when the harness can launch both | Required when the other lineage's CLI is available | Advisory exact-head cross-lineage review; a human judges its evidence before merge |

**The independent pre-PR review is mandatory at every tier.** The session that
wrote the code does not count as its reviewer, and no local cross-lineage report
replaces it. Tell each reviewer the exact base and head commits, and that it is
read-only. It must name the head it examined.

**Cross-lineage** means the lineage that did *not* write the change: a Claude
reviewer for Codex/GPT-authored work, a Codex/GPT reviewer for Claude-authored
work. Cursor is a harness, not a lineage; declare the lineage of the model that
actually wrote the change. Two models of one lineage are not a cross-lineage review.

## Local cross-lineage preflight

A read-only review of an immutable snapshot of the committed diff, run before the
PR opens. It records the base commit, head commit, full-diff SHA-256, reviewer
identity, and a local report path.

- Use the repository's preflight helper where it has one, one per reviewing
  lineage: a Claude reviewer for Codex-authored changes, a Codex reviewer for
  Claude-authored changes.
- Where a repository has no helper and the tier requires one, record the gap in
  the PR and get human judgment on it before publication. `request-pov` in this
  collection is an outside opinion, not a snapshot preflight, and does not fill
  that gap.
- If the other lineage's CLI is unavailable for high-stakes work, record the
  limitation the same way.

## Staleness, delta review, and full re-review

Review evidence is valid only for the head it examined. New commits, a moved base,
or a moved PR head make it stale. Check freshness when a run completes **and**
immediately before creating the PR or merging.

- **Delta review:** only for a confined fix whose design, surface, permissions, and
  data boundary are unchanged. Show the reviewer both the change since the prior
  reviewed head and the full cumulative diff, and name both heads.
- **Full re-review:** required when a fix adds a surface, changes a permission or
  data boundary, changes the design, raises the tier, or invalidates an earlier
  assumption. Also required after a rebase or rewrite where the prior reviewed head
  is no longer an ancestor of the new one.

## Three kinds of evidence

Never let one stand in for another:

| Evidence | What it proves |
| --- | --- |
| **Local feedback** — a preflight or local exact-head report | A reviewer read that exact diff. It is unpublished and cannot be checked by others |
| **GitHub-published review** | A review exists on that head. Its lineage is a declaration, because every agent posts as the same human account |
| **CI-verified provenance** — where a repository has it | CI selected the reviewer lineage and model for that head |

**A run is not "reviewed" because its process exited.** Count a local run only
when its report shows a completed, attested review that is still fresh. Count a
published review only when it is attached to the expected head commit. Count CI
provenance only from the CI bot, naming the expected head and a reviewed outcome.

## Publication stays with the reviewer

A cross-lineage review is published by the reviewing agent, in its own session, so
that attribution is the reviewer's. Never have the authoring session post another
lineage's findings as a review.

Request a post-PR cross-lineage review in exactly two cases: the tier table above
calls for one (high-stakes, or substantial with a documented trigger or sample),
or the user explicitly asks. The tier requirement is itself sufficient
authorization to use the paths below. Routine PRs get none of them unless the
user asks.

- **A repository's CI cross-lineage workflow**, where one exists. Prefer it for
  high-stakes work, because CI verifies the reviewer's provenance.
- **Codex-authored PR → Claude review:** `agent-pr-review-link claude --start <full-pr-url>`,
  verified with `agent-pr-review-link claude --status --json <full-pr-url>`.
  `--follow-up` requests an exact-head re-review after fixes.
- **Claude-authored PR → Codex review:** `agent-pr-review-link codex --open <full-pr-url>`
  opens the Codex app with a read-only review request typed in. Nothing is sent
  until the user presses Enter, and a prefilled, unsent request is not a review.

## Failure recovery

Report a reviewer failure once, with the specific condition and the recovery step,
without implying the pull request itself failed.

| Condition | Recovery |
| --- | --- |
| Reviewer CLI missing or signed out | Record the access limitation; for high-stakes work, get human judgment on the gap |
| Run failed, timed out, or its supervisor died | Read its captured output, fix the cause, start a new run |
| Completed but unpublished, or not verifiable | Treat it as not reviewed; check the review target and head, then retry |
| Stale head | Delta or full re-review of the new head, as above |
