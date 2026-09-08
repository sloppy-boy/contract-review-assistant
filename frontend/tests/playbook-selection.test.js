import test from 'node:test'
import assert from 'node:assert/strict'

test('Playbook selection is sent with an explicit immutable version', async () => {
  const calls = []
  global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  const originalFetch = global.fetch
  global.fetch = async (url, options = {}) => {
    calls.push({ url, options })
    if (String(url).includes('/playbooks')) return new Response(JSON.stringify({ playbooks: [{ playbookId: 'p1', version: 2, status: 'active', content: { contractType: 'purchase', jurisdiction: 'CN', businessScenario: 'general', effectiveScope: ['procurement'] } }, { playbookId: 'p2', version: 1, status: 'draft', content: { contractType: 'purchase' } }] }), { status: 200 })
    if (String(url).includes('/report/')) return new Response(JSON.stringify({ status: 'done', report: { risks: [] } }), { status: 200 })
    return new Response(JSON.stringify({ taskId: 'run-1' }), { status: 200 })
  }
  try {
    const api = await import(`../src/api.js?selection=${Date.now()}`)
    const books = await api.fetchPlaybooks('purchase')
    assert.deepEqual(books.map(item => item.playbookId), ['p1'])
    const promise = api.uploadAndReview('合同文本', 'purchase', undefined, undefined, books[0])
    await promise
    const body = new URLSearchParams(calls[1].options.body)
    assert.equal(body.get('playbook_id'), 'p1')
    assert.equal(body.get('playbook_version'), '2')
    assert.equal(body.get('effective_scope'), 'procurement')
  } finally { global.fetch = originalFetch }
})
