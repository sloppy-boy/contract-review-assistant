import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import test from 'node:test'

async function fixture(t, respond = () => ({ status: 200, body: { ok: true } })) {
  const received = []
  const server = createServer(async (req, res) => {
    let body = ''
    for await (const chunk of req) body += chunk
    received.push({ method: req.method, url: req.url, headers: req.headers, body: body ? JSON.parse(body) : null })
    const reply = respond(req)
    res.writeHead(reply.status, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(reply.body))
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise((resolve) => server.close(resolve)))
  const { createCollaborationClient } = await import('../src/collaboration-api.js')
  const base = `http://127.0.0.1:${server.address().port}`
  const client = createCollaborationClient((path, options) => fetch(new URL(path, base), options))
  return { client, received }
}

test('create and assignment send explicit revision and domain payload over HTTP', async (t) => {
  const { client, received } = await fixture(t)
  await client.create({ title: 'Synthetic request', contractType: 'purchase' })
  await client.assign('r/1', { expectedRevision: 2, assignee: 'alice', approver: 'bob', dueAt: '2099-01-01T00:00:00Z' })
  assert.equal(received[0].method, 'POST')
  assert.equal(received[0].url, '/api/collaboration/requests')
  assert.deepEqual(received[0].body, { title: 'Synthetic request', contractType: 'purchase' })
  assert.equal(received[1].url, '/api/collaboration/requests/r%2F1/assign')
  assert.equal(received[1].body.expectedRevision, 2)
  assert.equal(received[1].headers['content-type'], 'application/json')
})

test('approval workflow includes report ID and explicit human reason', async (t) => {
  const { client, received } = await fixture(t)
  await client.submit('r1', 1)
  await client.bindRun('r1', { expectedRevision: 3, runId: 'run1' })
  await client.requestApproval('r1', 4)
  await client.decide('r1', { expectedRevision: 5, decision: 'changes_requested', reason: 'Missing evidence' })
  await client.cancel('r1', { expectedRevision: 6, reason: 'Cancelled by requester' })
  assert.deepEqual(received.map(({ url }) => url), [
    '/api/collaboration/requests/r1/submit', '/api/collaboration/requests/r1/run',
    '/api/collaboration/requests/r1/request-approval', '/api/collaboration/requests/r1/decide',
    '/api/collaboration/requests/r1/cancel',
  ])
  assert.deepEqual(received[0].body, { expectedRevision: 1 })
  assert.deepEqual(received[1].body, { expectedRevision: 3, runId: 'run1' })
  assert.deepEqual(received[2].body, { expectedRevision: 4 })
  assert.equal(received[3].body.reason, 'Missing evidence')
})

test('comments and notification reads use dedicated endpoints', async (t) => {
  const { client, received } = await fixture(t)
  await client.comment('r1', { expectedRevision: 4, body: 'Please review', mentions: ['bob'] })
  await client.notifications(true)
  await client.readNotification(7)
  assert.deepEqual(received[0].body.mentions, ['bob'])
  assert.equal(received[0].url, '/api/collaboration/requests/r1/comments')
  assert.equal(received[1].url, '/api/collaboration/notifications?unread_only=true')
  assert.equal(received[2].method, 'POST')
  assert.equal(received[2].url, '/api/collaboration/notifications/7/read')
})

test('tenant identity, members and filtered reads return parsed server data', async (t) => {
  const { client, received } = await fixture(t, () => ({ status: 200, body: { subject: 'alice', requests: [] } }))
  assert.equal((await client.me()).subject, 'alice')
  await client.members()
  await client.list('pending_approval')
  await client.get('r1')
  await client.events('r1')
  assert.deepEqual(received.map(({ url }) => url), [
    '/api/collaboration/me', '/api/collaboration/members', '/api/collaboration/requests?status=pending_approval',
    '/api/collaboration/requests/r1', '/api/collaboration/requests/r1/events',
  ])
  assert.ok(received.every(({ method }) => method === 'GET'))
})

test('stale changes are surfaced with status so callers can refresh', async (t) => {
  const { client } = await fixture(t, () => ({ status: 409, body: { detail: 'revision conflict' } }))
  await assert.rejects(client.submit('r1', 1), (error) => error.status === 409 && error.message.includes('revision conflict'))
})

test('validation arrays show a readable field error', async (t) => {
  const { client } = await fixture(t, () => ({ status: 422, body: { detail: [{ loc: ['body', 'dueAt'], msg: 'timezone required' }] } }))
  await assert.rejects(client.assign('r1', {}), /dueAt.*timezone required/)
})

test('business calendar SLA calculation is sent to the authenticated API', async (t) => {
  const { client, received } = await fixture(t, () => ({ status: 200, body: { dueAt: '2030-01-01T00:00:00Z' } }))
  const result = await client.calculateSla({ startedAt: '2029-12-31T00:00:00Z', businessMinutes: 480 })
  assert.equal(result.dueAt, '2030-01-01T00:00:00Z')
  assert.equal(received[0].url, '/api/collaboration/sla/calculate')
  assert.equal(received[0].method, 'POST')
})
