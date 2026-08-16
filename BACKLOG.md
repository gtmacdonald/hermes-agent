# Engineering backlog

Ideas parked rather than built. Mirrors the shape of the Syndicate repo's
`BACKLOG.md` (which this file supersedes for Hermes-side work).

## Parked

### Add a "btw" skill — interject a side question without breaking the active task {#btw-skill}

Gregory, 2026-08-14: Claude Code has a "btw" affordance — a way to
interject a side question without breaking the active task or losing
the in-flight work. Hermes needs the same. The user should be able to
ask a quick unrelated thing mid-run, get a short answer, and return to
the ongoing work without losing context or causing the agent to
context-switch the whole task.

**The behavior (Claude Code reference, not invented here):**
- "btw <question>" — or any out-of-band phrasing recognized by the
  skill — is intercepted *before* it reaches the current task loop.
- The agent answers the btw briefly (one or two sentences, no tool
  sprawl) and explicitly signals return to the original work.
- The in-flight task's state, scratch, and conversation position are
  preserved. No resumption prompt is needed; the agent just picks back
  up.
- If the btw itself needs real work, the skill escalates into a full
  interrupt — same machinery as a normal user message, just with a
  "you can come back" semantic.

**Why now:** Hermes already has the out-of-band delivery plumbing —
the `[OUT-OF-BAND USER MESSAGE]` marker that gets appended to a tool
result mid-turn (see the `mid-turn user steering` section of the system
prompt). That marker is *delivery*; what it lacks is a *named
affordance* the user can invoke intentionally ("btw") and a *behavior
contract* the agent follows (short answer, signal return, don't
abandon the active task). The wiring exists; the skill is missing.

**Scope (sketch):**
1. New skill `btw` in `~/.hermes/profiles/<name>/skills/btw/SKILL.md`
   that defines the trigger phrasing, the response shape ("btw answer
   + return-to-task line"), and the no-tool-sprawl rule. Skill is
   per-profile but the *behavior contract* is global — mirror the
   pattern from `interrupt-settle-voice` (per-profile config,
   **global** behavior).
2. Decide whether the trigger is a slash command (`/btw`), a prefix
   convention (`btw ...`), or both. Slash command is cheaper to
   recognize; prefix is more natural in voice.
3. Pair with the existing interrupt-settle work: an interjection is a
   soft interrupt (return to task), an outright stop is a hard
   interrupt (settle into silence). Same machinery, different policy.
4. Logging: tag btw exchanges in the session log so they can be
   filtered out of task narration and reviewed independently.

**Open questions:**
- Does "btw" also work during delegated subagent runs, or only in the
  parent session? My instinct: parent only — a child agent doing
  deterministic work shouldn't be interrupted by a side question
  intended for the user-facing agent.
- Where does the btw answer get delivered if the user is on a voice
  channel (Telegram voice notes, etc.) — same voice bubble, or a
  different audio cue so the user can tell it apart from the task
  reply?

### Add n8n-backed pub-sub to Syndicate so it can be driven entirely from a Hermes child profile {#syndicate-n8n-pubsub}

Gregory, 2026-08-14: n8n pub-sub was the missing piece in the Syndicate
tool. Goal: drive the whole Syndicate from inside a Hermes profile
(child) and resume in the parent profile after the child finishes. At
any moment only **one profile lineage is active** — the child runs until
the work is done, hands back to the parent, and the parent resumes here
with any improvements. This is the pattern the
`minecraft-process-visualizer` profile is being built under, and the
same pattern should be available to any future project that needs to
"go do this work over there and come back."

**Why now, not later:** the Syndicate skill (`~/src/syndicate/skills/syndicate/SKILL.md`)
already treats files as the system of record and a host session as a
disposable cache — *the files are the system of record; a host session
is a disposable working cache.* Pub-sub on top of n8n is the natural
extension: the canonical state is in files; n8n topics fan events out
to whichever subscribers (including the child profile) are awake. The
"resume in parent" handoff becomes a single file-mail drop, not a
process orchestration.

**Scope (sketch, not designed):**
1. Decide the topic taxonomy. Candidate namespaces:
   `syndicate.room.<project_key>.<verb>`,
   `syndicate.card.<card_id>.<verb>`,
   `syndicate.profile.<profile_name>.<verb>` (the cross-profile
   hand-off channel).
2. Define the wire format — JSON envelope `{event, ts, project_key,
   actor, payload, trace_id}`. Probably piggy-back on the existing
   `state.db` mail table so the child profile speaks the same language
   as the parent.
3. Decide subscriber semantics: at-least-once vs at-most-once; replay
   from log on child-profile boot; idempotency keys per `trace_id`.
4. The "child finishes, parent resumes" pattern needs two primitives:
   (a) a child profile can publish `syndicate.profile.<parent>.resume`
   with a handoff payload (improvements, blockers, new state); (b) the
   parent profile must be reawakeable on that topic — likely via
   `wake-hermes` workflow already in `n8n-shared-daemon`.
5. Single-active-lineage invariant must be enforced somewhere — either
   a profile-level lock in `state.db` or an n8n gate topic that the
   parent publishes before child boots and the child publishes before
   parent resumes. Pick one and document it.

**Open questions:**
- Do we keep Syndicate's file-based mailbox (`syn_mail`) or move it
  entirely onto n8n topics? The hybrid (files for state, n8n for fan-out)
  is probably right but needs a migration story.
- Where does the child profile's improvements get merged back into the
  parent — file-mail drop, kanban card, or n8n topic payload?
- Does this belong inside the existing "Merge Syndicate into Hermes"
  backlog entry above, or is it a separate workstream? (Likely
  separate: that one is a *code* merge; this one is a *protocol*
  addition that depends on n8n.)

**Related session:** `20260814_21....` in profile
`minecraft-process-visualizer` (this one).

### Interrupt-settle voice loop: interruption shortens the next response until silence {#interrupt-settle-voice}

Gregory, 2026-08-14 (dictated while waiting for it to exist): *"I want to
be able to naturally interrupt your response. Then you continue with a
session round considering a shorter response. This eventually settles
into silence."* The design already exists in the Syndicate and needs
lifting into the Hermes fork and hermes **global** config (not
per-profile).

**The mechanic (from the Syndicate, not invented here):**
- Room-engine spec `2026-08-03-syndicate-room-engine-design.md` §7
  "Slider drift": `current` drifts inside `band` in response to events
  and settles toward `default` at rest. Substitute *response length* for
  the slider: an interruption is an event that pushes `current` down;
  each uninterrupted round lets it relax back toward `default`.
- Entity-actor spec `2026-08-06-entity-actor-system.md`: heartbeat
  backoff + the mail-settle rhythm ("talk makes mail; mail defers
  talk") — repeated interruption compounds the backoff until the agent
  contributes nothing, i.e. **settles into silence** rather than being
  cut off.

**Scope (sketch):**
1. Config surface: `voice.interrupt_settle: {enabled, decay_factor,
   floor_chars, recovery_rounds}` in **global** hermes config, overridable
   per profile.
2. Signal: an interrupted turn is already recorded (desktop
   `interrupted_turns.json` exists in profile `desktop/` state today) —
   feed that into the next round's drafting budget instead of dropping it.
3. Half of this pairs with the open audio question: a session should
   know **where** playback stopped, so the shorter follow-up can pick up
   from what wasn't heard instead of repeating from the top. (Parked
   sub-question — TTS players today don't report playhead position back
   to the session.)
4. Settle: N consecutive interruptions (or `current` hitting
   `floor_chars`) → the agent's round produces silence (no reply, or an
   ack glyph), and recovery only via user re-engagement.

