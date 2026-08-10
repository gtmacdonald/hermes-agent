# Engineering backlog

Ideas parked rather than built. Mirrors the shape of the Syndicate repo's
`BACKLOG.md` (which this file supersedes for Hermes-side work).

## Parked

### Merge the Syndicate project into Hermes and mark the old repo DEPRECATED {#merge-syndicate-deprecate}

Owen, 2026-08-08: the Syndicate additions belong in Hermes now — merge
`~/src/syndicate` into this repo and mark the standalone project as
DEPRECATED. This entry also resolves "where do Syndicate notes go":
Hermes-side work is recorded here from now on, not in the old repo's
backlog.

**Context, verified earlier today, not assumed:**
- Canonical Syndicate is v0.26.0, 56 commits ahead of origin/main, ~5.5GB
  total (722MB of it `art/`).
- Migration is INCOMPLETE: `~/.hermes/syndicate/` holds 3 stub scripts,
  while the source repo is the complete project. Decision on record:
  copy full `art/` and `speak/` source, excluding venvs.
- `MIGRATION.md` (in the syndicate repo) says `state.db` replaces the
  file-mail system; `handlers.py` still references file-based mail and
  must be refactored to the `state.db` mail API before integration
  (kanban card `t_f4c1c4f0`).

**Scope:**
1. Finish the migration into the agreed destination (`~/src/hermes/syndicate/`
   was the path last floated — confirm before moving 5.5GB).
2. Complete the `handlers.py` → `state.db` refactor first; don't merge
   code that contradicts the migration.
3. Mark `~/src/syndicate` DEPRECATED: banner at the top of its README and
   BACKLOG pointing here, final tag, no further work lands there.
