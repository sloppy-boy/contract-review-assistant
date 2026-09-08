import { readFileSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { computed, reactive, ref } from 'vue'

function page(overrides = {}) {
  const source = readFileSync(new URL('../src/views/CollaborationView.vue', import.meta.url), 'utf8').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const client = { get: async id => ({ id, status: 'draft', revision: 1 }), events: async () => ({ events: [] }), list: async () => ({ requests: [] }), notifications: async () => ({ notifications: [] }), me: async () => ({ subject: 'new' }), members: async () => ({ members: [] }), ...overrides }
  let changeWorkspace
  const setup = new Function('computed', 'reactive', 'ref', 'watch', 'onMounted', 'ElMessage', 'apiFetch', 'fetchReviewHistory', 'store', 'createCollaborationClient', source + '\nreturn {addComment,selectRequest,selected,commentBody,loadAll,me,requests}')
  const result = setup(computed, reactive, ref, (_, fn) => { changeWorkspace = fn }, () => {}, { error() {} }, () => {}, async () => [], { mode: 'online' }, () => client)
  return { ...result, changeWorkspace: () => changeWorkspace() }
}
test('a delayed comment preserves navigation and the other request draft', async () => {
  let release
  const view = page({ comment: () => new Promise(resolve => { release = resolve }) })
  await view.selectRequest('A')
  view.commentBody.value = 'A comment'
  const saving = view.addComment()
  await view.selectRequest('B')
  view.commentBody.value = 'B draft'
  release({ id: 'A' })
  await saving
  assert.equal(view.selected.value.id, 'B')
  assert.equal(view.commentBody.value, 'B draft')
})
test('old workspace responses cannot restore the previous identity', async () => {
  let release, calls = 0
  const view = page({ me: () => ++calls === 1 ? new Promise(resolve => { release = resolve }) : Promise.resolve({ subject: 'new' }) })
  const old = view.loadAll()
  view.changeWorkspace()
  await new Promise(resolve => setImmediate(resolve))
  release({ subject: 'old' })
  await old
  assert.equal(view.me.value.subject, 'new')
})
