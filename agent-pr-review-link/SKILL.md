---
name: agent-pr-review-link
description: >-
  Hand a pull request to the other AI lineage for review, and apply the global review
  tiers (routine / substantial / high-stakes). Use when classifying a change's review
  tier, when a tier calls for a cross-lineage review, when Rob explicitly asks Claude
  or Codex to review a PR, or when checking whether such a review is verified for the
  current head. Not for routine PRs without an explicit request, and not a
  replacement for the mandatory independent pre-PR review.
---

# Cross-lineage PR review handoff

`agent-pr-review-link` hands a pull request to the lineage that did **not** write
it, and verifies the result against the exact head. The policy that decides when
to use it is [REVIEW-TIERS.md](REVIEW-TIERS.md). Read it before choosing a tier.

## Workflow

1. Classify the committed change as routine, substantial, or high-stakes, per
   REVIEW-TIERS.md, and record the tier, reason, base, and head in the PR.
2. Complete the independent pre-PR review of the committed diff. It is required at
   every tier, and this helper does not replace it.
3. After the PR exists, request a cross-lineage review only when the tier calls
   for one or Rob asks:
   - Codex-authored PR, Claude reviews:
     `agent-pr-review-link claude --start <full-pr-url>`, then
     `agent-pr-review-link claude --status --json <full-pr-url>`.
     After fixes, `--follow-up` requests an exact-head re-review.
   - Claude-authored PR, Codex reviews:
     `agent-pr-review-link codex --open <full-pr-url>`. This prefills a read-only
     review request in the Codex app; nothing is sent until Rob presses Enter.
4. Treat a review as done only when it is verified on the expected head. For
   `claude --status`, that is the `reviewed` status; a process exit or transcript
   is not evidence. A stale head needs a delta or full re-review, per the tiers.
5. Report a helper failure without implying the PR failed. Exit status 3 means the
   app could not be opened, and the output still carries the link.

Pass the full PR URL exactly as GitHub returned it; it is the only source of
repository identity. Run the helper from the PR's checkout.

## Install and verify

This repository is the source of truth; the copy on `PATH` is an install artifact.
Never edit the installed copy.

```bash
python3 agent-pr-review-link/scripts/install.py install
python3 agent-pr-review-link/scripts/install.py check
```

See the repository README for the full procedure, including rollback.