**Open questions:** does decay apply per-session or per-topic; does an
explicit user question always reset the slider to `default`; where does
the playhead signal come from on each platform (desktop app vs Telegram
voice notes have different affordances).

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

### Delete and clean up the per-profile `projects.db` stores {#per-profile-projects-db-cleanup}

Gregory, 2026-08-14: each Hermes profile currently carries its own
`projects.db` (under `~/.hermes/profiles/<name>/projects.db`). Observed
state on this machine: 6 per-profile copies, all 45056 bytes, schema
identical (`projects`, `project_folders`, `project_meta`,
`discovered_repos`). Real data is sparse and partially duplicated —
studio has 5 projects, professor 3, accountant 1, image / video /
minecraft-process-visualizer have 0. The same primary path
(`/Users/greg/src/syndicate`) appears in both the `default` profile's
`projects.db` and `studio`'s, and `/Users/greg/ACC` appears twice in
`professor` under different slugs (`acc-professor`, `acc-professor-2`).
This is the cleanup pass for that.

**What "clean up" means, concretely:**
1. Decide whether projects are **per-profile** (current design — see
   `hermes_cli/projects_db.py:14` "Scope: per-profile" and
   `hermes_cli/backup.py:1120` listing `projects.db` as a per-profile
   artifact) or **global**. If global, every CLI path that calls
   `get_projects_db_path()` changes. If per-profile, this entry
   becomes a dedup-and-prune job instead of a refactor.
2. Whatever the decision: back up first. Snapshot every
   `~/.hermes/profiles/*/projects.db` (and its `-shm` / `-wal`
   siblings) into a timestamped tarball under `~/.hermes/backups/` so
   the delete is reversible. No silent drops.
3. Drop rows whose `archived=1` and were archived before
   2026-08-01 — those are stale and have no UI surface anyway.
4. Dedupe live projects on `primary_path`: if the same path shows up
   in two profiles, keep the oldest `created_at` and re-anchor the
   loser to the winner via `project_folders.is_primary=0`. Don't
   silently delete — surface the duplicate to the user.
5. Re-run `hermes doctor` after the cleanup and confirm the
   `projects.db: rollback journal mode` line still passes (see
   `tests/hermes_cli/test_doctor_journal_modes.py:186`).

