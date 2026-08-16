import { useStore } from '@nanostores/react'

import { useSessionView } from '@/app/chat/session-view'
import { sessionTitle } from '@/lib/chat-runtime'
import { cn } from '@/lib/utils'
import { $sessions, sessionMatchesStoredId } from '@/store/session'

/**
 * A quiet, persistent destination label beside the composer. The composer is
 * where a message is committed, so it names the session that will receive it
 * without asking the user to infer selection from a tab, sidebar row, or
 * background activity elsewhere in the workspace.
 */
export function SessionIdentity() {
  const view = useSessionView()
  const storedId = useStore(view.$storedId)
  const sessions = useStore($sessions)
  const session = storedId ? sessions.find(candidate => sessionMatchesStoredId(candidate, storedId)) : undefined
  const label = session ? sessionTitle(session) : 'New session'

  return (
    <div
      aria-label={`Sending to ${label}`}
      className={cn(
        'flex min-w-0 items-center gap-1.5 text-[0.6875rem] leading-none text-(--ui-text-tertiary)',
        'select-none'
      )}
      data-testid="composer-session-identity"
    >
      <span className="shrink-0">Sending to</span>
      <span className="max-w-64 truncate font-medium text-(--ui-text-secondary)">{label}</span>
    </div>
  )
}
