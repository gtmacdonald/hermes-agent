import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesReadDirResult } from '@/global'
import type * as HermesModule from '@/hermes'

import { $pluginRecords, setPluginEnabled } from './plugins-store'
import { discoverRuntimePlugins, watchRuntimePlugins } from './runtime-loader'

// getStatus would supply the connected backend's hermes_home — a REMOTE path in
// remote mode. The disk scanner must NOT derive the plugin root from it (#66899).
const getStatus = vi.fn(async () => ({ hermes_home: '/remote/box/.hermes' }))

vi.mock('@/hermes', async importActual => ({
  ...(await importActual<typeof HermesModule>()),
  getStatus: () => getStatus()
}))

const desktopPluginsRoot = vi.fn<() => Promise<string>>()
const agentPluginsRoot = vi.fn<() => Promise<string>>()
const readDir = vi.fn<(path: string) => Promise<HermesReadDirResult>>()
const readFileText = vi.fn<(path: string) => Promise<{ text: string }>>()
const watchDirectory = vi.fn<(path: string) => Promise<{ id: string }>>()
const watchPreviewFile = vi.fn<(path: string) => Promise<{ id: string }>>()
const onPreviewFileChanged = vi.fn()

beforeEach(() => {
  desktopPluginsRoot.mockReset()
  agentPluginsRoot.mockReset()
  readDir.mockReset()
  readFileText.mockReset()
  watchDirectory.mockReset()
  watchPreviewFile.mockReset()
  onPreviewFileChanged.mockReset()
  getStatus.mockClear()
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    agentPluginsRoot,
    desktopPluginsRoot,
    onPreviewFileChanged,
    readDir,
    readFileText,
    watchDirectory,
    watchPreviewFile
  }
})

