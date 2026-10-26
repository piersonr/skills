---
name: agent-pr-review-link
description: >-
  Hand a pull request to the other AI lineage for review, and apply the global review
  tiers (routine / substantial / high-stakes). Use when classifying a change's review
  tier, when a tier calls for a cross-lineage review, when the user explicitly asks Claude
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
   for one (the tier requirement is sufficient authorization) or the user asks:
   - Codex-authored PR, Claude reviews:
     `agent-pr-review-link claude --start <full-pr-url>`, then
     `agent-pr-review-link claude --status --json <full-pr-url>`.
     After fixes, `--follow-up` requests an exact-head re-review.
   - Claude-authored PR, Codex reviews:
     `agent-pr-review-link codex <full-pr-url>`, run from the PR's checkout. It
     starts a headless background review and returns at once. The output gives
     the job's log, PID and status command. Report it as `Waiting on`, and run
     `agent-pr-review-link codex --wait <full-pr-url>` in the background so you
     are woken when it ends. `--status [--json]` reports `running`, `finished`
     (with the result and the review URL) or `interrupted`.
     The reviewer runs `codex exec` on the ChatGPT login. It never uses an API
     key, and it never runs in CI. Its filesystem is read-only, and its shell can
     reach only the PR's GitHub host. It is not read-only on GitHub: it
     publishes with the user's own `gh` token, which could also push or merge,
     and only its instructions hold it to one review. It reviews the head
     resolved when the job started and publishes its own COMMENT review, the way
     the repository's instructions require. Hook-based publishers need Python
     3.11 or newer, which carries the user's hook trust over. For a re-review after fixes, run it again on the new
     head.
     `--interactive` (formerly `--open`, which still works) is the opt-out. It
     opens the Codex app with the request typed in, and nothing is sent until the
     user presses Enter.
4. Treat a review as done only when it is verified on the expected head. For
   `claude --status`, that is the `reviewed` status. For the headless Codex
   review, it is a `finished` job with result `reviewed` and a review URL. Status
   checks the live head each time, and reports `stale_head` after a new push. A
   process exit or transcript is not evidence. A stale head needs a delta or
   full re-review, per the tiers.
5. Report a helper failure without implying the PR failed. Exit codes:
   - 3: the app could not be opened, and the output still carries the link.
   - 5: Codex is not logged in with ChatGPT.
   - 6: the head moved.
   - 7: no published review on the expected head carries the run's marker.
   - 8: the background job failed to start.
   - 4: any other operational failure, including a failed or timed-out Codex
     run.

   The job's log ends with `exit=<code>`.

Pass the full PR URL exactly as GitHub returned it; it is the only source of
repository identity. Run the helper from the PR's checkout.

## Install and verify

The repository https://github.com/piersonr/skills is the source of truth; the
copy on `PATH` is an install artifact. Never edit the installed copy. Install from
a clone of the repository, not from an agent's skills directory, because the
installer records the clone's commit:

```bash
python3 <clone>/agent-pr-review-link/scripts/install.py install
python3 <clone>/agent-pr-review-link/scripts/install.py check
```

See the repository README for the full procedure, including rollback.

## Background publisher identity recovery

The helper removes the caller's harness markers from requests, but Claude's
existing supervisor merges those requests into its own environment. Deleting a
request variable does not remove a variable already held by that supervisor.
Before PR `--start` and `--follow-up`, a model-free `--bg --exec` shell probe
checks the supervisor worker environment for foreign publisher markers. It
reports names only and fails closed if the probe is unavailable or contaminated.
The temporary shell row expires automatically; it is not a review run. QA packet
reviews and `--status` do not run this publishing preflight.

A clean probe is not a publisher identity attestation. An already running resumed
worker may retain an older environment, settings or shell startup can introduce
markers, and the supervisor can change between probe and dispatch. The reviewing
session must still run its repository publisher's `whoami` before reading the
diff, and the publisher must keep rejecting genuinely ambiguous identities.

Do not automatically restart a shared supervisor or scrub variables inside a
reviewer's publishing command. Coordinate any supervisor restart with its other
sessions. `claude daemon stop --any --keep-workers` preserves running workers,
including their old environments; it does not clean them. For the pending review,
stop only its idle worker with `claude stop <id>` (preserving the conversation),
then use foreground
`claude --resume <saved-session-id>` from a clean human shell. Attaching to an
existing worker retains its environment. Check `whoami`, reconcile the exact head,
and only then publish in that reviewer session with the existing marker. This
requires separate recovery
execution authorization. If the head changed or the session cannot continue in
place, use an authorized helper follow-up and its new session/run marker.
