import assert from 'node:assert/strict'
import test from 'node:test'
import { humanStatus, confidenceLabel, riskSources, requestWordExport } from '../src/report-evidence.js'

test('legacy reports remain pending; confidence is never invented', () => {
  assert.equal(humanStatus({ reviewStatus: 'not_required' }), 'pending')
  assert.equal(humanStatus({ reviewStatus: 'accepted' }), 'accepted')
  assert.equal(humanStatus({ reviewDecision: { decision: 'pending' }, humanStatus: 'accepted' }), 'pending')
  for (const unknown of [null, undefined, -1, 2, '0.9', NaN]) assert.equal(confidenceLabel(unknown), '未提供')
  assert.equal(confidenceLabel(0.75), '75%')
  assert.deepEqual(riskSources({ source: 'playbook', sourceDetails: { ruleId: 'r1' }, sources: [] }), [{ source: 'playbook', ruleId: 'r1' }])
})

test('online Word export sends only the run ID through authenticated request', async () => {
  let payload
  const result = await requestWordExport(async (url, options) => {
    assert.equal(url, '/api/export/word')
    payload = JSON.parse(options.body)
    return new Response('synthetic docx')
  }, 'run-1', { reviewDecision: 'untrusted' })
  assert.deepEqual(payload, { runId: 'run-1' })
  assert.equal(await result.text(), 'synthetic docx')
})

test('legacy Word export uses explicit report path', async () => {
  await requestWordExport(async (_, options) => {
    assert.deepEqual(JSON.parse(options.body), { report: { risks: [] } })
    return new Response('docx')
  }, null, { risks: [] })
})
