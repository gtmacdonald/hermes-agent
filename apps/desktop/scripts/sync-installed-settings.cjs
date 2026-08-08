// Copy the installed app's renderer settings into the dev app.
//
// Desktop settings live in two places. Main-process state (connection.json,
// native-theme.json, window-state.json, ...) sits in userData, which dev and
// the installed app already share. Renderer preferences -- theme/skin, mode,
// statusbar, review layout, per-profile appearance -- live in localStorage,
// which Chromium partitions BY ORIGIN. The installed app loads the renderer
// from `file://`; dev loads it from the Vite server at http://127.0.0.1:5174
// (electron/main.ts, `DEV_SERVER`). Same userData, same leveldb, two disjoint
// stores -- which is why a dev run comes up with default appearance no matter
// what you picked in the installed app.
//
// Nothing outside a browser context can read those stores, so this runs as
// Electron against the same userData, reads `file://`, and writes the dev
// origin:
//
//   npx electron scripts/sync-installed-settings.cjs
//
// Run it with the desktop STOPPED (both the dev app and the installed one):
// a running app holds the leveldb lock, and dev holds port 5174.
//
// CommonJS on purpose -- an ESM Electron entry point never fires `ready`.
const { app, BrowserWindow } = require('electron')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const DEV_ORIGIN = process.env.HERMES_DESKTOP_DEV_SERVER || 'http://127.0.0.1:5174'

// Same two knobs main.ts uses, in the same order: the name decides userData,
// an explicit dir overrides it. Without this we'd read some other store.
app.setName(process.env.HERMES_DESKTOP_APP_NAME || 'Hermes')

if (process.env.HERMES_DESKTOP_USER_DATA_DIR) {
  app.setPath('userData', path.resolve(process.env.HERMES_DESKTOP_USER_DATA_DIR))
}

function die(message) {
  console.error(`sync-installed-settings: ${message}`)
  app.exit(1)
}

// A page on the dev origin has to exist before we can write its localStorage,
// and Vite isn't running (we require the port free). Serve a blank one.
function serveBlankDevOrigin(url) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((_req, res) => {
      res.writeHead(200, { 'Content-Type': 'text/html' })
      res.end('<!doctype html><title>hermes settings sync</title>')
    })

    server.once('error', reject)
    server.listen(Number(url.port), url.hostname, () => resolve(server))
  })
}

function readAll(win) {
  return win.webContents.executeJavaScript(
    '(() => { const o = {}; for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); o[k] = localStorage.getItem(k) } return o })()'
  )
}

function writeAll(win, entries) {
  return win.webContents.executeJavaScript(
    `(() => { const o = JSON.parse(${JSON.stringify(JSON.stringify(entries))});
      for (const [k, v] of Object.entries(o)) localStorage.setItem(k, v)
      return localStorage.length })()`
  )
}

async function main() {
  const devUrl = new URL(DEV_ORIGIN)
  let server

  try {
    server = await serveBlankDevOrigin(devUrl)
  } catch (error) {
    return die(
      error.code === 'EADDRINUSE'
        ? `${devUrl.origin} is already serving. Stop the dev app (scripts/dev.sh) and run this again.`
        : `could not serve ${devUrl.origin}: ${error.message}`
    )
  }

  const blank = path.join(os.tmpdir(), 'hermes-settings-sync.html')
  fs.writeFileSync(blank, '<!doctype html><title>hermes settings sync</title>')

  const win = new BrowserWindow({ show: false })

  console.log(`userData: ${app.getPath('userData')}`)

  await win.loadURL(pathToFileURL(blank).toString())
  const installed = await readAll(win)
  const keys = Object.keys(installed)

  if (keys.length === 0) {
    return die(
      `no settings found in the installed app's file:// store under ${app.getPath('userData')}. ` +
        'Quit Hermes if it is running (it holds the store open), or check HERMES_DESKTOP_USER_DATA_DIR.'
    )
  }

  await win.loadURL(devUrl.origin + '/')
  const total = await writeAll(win, installed)

  console.log(`copied ${keys.length} settings from file:// to ${devUrl.origin} (${total} keys there now)`)

  for (const key of ['hermes-desktop-skin-v1', 'hermes-desktop-mode-v1']) {
    if (installed[key]) {
      console.log(`  ${key}: ${installed[key]}`)
    }
  }

  server.close()
  fs.rmSync(blank, { force: true })
  win.destroy()
  app.quit()
}

app.whenReady().then(main)
