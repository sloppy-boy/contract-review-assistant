import { readFileSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { computed, ref, reactive, watch, nextTick, effectScope } from 'vue'

// Exercise the real setup scripts, following the other view tests in this repo.
function setupView(name, bindings, expose) {
  const source = readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  return new Function(...Object.keys(bindings), `${source}\nreturn {${expose}}`)(...Object.values(bindings))
}

function workbench(t, overrides = {}) {
  const readers = [], calls = [], messages = []
  const activated = [], deactivated = []
  const values = new Map()
  const store = reactive({ mode: 'online', running: false, contractType: 'purchase', balance: null, balanceAvailable: null, assetDraft: null, principalRole: 'visitor' })
  const scope = effectScope()
  t.after(() => scope.stop())
  const view = scope.run(() => setupView('views/Workbench.vue', {
    computed, ref, watch, nextTick, onMounted() {}, onUnmounted() {}, onActivated: fn => activated.push(fn), onDeactivated: fn => deactivated.push(fn), defineExpose() {},
    defineProps: () => ({ active: true }), defineEmits: () => () => {}, store,
    DEMO_CONTRACTS: [{ id: 'demo_high' }, { id: 'demo_clean' }, { id: 'demo_boundary' }], STAGES: ['抽取', '识别', '复核', '报告'],
    fetchPlaybooks: async () => [], resumeReview: async () => ({}), loadDemoReport: async () => { calls.push('demo'); return {} },
    uploadAndReview: async (text) => { calls.push(text); return { taskId: 'test-run', report: {} } },
    ElMessage: Object.fromEntries(['success', 'warning', 'info', 'error'].map(key => [key, value => messages.push(value)])), ElMessageBox: {},
    localStorage: { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
    FileReader: class { constructor() { readers.push(this) } readAsText() {} },
    setInterval: () => 1, clearInterval() {}, ...overrides,
  }, 'text, pasteText, fileName, handleFile, reviewText, loadDemo, error, resumePersistedReview'))
  const read = (index, result) => { readers[index].result = result; readers[index].onload() }
  return { ...view, read, readers, calls, messages, store, values,
    activate: async () => { for (const fn of activated) await fn() },
    deactivate: () => deactivated.forEach(fn => fn()),
  }
}

test('uploaded text is editable and the edited text is submitted', async t => {
  const view = workbench(t)
  view.handleFile({ name: 'contract.txt' })
  view.read(0, '最初的合同正文')
  assert.equal(view.pasteText.value, '最初的合同正文')
  view.pasteText.value = '修改后的合同正文'
  await view.reviewText()
  assert.deepEqual(view.calls, ['修改后的合同正文'])
})

test('unsupported files never replace a valid contract or enter a text reader', t => {
  const view = workbench(t)
  view.pasteText.value = '保留的合同'
  view.handleFile({ name: 'contract.pdf' })
  assert.equal(view.readers.length, 0)
  assert.equal(view.pasteText.value, '保留的合同')
})

test('late file reads cannot overwrite a newer file selection', t => {
  const view = workbench(t)
  view.handleFile({ name: 'old.txt' })
  view.handleFile({ name: 'new.md' })
  view.read(1, '新正文')
  view.read(0, '旧正文')
  assert.equal(view.pasteText.value, '新正文')
  assert.equal(view.fileName.value, 'new.md')
})

test('running reviews reject another submit, demo, and file replacement', async t => {
  const view = workbench(t)
  view.pasteText.value = '当前合同'
  view.store.running = true
  await view.reviewText()
  await view.loadDemo('demo_clean')
  view.handleFile({ name: 'other.txt' })
  assert.deepEqual(view.calls, [])
  assert.equal(view.readers.length, 0)
})

test('returning to the workbench resumes a retained review after a connection failure', async t => {
  let attempts = 0
  const view = workbench(t, { resumeReview: async () => {
    if (++attempts === 1) throw new Error('连接中断，可稍后返回工作台继续查看')
    return { taskId: 'retained-run', report: { summary: {} } }
  } })
  const saved = { taskId: 'retained-run', contractName: '合同' }
  view.values.set('cra_active_review_run', JSON.stringify(saved))
  await view.resumePersistedReview(saved)
  assert.equal(view.store.running, false)
  view.deactivate()
  await view.activate()
  await nextTick()
  assert.equal(attempts, 2)
  assert.equal(view.store.reviewRunId, 'retained-run')
  assert.equal(view.values.has('cra_active_review_run'), false)
})

test('reactivating the workbench never starts duplicate polling while a review is running', async t => {
  let attempts = 0, finish
  const view = workbench(t, { resumeReview: () => { attempts++; return new Promise(resolve => { finish = resolve }) } })
  const saved = { taskId: 'active-run' }
  view.values.set('cra_active_review_run', JSON.stringify(saved))
  const review = view.resumePersistedReview(saved)
  view.deactivate()
  await view.activate()
  assert.equal(attempts, 1)
  finish({ taskId: 'active-run', report: {} })
  await review
})

function history(fetchReviewHistory) {
  return setupView('views/HistoryView.vue', { ref, computed, onMounted() {}, defineProps: () => ({}), defineEmits: () => () => {}, fetchReviewHistory, ElMessage: { error() {} } }, 'runs, status, loading, load, statusName')
}

test('a slower history filter response cannot replace the latest results', async () => {
  let release
  const view = history(status => status === 'done' ? new Promise(resolve => { release = resolve }) : Promise.resolve([{ id: 'failed-run' }]))
  view.status.value = 'done'
  const old = view.load()
  view.status.value = 'failed'
  await view.load()
  release([{ id: 'done-run' }])
  await old
  assert.equal(view.runs.value[0].id, 'failed-run')
})

test('all terminal history states have readable labels', () => {
  const view = history(async () => [])
  assert.equal(view.statusName('cancelled'), '已取消')
  assert.equal(view.statusName('dead_letter'), '未完成')
})

test('workbench is retained across tab navigation without mounting other API views', () => {
  const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  assert.match(source, /<KeepAlive>[\s\S]*?<Workbench v-if="activeTab === 'workbench'"[\s\S]*?<\/KeepAlive>/)
  assert.match(source, /<HistoryView v-if="activeTab === 'history'"/)
})

test('online report disposition controls require an authorized role', () => {
  const source = readFileSync(new URL('../src/views/ReportDetail.vue', import.meta.url), 'utf8')
  assert.match(source, /v-if="canReview" class="human-review-actions"/)
  assert.match(source, /\['admin', 'legal_reviewer'\]\.includes\(store\.principalRole\)/)
})

test('risk filtering and clause navigation preserve the original report', () => {
  const report = { summary: { total: 2 }, risks: [{ id: 'low', severity: 'low', clauseId: '1' }, { id: 'high', severity: 'high', clauseId: '2' }], clauses: [{ clauseId: '2', quote: '完整条款正文' }] }
  const view = setupView('views/ReportDetail.vue', {
    computed, ref, defineProps: () => ({ report, reviewRunId: 'run' }), defineEmits: () => () => {}, store: { principalRole: 'visitor' },
  }, 'activeClause, canReview, filteredRisks: typeof filteredRisks === "undefined" ? null : filteredRisks, severity: typeof severity === "undefined" ? null : severity, selectedClause: typeof selectedClause === "undefined" ? null : selectedClause')
  assert.ok(view.filteredRisks, 'the report offers a severity filter')
  assert.deepEqual(view.filteredRisks.value.map(risk => risk.id), ['high', 'low'])
  view.severity.value = 'high'
  assert.deepEqual(view.filteredRisks.value.map(risk => risk.id), ['high'])
  view.activeClause.value = '2'
  assert.equal(view.selectedClause.value.quote, '完整条款正文')
  assert.equal(view.canReview.value, false)
  assert.deepEqual(report.risks.map(risk => risk.id), ['low', 'high'])
})

test('opening a running history item selects it before the cached workbench activates', async () => {
  let saved = JSON.stringify({ taskId: 'older-run' }), selectedOnActivation
  const view = setupView('App.vue', {
    computed, ref, watch() {}, onMounted() {}, onUnmounted() {},
    store: reactive({ principalRole: 'visitor', mode: 'online', running: false }), FRONT_VERSION: 'test',
    fetchReviewRun: async () => ({ id: 'chosen-run', status: 'running' }),
    localStorage: { setItem: (_, value) => { saved = value } },
    nextTick: async () => { selectedOnActivation = JSON.parse(saved).taskId },
    ElMessage: { error: message => assert.fail(message), info: message => assert.fail(message) },
  }, 'openHistoryRun, workbench')
  view.workbench.value = { resumePersistedReview() {} }
  await view.openHistoryRun({ id: 'chosen-run', contract_type: 'purchase' })
  assert.equal(selectedOnActivation, 'chosen-run')
})