**Open questions:**
- Per-profile vs global — the existing code says per-profile
  deliberately. If we go global, `profiles.py:464`'s claim that
  "projects.db" is part of the per-profile scope (alongside
  `state.db`) needs to be revisited. That decision should probably
  piggy-back on
  [project = folder](#project-folder-profile-users) — the answer
  there ("project is a folder") implies a global-ish store keyed by
  folder, not by profile.
- Do duplicate slugs across profiles (`syndicate` in default +
  studio) come from the desktop app seeding a fresh per-profile
  store with the same project on first boot? That would be a bug
  worth fixing, not just cleaning up.
- The `discovered_repos` table has 42 rows in every profile that has
  it, and they look identical (same roots, same `last_seen`). If
  that's true, the dedup logic should apply to `discovered_repos`
  too — but they're meant to be profile-scoped cache of "what repos
  has this profile seen," so globalising them may be wrong.

**Related:** directly downstream of
[project = folder](#project-folder-profile-users) — the cleanup
can't be done right until that decision lands. Also touches
[project config graph](#project-config-graph-lifecycle) if we go
global (the graph needs to live somewhere, and "per-profile" is no
longer a viable answer once projects are global).

### Simple Hermes dashboard + per-profile three-sentence status update {#simple-dashboard-profile-status}

Gregory, 2026-08-15: the web dashboard already exists
(`hermes dashboard`, `web/`, `hermes_cli/subcommands/dashboard.py`)
but it's heavy — full admin panel plus embedded TUI chat. What's
missing is the at-a-glance surface Greg keeps wanting: a
**simple Hermes dashboard** that shows, per profile, a **three-sentence
status update** (≤150 words each) broken down as: what's currently
busy, what's next, and the last three things that were finished.
This entry pairs with the existing dashboard code rather than
replacing it — the simple view is a tile or page on top of the
existing UI, not a parallel product.

**Why now:** today's working set spans ~13 profiles (`studio`,
`professor`, `accountant`, `audio-synth`, `image`, `video`,
`vocal-coach`, `joni`, `game-engine`, `frontier-access`,
`student-frontier-access`, `acc-personal`, and this one —
`minecraft-process-visualizer`). At session start the user has to
context-switch into "which one is alive, which is queued, which just
finished" by hand. A fixed-shape status card per profile collapses
that to a glance. The three-sentence budget (≤150 words) is
deliberately tight — it's a status, not a report.

**The status shape (one card per profile):**
1. **Busy working on** — one sentence, present-tense, naming the
   in-flight task or block.
2. **What's next** — one sentence, the immediate next step the
   profile (or its owner) intends to take.
3. **Last 3 finished** — one sentence naming the three most recent
   completions, comma-separated. Older than that doesn't belong here.

**Scope (sketch):**
1. **Data source.** Each profile writes its status to a small JSON
   blob under `~/.hermes/profiles/<name>/status.json` with the
   three-sentence fields above. The profile is the writer; the
   dashboard is a read-only consumer. Updates are pull-on-page-load,
   no push channel needed.
2. **Writer surface.** Add a slash command (e.g. `/status`) and an
   equivalent CLI (`hermes status set / next / done`) so the
   profile's own agent can maintain its status while working, plus
   a minimal interactive prompt for human entry. The three
   sentences are stored verbatim so the agent or user keeps full
   voice — no template rewrites.
3. **Reader surface.** Add a "Status" tile / page to the existing
   web dashboard (`web/`) that lists every profile as a card with
   the three sentences, sorted by `last_visited` (most recent
   first). No interaction beyond read; editing goes through the
   writer surface above.
4. **Word budget enforcement.** The reader warns (not blocks) when
   a sentence exceeds 150 words; the writer enforces a hard 200-word
   ceiling per field so the card can't blow its layout.
5. **Where this lives.** Reuses the existing profile enumeration
   (`HERMES_HOME/profiles/`), the existing `dashboard_auth` plugin
   for read-access control, and the existing `dashboard_register`
   scaffolding for tile registration. No new auth model.

**Open questions:**
- Does the "last 3 finished" auto-derive from `state.db` (recent
  completed sessions) or is it hand-entered? Hand-entered is more
  curated; auto-derived is cheaper but noisier. Lean hand-entered
  for v1, with an auto-suggest toggle behind it.
- Should the dashboard group profiles by owner (`studio` and
  `vocal-coach` are Greg's working set; `professor` is the
  academic hat; etc.) or just render a flat list? Lean grouped —
  the at-a-glance value is highest when profiles are clustered the
  way the user thinks about them.
- Does the status card belong on the dashboard home page or behind
  a `/status` route? Lean home-page tile — a separate route
  buries it.

**Related:** none direct. Adjacent work in this file is the
[btw skill](#btw-skill) (status can include "interrupted by
btw at HH:MM") and the profile-graph entries above (the status
card is the simplest possible read of "what is each profile
doing right now," which the drift-detection and config-graph
entries want anyway).
