import { readFileSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { computed, reactive, ref } from 'vue'

function page(overrides = {}) {
  const source = readFileSync(new URL('../src/views/AssetsView.vue', import.meta.url), 'utf8').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const emitted = []
  const client = {
    get: async id => ({ id, title: id, version: 2, revision: 3, text: 'latest', filename: 'new.txt', tags: [], sharedWith: [], warnings: [] }),
    versions: async () => ({ versions: [{ version: 2 }, { version: 1 }] }),
    version: async (_, version) => ({ version, text: `version ${version}`, filename: `v${version}.txt` }),
    obligations: async () => ({ obligations: [] }), notifications: async () => ({ notifications: [] }),
    list: async () => ({ assets: [] }), ...overrides,
  }
  let workspaceChange
  const setup = new Function('computed', 'reactive', 'ref', 'watch', 'onMounted', 'defineEmits', 'apiFetch', 'store', 'createAssetClient', 'createCollaborationClient', source + '\nreturn {selectAsset,selectVersion,reviewVersion,compareVersions,versionDocument,selected,comparison,compareFrom,compareTo,reportRunId}')
  const view = setup(computed, reactive, ref, (_, callback) => { workspaceChange = callback }, () => {}, () => (...args) => emitted.push(args), () => {}, { mode: 'online' }, () => client, () => ({ me: async () => ({}), members: async () => ({ members: [] }) }))
  return { ...view, emitted, workspaceChange: () => workspaceChange() }
}

test('the selected historical version is sent for review and comparison keeps source run', async () => {
  let request
  const view = page({ diff: async (...args) => { request = args; return { changes: [] } } })
  await view.selectAsset('A')
  await view.selectVersion(1)
  view.reviewVersion()
  assert.equal(view.emitted[0][1].id, 'A')
  assert.equal(view.emitted[0][1].text, 'version 1')
  assert.equal(view.emitted[0][1].version, 1)
  view.compareFrom.value = 1
  view.compareTo.value = 2
  view.reportRunId.value = 'verified-run'
  await view.compareVersions()
  assert.deepEqual(request, ['A', 1, 2, 'verified-run'])
})

test('late historical content cannot overwrite a newly selected asset or workspace', async () => {
  let release
  const view = page({ version: () => new Promise(resolve => { release = resolve }) })
  await view.selectAsset('A')
  const old = view.selectVersion(1)
  await view.selectAsset('B')
  release({ version: 1, text: 'secret A' })
  await old
  assert.equal(view.selected.value.id, 'B')
  assert.equal(view.versionDocument.value.text, 'latest')
  view.workspaceChange()
  assert.equal(view.versionDocument.value, null)
})
