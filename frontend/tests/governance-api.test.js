import test from 'node:test'
import assert from 'node:assert/strict'
import { createGovernanceClient } from '../src/governance-api.js'

test('governance client lists tasks and exposes retry/cancel operations', async () => {
  const requests = []
  const client = createGovernanceClient(async (url, options = {}) => { requests.push({ url, ...options }); return Response.json({ tasks: [], status: 'queued' }) })
  await client.tasks({ status: 'dead', kind: 'review', limit: 20 })
  await client.retryTask('dead/id')
  await client.cancelTask('run/id')
  await client.requeueStale(600)
  assert.equal(new URL(requests[0].url, 'http://test').searchParams.get('status'), 'dead')
  assert.equal(requests[1].url, '/api/platform/tasks/dead%2Fid/retry')
  assert.equal(requests[1].method, 'POST')
  assert.equal(requests[2].url, '/api/platform/tasks/run%2Fid/cancel')
  assert.equal(requests[3].url, '/api/platform/tasks/requeue-stale?lease_seconds=600')
})
