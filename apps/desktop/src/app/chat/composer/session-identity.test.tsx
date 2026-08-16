import { cleanup, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, describe, expect, it } from 'vitest'

import { type SessionView, SessionViewProvider } from '@/app/chat/session-view'
import { $sessions } from '@/store/session'

import { SessionIdentity } from './session-identity'

const view = (over: Partial<SessionView> = {}): SessionView => ({
  kind: 'primary',
  $awaitingResponse: atom(false),
  $busy: atom(false),
  $cwd: atom('/Users/greg/src/hermes'),
  $fast: atom(false),
  $lastVisibleIsUser: atom(false),
  $messages: atom([]),
  $messagesEmpty: atom(false),
  $model: atom(''),
  $provider: atom(''),
  $reasoningEffort: atom(''),
  $runtimeId: atom('runtime-1'),
  $storedId: atom('stored-1'),
  ...over
})

afterEach(() => {
  cleanup()
  $sessions.set([])
})

describe('SessionIdentity', () => {
  it('names the stored conversation receiving the next message', () => {
    $sessions.set([{ id: 'stored-1', message_count: 1, title: 'Hermes Fork Status Update' }] as never)

    render(
      <SessionViewProvider value={view()}>
        <SessionIdentity />
      </SessionViewProvider>
    )

    expect(screen.getByText('Hermes Fork Status Update')).toBeTruthy()
    expect(screen.getByText('Sending to')).toBeTruthy()
  })

  it('labels a fresh draft as a new session instead of inventing a prior destination', () => {
    render(
      <SessionViewProvider value={view({ $runtimeId: atom(null), $storedId: atom(null) })}>
        <SessionIdentity />
      </SessionViewProvider>
    )

    expect(screen.getByText('New session')).toBeTruthy()
  })
})
