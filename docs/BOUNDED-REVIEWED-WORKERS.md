# Bounded reviewed workers

A reviewed dispatch entry can include `worker_policy` with `max_turns` (1–80),
`run_budget_seconds` (1–600), and `completion_mode` (`inspection` or `full`).
The dispatcher records this policy on the actual run before spawning, and passes
the limits to the native CLI. The classic CLI also applies the existing local
worker overlay, which restricts auxiliary routes and disables cloud fallbacks,
memory and MCP for an owned local worker. Interactive defaults remain unchanged.

An inspection run may attach its report and request review or block the card.
It cannot mark the canonical implementation complete, including after requesting
review, and cannot replace the recorded policy through completion metadata.
Approving an inspection report alone does not establish implementation acceptance.
Continue the canonical card with a freshly reviewed full-work run when its
implementation and acceptance work are ready. Existing full and unscoped runs
retain their normal completion semantics.

An approval without `reviewed_through_run_id` admits a never-run card once.
To authorize one further attempt while preserving history, set this field to the
exact latest run ID and review the current task and judge hashes again. Future
watermarks are refused. The next actual claim creates a new run ID and consumes
the approval, even if the attempt fails. Resetting a failure counter or returning
a card to ready does not renew approval. This applies to direct reviewed scopes;
the ready-cycle planner still does not revive historical attempts.

These controls neither change the profile/model assignment nor admit a whole
queue. Use supported existing failure accounting for local models; distinguish
infrastructure refusal from model task failure. Runtime installation requires the
existing gateway owner to stop the service before updating its live source and
restart that same service. Do not create a competing gateway.
