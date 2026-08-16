import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  initQuickEntryBridge,
  QUICK_TARGET_NEW,
  type QuickEntrySubmitPayload,
  setQuickEntrySubmitHandler
} from '@/store/quick-entry'

import { useQuickEntryBridge } from './use-quick-entry-bridge'

vi.mock('@/store/quick-entry', () => ({
  initQuickEntryBridge: vi.fn(() => () => {}),
  QUICK_TARGET_CURRENT: 'current',
  QUICK_TARGET_NEW: 'new',
  setQuickEntrySubmitHandler: vi.fn()
}))

vi.mock('@/store/session-states', () => ({ sessionTileDelegate: () => null }))
vi.mock('@/store/windows', () => ({ isSecondaryWindow: () => false }))

function Harness({
  startFreshSessionDraft,
  submitText
}: {
  startFreshSessionDraft: (options?: { workspaceTarget: null }) => void
  submitText: (text: string) => void
}) {
  useQuickEntryBridge({ startFreshSessionDraft, submitText })

  return null
}

describe('useQuickEntryBridge global Quick Entry target', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('creates the default Quick Entry session without a project cwd', () => {
    const startFreshSessionDraft = vi.fn()
    const submitText = vi.fn()

    const { unmount } = render(<Harness startFreshSessionDraft={startFreshSessionDraft} submitText={submitText} />)
    const handler = vi.mocked(setQuickEntrySubmitHandler).mock.calls.at(-1)?.[0]

    expect(handler).toBeTypeOf('function')
    ;(handler as (payload: QuickEntrySubmitPayload) => void)({ target: QUICK_TARGET_NEW, text: 'Capture this' })

    expect(startFreshSessionDraft).toHaveBeenCalledWith({ workspaceTarget: null })
    expect(submitText).toHaveBeenCalledWith('Capture this')

    unmount()
    expect(initQuickEntryBridge).toHaveBeenCalled()
  })
})