4. Carry forward (don't lose) the old repo's parked backlog entries —
   either migrate them into this file or leave them readable behind the
   deprecation banner.

**Open questions:** exact destination layout inside the Hermes repo;
whether the old repo is archived on the forge or just banner-deprecated.

### Drift detection for sessions and projects — "where am I and what does this belong to" {#session-project-drift-detection}

Owen, 2026-08-08: *"we need to add drift detection to the overall flow.
This is happening to me a lot. It's hard to know where a session is and
what it belongs to."*

**Observed instances, same day, not hypothetical:**
- `project_switch` returned `success: true` for a switch to `hermes`, but
  `project_list` later showed the active project was actually `SKYDEWY` —
  the chat's workspace silently drifted from where the user (and agent)
  believed it was. Had to switch twice. Same failure previously observed
  with `stardewy` (recorded in the `hermes-desktop-projects` skill as a
  known pitfall: the switch return means "request accepted," not
  "workspace moved").
- Sessions disagreeing on model config (the config-coherence
  investigation, `CONFIG_COHERENCE_FIX.md`): multiple sessions stuck on
  stale config, unable to hot-swap models — the same class of problem,
  state drifting out from under a live session.

**Scope (sketch, not designed):**
1. Turn-start assertion: each turn, the agent/UI verifies the tuple
   (active project, workspace path, session id, model/config fingerprint)
   against the stores of record (`projects.db`, config fingerprint) and
   surfaces a visible warning on mismatch instead of proceeding silently.
2. Make `project_switch` verify-after-write internally (switch →
   re-read → confirm) so callers get truth, not "request accepted."
3. Always-visible session identity in the UI: which project this chat is
   anchored to, and the cwd its commands actually run in — the two are
   independent today and that's the confusing part.

**Related, already specced:** config fingerprint validation at turn start
is fix #2 in `CONFIG_COHERENCE_FIX.md`
(`.worktrees/stable-config-sprint/`) — this entry generalizes it from
model config to project/workspace identity.

### Default model/provider changes should propagate to sessions — Slow/Medium/Fast model meta {#model-provider-change-propagation}

Owen, 2026-08-08: when the default model is changed, also optionally
change it in all sessions. Really needs more user meta so that all
models are broadly **Slow / Medium / Fast** (user-configurable, v2).
When we have to switch provider sets, also update the default session
to the new provider set — otherwise a user will hit the untouchable
old-provider session. Better still: a button that glows, pressed to
update the provider, or a "provider refresh."

**Scope (as requested):**
1. Default model change offers optional propagation to all existing
   sessions, not just new ones.
2. Model meta-tiers: classify models as Slow / Medium / Fast so
   sessions can track a tier rather than a pinned model id;
   user-configurable tiers deferred to v2.
3. Provider-set switch updates the default session to the new provider
   set, so no session is left stranded on an unreachable old provider.
4. UI affordance: glowing button / "provider refresh" action to apply
   the new provider to a stale session instead of silent failure.

**Related:** the stale-session symptom is the same class as the
config-coherence cases in
[drift detection](#session-project-drift-detection) — this entry is the
remediation UX (propagate/refresh), that one is detection.

### Make "project" one thing — a folder — and split "profile" into Users keyed by home directory {#project-folder-profile-users}

Owen, 2026-08-08: *"We should tie the idea of a project together. It is
kind of nebulous. Is it the cwd? Is it the git repo? I think it is a
folder."* And: profile *"needs to be split into Users, identified by
their home directories. We should reuse metaphors when we can."*

**The ask:**
1. One definition of *project* everywhere in Hermes: a **folder**. The
   cwd and the git repo become derived facts about that folder, not
   competing identities for what a project is.
2. Split *profile* into **Users**, each identified by its home
   directory. (Documented current state: a profile already IS its own
   `HERMES_HOME` under `~/.hermes/profiles/<name>` — the rename makes
   the existing mechanism match the metaphor.)
3. Guiding principle: reuse familiar OS metaphors (folder, user)
   instead of inventing new ones.

**Related:** [drift detection](#session-project-drift-detection) — half
of "what does this session belong to" is that *project* has no single
definition to belong to today.

### Tag sessions by origin — background / subagent — and keep the parent hierarchy intact {#session-origin-tags-parent-hierarchy}

Owen, 2026-08-08: tag sessions as "background" or user sub-agent
tasks, *"but keep the parent hierarchy intact. It's getting
collapsed."*

**The ask:**
1. Sessions carry an origin tag — background (cron/background runs)
   or subagent task — so they're distinguishable from direct user
   sessions wherever sessions are listed.
2. Preserve the parent→child relationship (which session spawned
   which). Observed problem: that hierarchy is currently getting
   collapsed, flattening spawned sessions into the same level as
   top-level ones.

**Related:** [drift detection](#session-project-drift-detection) —
*"it's hard to know where a session is and what it belongs to"*;
origin tags plus intact parentage are the "what it belongs to" half.

### Config layers + project graph: global → project config, lifecycle status, retire, Excel-style hide/show {#project-config-graph-lifecycle}

Owen, 2026-08-08: *"Profiles with hermes is orchestration but I want to
use it in a more fine grained [way]. So there is the global hermes
config, the project config, and there is also the graph of project
dependencies and status; abandoned, replaced by, etc. We're missing
that. I need a way to retire a project, hide and show like Excel does
with rows and cols."*

**The ask:**
1. Config layering finer than profiles: profiles serve orchestration
   (whole separate instances); what's wanted is **global hermes config →
   per-project config** as distinct layers within one instance.
2. A **graph of projects** that doesn't exist today: dependencies
   between projects, plus lifecycle status on each — *abandoned*,
   *replaced by <other project>* (an edge, not just a flag), etc.
3. A way to **retire** a project as a first-class operation.
4. **Hide/show projects like Excel rows and columns** — reversible
   visibility, not deletion; hidden projects stay in the store and can
   be shown again.

**Related:** builds on
[project = folder](#project-folder-profile-users) — that entry defines
what a project *is*; this one adds the metadata around projects
(config layer, dependency/status graph, lifecycle, visibility).