afterEach(() => {
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

describe('scanDiskPlugins (#66899)', () => {
  it('scans the Electron-resolved local roots, never the backend hermes_home', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    agentPluginsRoot.mockResolvedValue('/local/.hermes/plugins')
    readDir.mockResolvedValue({ entries: [] })

    await discoverRuntimePlugins()

    expect(desktopPluginsRoot).toHaveBeenCalled()
    expect(readDir).toHaveBeenCalledWith('/local/.hermes/desktop-plugins')
    expect(readDir).toHaveBeenCalledWith('/local/.hermes/plugins')
    // The remote backend's hermes_home must never feed the local plugin scan.
    expect(getStatus).not.toHaveBeenCalled()
    expect(readDir).not.toHaveBeenCalledWith('/remote/box/.hermes/desktop-plugins')
  })

  it('no-ops when the resolvers yield no local root', async () => {
    desktopPluginsRoot.mockResolvedValue('')
    agentPluginsRoot.mockResolvedValue('')

    await discoverRuntimePlugins()

    expect(readDir).not.toHaveBeenCalled()
  })

  it('looks for desktop/plugin.js inside agent-plugin packages without asking for a missing file', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    agentPluginsRoot.mockResolvedValue('/local/.hermes/plugins')
    // A Python-only package: plugin.yaml + __init__.py, no desktop half.
    readDir.mockImplementation(async dir =>
      dir === '/local/.hermes/plugins'
        ? { entries: [{ isDirectory: true, name: 'my-feature', path: '/local/.hermes/plugins/my-feature' }] }
        : dir === '/local/.hermes/plugins/my-feature'
          ? {
              entries: [
                { isDirectory: false, name: 'plugin.yaml', path: '/local/.hermes/plugins/my-feature/plugin.yaml' }
              ]
            }
          : { entries: [] }
    )
    readFileText.mockRejectedValue(new Error('ENOENT'))

    await discoverRuntimePlugins()

    // The listing already says there is no desktop/ half, so nothing is read.
    // Reading it and catching the miss rejects the IPC call, and Electron logs
    // a stack trace for every rejected handler — noise on every scan pass.
    expect(readDir).toHaveBeenCalledWith('/local/.hermes/plugins/my-feature')
    expect(readFileText).not.toHaveBeenCalled()
  })

  it('does not mistake a desktop/plugin.js DIRECTORY for the entry file', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    agentPluginsRoot.mockResolvedValue('/local/.hermes/plugins')
    readDir.mockImplementation(async dir => {
      if (dir === '/local/.hermes/plugins') {
        return { entries: [{ isDirectory: true, name: 'odd', path: '/local/.hermes/plugins/odd' }] }
      }

      if (dir === '/local/.hermes/plugins/odd') {
        return { entries: [{ isDirectory: true, name: 'desktop', path: '/local/.hermes/plugins/odd/desktop' }] }
      }

      if (dir === '/local/.hermes/plugins/odd/desktop') {
        return {
          entries: [{ isDirectory: true, name: 'plugin.js', path: '/local/.hermes/plugins/odd/desktop/plugin.js' }]
        }
      }

      return { entries: [] }
    })

    await discoverRuntimePlugins()

    expect(readFileText).not.toHaveBeenCalled()
  })

  it('still scans the standalone root when agentPluginsRoot is absent (older shell)', async () => {
    delete (window.hermesDesktop as unknown as { agentPluginsRoot?: unknown }).agentPluginsRoot
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    readDir.mockResolvedValue({ entries: [] })

    await discoverRuntimePlugins()

    expect(readDir).toHaveBeenCalledWith('/local/.hermes/desktop-plugins')
    expect(readDir).toHaveBeenCalledTimes(1)
  })

  it('loads a unified desktop half OPT-IN: inventoried but not activated by default', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    agentPluginsRoot.mockResolvedValue('/local/.hermes/plugins')
    readDir.mockImplementation(async dir => {
      if (dir === '/local/.hermes/plugins') {
        return { entries: [{ isDirectory: true, name: 'uni', path: '/local/.hermes/plugins/uni' }] }
      }

      if (dir === '/local/.hermes/plugins/uni') {
        return { entries: [{ isDirectory: true, name: 'desktop', path: '/local/.hermes/plugins/uni/desktop' }] }
      }

      if (dir === '/local/.hermes/plugins/uni/desktop') {
        return {
          entries: [{ isDirectory: false, name: 'plugin.js', path: '/local/.hermes/plugins/uni/desktop/plugin.js' }]
        }
      }

      return { entries: [] }
    })

    const register = vi.fn()

    ;(globalThis as unknown as { __uniRegister: unknown }).__uniRegister = register
    readFileText.mockResolvedValue({
      text: 'export default { id: "uni", register: globalThis.__uniRegister }'
    })
    watchPreviewFile.mockResolvedValue({ id: 'w-uni' })

    // The loader evaluates plugins via blob-URL import(), which vite's module
    // runner can't resolve in tests — reroute to a data: URL, which node's
    // native ESM loader handles.
    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockImplementation(
        blob =>
          `data:text/javascript;base64,${Buffer.from((blob as unknown as { parts: string[] }).parts.join('')).toString('base64')}`
      )

    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    const RealBlob = globalThis.Blob
    vi.stubGlobal(
      'Blob',
      class {
        parts: string[]
        constructor(parts: string[]) {
          this.parts = parts
        }
      }
    )

    try {
      await discoverRuntimePlugins()

      // Inventoried for Settings → Plugins, but the root's opt-in posture wins:
      // ~/.hermes/plugins stays installed-but-inert until the user toggles it.
      expect($pluginRecords.get().uni).toMatchObject({ kind: 'disk', status: 'disabled' })
      expect(register).not.toHaveBeenCalled()

      // The user's explicit enable still activates it.
      await setPluginEnabled('uni', true)
      expect(register).toHaveBeenCalledTimes(1)
      expect($pluginRecords.get().uni.status).toBe('loaded')
    } finally {
      createObjectURL.mockRestore()
      revokeObjectURL.mockRestore()
      vi.stubGlobal('Blob', RealBlob)
      delete (globalThis as unknown as { __uniRegister?: unknown }).__uniRegister
    }
  })
})

describe('watchRuntimePlugins dir watch (#66899)', () => {
  it('watches both Electron-resolved local roots, never the backend hermes_home', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    agentPluginsRoot.mockResolvedValue('/local/.hermes/plugins')
    readDir.mockResolvedValue({ entries: [] })
    watchDirectory.mockResolvedValue({ id: 'watch-1' })

    watchRuntimePlugins()
    // Drain the async scan + startDirWatches chains.
    await vi.waitFor(() => expect(watchDirectory).toHaveBeenCalledTimes(2))

    expect(watchDirectory).toHaveBeenCalledWith('/local/.hermes/desktop-plugins')
    expect(watchDirectory).toHaveBeenCalledWith('/local/.hermes/plugins')
    expect(watchDirectory).not.toHaveBeenCalledWith('/remote/box/.hermes/desktop-plugins')
    expect(getStatus).not.toHaveBeenCalled()
  })
})
