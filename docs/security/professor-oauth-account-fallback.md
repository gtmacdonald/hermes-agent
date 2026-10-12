# Professor OAuth account fallback

This core feature is **off by default**. It implements Greg's October 11, 2026 authorization to use existing Studio and personal OAuth accounts when every applicable Professor account exhausts its allowance. No production policy or credential was changed by this PR. The active checkout is not the development worktree.

The consumer stays Professor. Account rotation keeps the current Anthropic provider, exact model, endpoint, API transport, ACC workgroup, session, tools, cwd and vault products. It never loads another profile's environment, changes HERMES_HOME, copies a credential into Professor, buys allowance, logs in, selects another model, or admits an API key.

## Admission

An explicit version-1 policy in Professor's auth store enables the feature. In this mode the primary account inventory consists of independent pool-owned OAuth grants (`manual:*` OAuth rows), whose owning store and provider-verified identities define ownership. Ambient environment credentials and shared Claude Code/singleton shadows are not claimed as Professor accounts and cannot bypass the gate. Existing approved API-key routing outside this opt-in mode is unchanged.

Healthy Professor grants remain primary, even if a donor has a higher pool priority. Borrowing requires a structured account allowance code (`quota_exhausted`, `usage_limit_reached`, `subscription_limit_reached`, or `insufficient_quota`) from the actual failed provider request, together with an explicit future reset. Evidence is bound to verified account/organization identity, lasts at most one hour, and is deduplicated by account UUID across grants and organizations. Generic 429s, model limits, authentication errors, outages, unknown identities, missing resets, and guessed delays do not qualify. A later nonquota failure invalidates quota admission.

The pool rereads persisted Professor inventory before every admission. A peer's reset, removal, newly added account, or changed identity overrides stale process state. Reset/elapsed windows restore Professor through normal pool selection and the existing turn-start reversion path. No manufactured exhaustion or live quota probe is introduced.

Eligible references are tried in Studio, then personal order. Each reference binds an exact existing owner credential ID, account/organization identity, and explicitly verified model list. Another grant or organization of the same Professor account adds no allowance and is refused as a donor.

## Owner transaction

Studio resolves through the existing named-profile registry; personal means the existing global Hermes auth store, not a new profile. Arbitrary owner paths and escaping symlinks are refused. Only independent pool-owned OAuth grants are supported as donors. Claude Code, environment and singleton rows have other token authorities and are deliberately not treated as pool-owned donor grants.

Consumer auth lock precedes the owner's existing path-keyed cross-process auth lock. Hydration verifies that the reference still exists, its metadata/model is unchanged, and account quota admission still holds. The owner lock covers reread, token refresh, and atomic durable commit. The token pair reaches the consumer only in memory after commit; the consumer never receives the refresh token. Native owner refresh uses the same auth-store lock. Owner identity binding travels with token-generation merges.

An uncommitted single-use refresh records the existing durable spent-rotation sidecar verdict. Its new pair is not returned, persisted in Professor, or replayed. Owner quota evidence is also written to the owner transaction so other consumers observe exhaustion. Explicit removal is never reseeded.

Professor reference persistence uses a strict metadata allowlist. It removes tokens, owner token bindings, fingerprints, raw messages and arbitrary nested payloads. Original owner grants remain in their original stores.

## Explicit enrollment API

These are supported Python APIs in `agent.oauth_account_fallback`. Run them with the installed Hermes interpreter and the intended existing profile context, after deploying reviewed code. There is no automatic production activation.

1. `verify_pool_account(credential_id, persist=False)` performs a non-persisting readiness check and returns private proof metadata without tokens or token-generation fingerprints. The default `verify_pool_account(credential_id)` enrolls an **existing pool-owned OAuth grant** in its owning store using one authenticated read-only GET to Anthropic's OAuth profile endpoint. It creates no login, token, model call or new credential. It commits identity only if the token generation still matches after the request; it returns a boolean, never identity values or credentials. OAuth exchange responses also capture authoritative identity when available. Unverifiable response shapes fail closed.
2. In Professor, `add_reference(owner="studio" or "personal", credential_id=..., models=[exact_previously_verified_model])` writes a secret-free reference. Owner identity must already be verified. Model support must come from prior genuine operation/verification; no model request is made by enrollment.
3. `configure(active=True)` verifies Professor identity inventory and explicit owner references before writing the reversible version-1 policy. `configure(active=False)` disables borrowing. `hermes auth remove anthropic <reference>` removes a reference through normal pool administration; `hermes auth reset anthropic` clears consumer status through the existing administration path.

Do not enable on partial account identity coverage. The October 11 inspection found independent Professor, Studio and personal OAuth rows, but no enrolled identity records. No production identity request, enrollment, policy activation, account switch, model turn, shared restart, or live allowance exhaustion was performed. The profile endpoint behavior and actual quota response/reset shape remain live-verification limitations.

## Validation

Synthetic real-import tests use isolated temporary Hermes homes and owner stores. Coverage includes healthy-primary priority, quota-only admission, account/token alias deduplication, Studio-to-personal ordering, identity/generation mismatch, exact models, API-key exclusion, owner removal, peer reset/new account, read-only identity enrollment, reversible configuration, thread/process refresh serialization, failed durable commit, main recovery, actual client swap, auxiliary recovery, secret-free persistence and scope preservation.

