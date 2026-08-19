import fs from 'node:fs'
import path from 'node:path'

// Mirrors hermes_cli.profiles._PROFILE_ID_RE so we never hand the backend a
// value its profile resolver would reject and exit on.
const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

// Which profile the primary (window) backend is REALLY on.
//
// The desktop's own preference (active-profile.json) is null on every install
// that never used the Settings profile switch, and `startHermes()` then spawns
// `hermes serve` with no --profile. That does NOT mean "default": the CLI's
// _apply_profile_override (hermes_cli/main.py) falls through to the sticky
// <root>/active_profile file and re-homes HERMES_HOME onto that profile. So the
// renderer adopted "default" while its own socket served, say, `studio` — and
// every profile-scoped route (sidebar scope, settings/theme scope, session id
// lookups, plugin roots) resolved against the wrong home. Read the same file
// the backend reads, so both halves of the app name one profile.
function readStickyProfile(hermesHome: string, fsImpl = fs): null | string {
  // _apply_profile_override step 1.5: an explicit HERMES_HOME already inside
  // profiles/ pins the backend on its own and the sticky file is never read.
  // Reporting a name here would also be wrong to pass back as --profile, since
  // resolve_profile_env() re-resolves it under the standard profiles root.
  if (path.basename(path.dirname(hermesHome)) === 'profiles') {
    return null
  }

  try {
    // ponytail: reads this root's sticky file; the CLI reads the platform
    // default root's. Same file unless HERMES_HOME points at a custom root.
    const name = fsImpl.readFileSync(path.join(hermesHome, 'active_profile'), 'utf8').trim()

    return name && name !== 'default' && PROFILE_NAME_RE.test(name) ? name : null
  } catch {
    // Missing (the common "no profile in use" case) or unreadable → nothing pinned.
    return null
  }
}

// The desktop's stored preference wins — when set it is passed as --profile and
// the backend honors it over the sticky file. null means nothing pins the
// primary, so it runs on the default root.
function resolvePrimaryProfile(hermesHome: string, storedProfile: null | string, fsImpl = fs): null | string {
  return storedProfile || readStickyProfile(hermesHome, fsImpl)
}

export { PROFILE_NAME_RE, readStickyProfile, resolvePrimaryProfile }
