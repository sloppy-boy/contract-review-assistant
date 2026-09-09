import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

test('initial page lazily mounts API views and visitor navigation omits admin areas', () => {
  const source = fs.readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

  assert.match(source, /<Workbench v-if="activeTab === 'workbench'"/)
  assert.match(source, /<HistoryView v-if="activeTab === 'history'"/)
  assert.match(source, /const visitorTabs = \[/)
  assert.match(source, /store\.principalRole === 'admin'/)
})
