# Accept an existing Kanban specification

A reviewed card in triage cannot be promoted through the public CLI without the auxiliary specifier rewriting it. `accept-spec` preserves the saved specification and routing fields and changes only the selected card's status to todo through the guarded `specify_triage_task` API.

```sh
hermes kanban --board ai-setup accept-spec TASK_ID --dry-run --json
hermes kanban --board ai-setup accept-spec TASK_ID --expected-spec-sha256 REVIEWED_HASH --json
```

The board is mandatory. There is no all-cards option. The hash binds the approval to every task field except lifecycle status, and is checked again inside the write transaction. Invalid state, an active claim/run, or a stale review is refused. The existing `specified` event records the transition. Title, body, assignee, model/provider, safety constraints, workspace, dependencies and history are preserved. Readback verifies the accepted row. The command does not call an LLM, spawn, infer, or recompute other cards' readiness.

The accepted card stays todo, including a parent-free card. Promotion and dispatch are separate explicit actions; dependency gating remains in the existing APIs. Normal auxiliary `specify` retains its current behavior.

## Proposed local deployment

1. Review this isolated branch based on live commit `1b91b8eaa576b3c3dfe08e4592bdd698d1aca019`; 14 targeted tests pass. The live checkout was clean when inspected. No remote push or live replacement has occurred.
2. Obtain approval for installing this exact patch into the existing Hermes CLI, after rechecking the live HEAD and working tree. Keep the original commit pointer and patch for rollback. Do not change models, credentials, gateways, cron, or MCP registrations.
3. Verify the installed `accept-spec --help`, then dry-run the four exact ai-setup IDs and review their full saved specifications and hashes. Require the board's complete card inventory to equal exactly these four IDs, with no active runs or claims and each pinned to the authorized local quick route.
4. Accept each by its freshly reviewed hash. Read back exact safety conditions and all routing fields. Explicitly promote the four named IDs using the existing semantic CLI, then inspect board-only dispatch dry-run. Recheck local route/lease eligibility with the setup owner before inference.
5. Run one board-scoped bounded pass with max four, under the quick profile so per-profile capacity and local-only auxiliary configuration apply. No global gateway dispatch or old queues. If dry-run includes anything beyond the four IDs, stop.
6. Observe claims/run IDs/logs and terminal semantic outcomes. A model-list GET proves reachability but does not prove resource lease availability or successful inference. Report failures and never auto retry beyond each card's existing max-retries=1 policy.

Rollback code using the saved original commit/patch after stopping only this task's workers if required. Do not rewind or overwrite the shared board database to undo code; preserve history. Any card reversal requires explicit semantic reconciliation of its actual run state.
