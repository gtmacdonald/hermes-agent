# Accept an existing Kanban specification

A reviewed card in triage cannot be promoted through the public CLI without the auxiliary specifier rewriting it. `accept-spec` preserves the saved specification and routing fields and changes only the selected card's status to todo through the guarded `specify_triage_task` API.

```sh
hermes kanban --board BOARD accept-spec TASK_ID --dry-run --json
hermes kanban --board BOARD accept-spec TASK_ID --expected-spec-sha256 REVIEWED_HASH --json
```

The board is mandatory. There is no all-cards option. The hash binds the approval to every task field except lifecycle status, and is checked again inside the write transaction. Invalid state, an active claim/run, or a stale review is refused. The existing `specified` event records the transition. Title, body, assignee, model/provider, safety constraints, workspace, dependencies and history are preserved. Readback verifies the accepted row. The command does not call an LLM, spawn, infer, or recompute other cards' readiness.

Acceptance itself does not recompute readiness, so the card lands in todo. It is not a persistent hold: the next normal readiness recompute (`hermes kanban list`, a dispatcher tick) promotes a parent-free todo card to ready exactly as it does for `specify`, and dependency gating stays in the existing APIs. Ready is not dispatch: in this fork the gateway dispatcher only spawns cards named by a separate reviewed `kanban.dispatch_scope` approval. Normal auxiliary `specify` retains its current behavior.
