import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { test } from 'vitest'

import { resolvePrimaryProfile } from './primary-profile'

function mkHome(sticky?: string) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-primary-profile-'))

  if (sticky !== undefined) {
    fs.writeFileSync(path.join(home, 'active_profile'), sticky, 'utf8')
  }

  return home
}

test('stored desktop preference wins over the sticky file', () => {
  assert.equal(resolvePrimaryProfile(mkHome('studio\n'), 'coder'), 'coder')
})

test('unset preference falls back to the sticky active_profile the backend would read', () => {
  assert.equal(resolvePrimaryProfile(mkHome('studio\n'), null), 'studio')
})

test('no sticky file, an empty one, or an explicit default means nothing is pinned', () => {
  assert.equal(resolvePrimaryProfile(mkHome(), null), null)
  assert.equal(resolvePrimaryProfile(mkHome('  \n'), null), null)
  assert.equal(resolvePrimaryProfile(mkHome('default\n'), null), null)
})

test('a sticky name the backend resolver would reject is ignored', () => {
  assert.equal(resolvePrimaryProfile(mkHome('../escape\n'), null), null)
  assert.equal(resolvePrimaryProfile(mkHome('Studio\n'), null), null)
})

test('HERMES_HOME already inside profiles/ pins the backend itself, so the file is not consulted', () => {
  const root = mkHome()
  const home = path.join(root, 'profiles', 'coder')
  fs.mkdirSync(home, { recursive: true })
  fs.writeFileSync(path.join(home, 'active_profile'), 'studio\n', 'utf8')

  assert.equal(resolvePrimaryProfile(home, null), null)
})