Run focused tests with the repository test interpreter; additionally run the existing credential-pool, OAuth write-through, profile-fork, endpoint-boundary, model-cooldown, auxiliary and turn-start reversion regressions. Isolate `CLAUDE_CONFIG_DIR` to a temporary directory when running legacy pool tests that otherwise try to open the operator's Claude credential lock.


## Live read-only readiness follow-up

On October 11 the existing verifier was run in non-persisting mode against all seven configured independent OAuth grants. Studio's identity endpoint accepted the grant with the supported account/organization response shape, and its authenticated Anthropic catalog returned fourteen model IDs. Both Professor grants and all four personal grants returned HTTP 401 for identity and catalog. No refresh, reauthentication, enrollment, model call, activation or auth-store change occurred. Read-only evidence is distinct from the synthetic suite and does not establish inference entitlement. Private account metadata stays outside Git, PR descriptions and bus messages.

Professor's current configuration at that follow-up is OpenRouter with `anthropic/claude-opus-5.5`. This feature is limited to native Anthropic OAuth and cannot be activated for that current provider while preserving the exact route. Readiness therefore requires a separate scope decision before any attempt to use it on Professor's current gateway route. The accepted Studio metadata addition has a private concrete preview and conditional field-removal rollback; it was not applied.

The current OpenRouter public catalog returned HTTP 200 and listed the exact configured `anthropic/claude-opus-5.5` among 458 IDs. This establishes catalog visibility for the current provider/model, not paid-account inference entitlement. It does not admit an Anthropic OAuth donor on the OpenRouter route.

The final default-route check used the actual installed config loader with Professor home/profile/workgroup pinned. It confirmed OpenRouter plus the same exact model on `chat_completions`, with no managed config layer; existing live sessions and assigned cards can still retain explicit or frozen overrides. The installed CLI loads a separate source checkout where this PR's new module is absent. This is implementation/readiness evidence, not deployed end-to-end account recovery. All six HTTP 401 refusals coincided with locally expired owner access tokens; the server's exact cause was not inferred as expiry, revocation or insufficient scope. No token refresh occurred.

## Explicit personal-account override under the strengthened ruling

Greg's later ruling explicitly allows his personal accounts, including the Studio personal profile, to authenticate Professor work. It is distinct from automatic account-quota admission. `configure(active=True, mode="user_override", requested_owner="studio" or "personal", authorization_reference=..., native_model=...)` requires an explicit operator authorization reference and a verified exact-model owner reference. It does not manufacture a quota verdict or relabel the task Studio. Normal `automatic_quota` mode still prefers healthy owned Professor accounts.

For the current OpenRouter `anthropic/claude-opus-5.5`, `native_override_route` admits the explicit mapping to native `claude-opus-5-5` only when that native ID is in the authenticated catalog. The config loader applies an in-memory Professor-only model/provider/endpoint/wire overlay: native Anthropic, `anthropic_messages`, `https://api.anthropic.com`. Original YAML remains unchanged. Old gateway key overrides and implicit provider fallback chains are removed from the overlay; session/cwd/workgroup/tools/vault are retained. Rollback disables only the override policy and removes the new reference/conditional identity metadata; it never clears histories. Existing frozen sessions are not migrated implicitly.

Actual Studio identity/catalog acceptance and remaining access validity establish a non-refresh candidate for this mapping; inference entitlement was not model-tested. The private exact owner-metadata, Professor-reference, policy and overlay preview remains unapplied. Six older grants remain expired/authentication-refused. Automatic approval review rejected the live refresh before execution because authorization was relayed by delegation rather than a direct trusted user message; no rejected refresh was retried. Direct approval is needed before that single-use credential action. The accepted Studio alternative does not bypass that rejection: it requires no refresh now, keeps all tokens in their owner store, and remains subject to review/install/enrollment gates.

Meaningful synthetic tests cover directed personal and Studio selection without quota fabrication, real config loader and actual client swap, forbidden healthy-grant refresh, unchanged owner store, preserved Professor scope/storage/tools/session, invalid-owner rejection, exact catalog mapping and absent direct-authorization rejection. The existing health owner receives this suite as the daily regression interface, with fixture success separated from deployed/live auth readiness. No new scheduler is created.

Final implementation evidence: CodeRabbit on the gtmacdonald seat completed its review with one valid managed-precedence finding, now fixed with a conflict-rejection test. The accepted Studio identity metadata was subsequently enrolled in its original owner store; its access/refresh pair stayed unchanged, no refresh or model call occurred, and Professor reference/policy remained unapplied. A separate isolated integration checkout based on the actual installed `7be32639c1` revision preserves its modern type syntax and passes 229 regression tests plus its stricter Ruff policy. Live source remains unchanged. Existing daily health ran the explicit override tests against a stable source snapshot and retains deployed/runtime readiness as a separate untested observation. The expired-personal refresh rejection was not bypassed or retried. The remaining shared installation boundary is owned by the installed Hermes session; no shared restart or history clearing occurred.
