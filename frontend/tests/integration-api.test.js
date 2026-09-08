import test from 'node:test'
import assert from 'node:assert/strict'
import { createIntegrationClient } from '../src/integration-api.js'

test('integration client keeps secrets in write payloads and exposes delivery metadata', async () => {
  const calls = []
  const transport = async (path, options) => {
    calls.push({ path, options })
    return { ok: true, json: async () => ({ integrations: [], deliveries: [] }) }
  }
  const client = createIntegrationClient(transport)
  await client.list()
  await client.deliveries()
  await client.save('ticket', { integrationKind: 'ticket', url: 'https://hooks.example.com', allowedHosts: ['hooks.example.com'], secret: 'test-secret', enabled: true })
  await client.remove('ticket')
  assert.equal(calls[0].path, '/api/platform/integrations')
  assert.equal(calls[1].path, '/api/platform/integrations/deliveries')
  assert.equal(calls[2].options.method, 'PUT')
  assert.match(calls[2].options.body, /test-secret/)
  assert.equal(calls[3].options.method, 'DELETE')
})
