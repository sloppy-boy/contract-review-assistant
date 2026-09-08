import test from 'node:test'
import assert from 'node:assert/strict'
import { createAssetClient } from '../src/asset-api.js'

test('asset import keeps file bytes and uses authenticated transport', async () => {
  let request
  const client = createAssetClient(async (url, options) => { request = { url, ...options }; return Response.json({ results: [] }) })
  const file = new File(['合成合同'], 'sample.txt', { type: 'text/plain' })
  assert.deepEqual(await client.importFiles([file]), { results: [] })
  assert.equal(request.url, '/api/contract-assets/import')
  assert.equal(request.method, 'POST')
  assert.equal(await request.body.get('files').text(), '合成合同')
  assert.equal(request.headers, undefined)
})
test('asset updates and obligations include revisions and query values are encoded', async () => {
  const requests = []
  const client = createAssetClient(async (url, options = {}) => { requests.push({ url, ...options }); return Response.json({ id: 'a' }) })
  await client.update('a', { expectedRevision: 3, title: '新名称' })
  await client.addObligation('a', { expectedRevision: 4, title: '续签', dueAt: '2030-01-01T00:00:00Z', kind: 'renewal' })
  await client.completeObligation('a', 'b', 5)
  await client.search('付款 & 续签', 'semantic')
  assert.equal(requests[0].method, 'PATCH')
  assert.equal(JSON.parse(requests[1].body).expectedRevision, 4)
  assert.deepEqual(JSON.parse(requests[2].body), { expectedRevision: 5 })
  assert.equal(new URL(requests[3].url, 'http://test').searchParams.get('q'), '付款 & 续签')
  assert.equal(new URL(requests[3].url, 'http://test').searchParams.get('mode'), 'semantic')
})
test('asset client exposes HTTP conflicts without losing status', async () => {
  const client = createAssetClient(async () => Response.json({ detail: 'revision conflict' }, { status: 409 }))
  await assert.rejects(client.get('a'), error => error.status === 409 && error.message === 'revision conflict')
})

test('version requests preserve bytes, baseline and report identity', async () => {
  const requests = []
  const client = createAssetClient(async (url, options = {}) => {
    requests.push({ url, ...options })
    return url.endsWith('/original') ? new Response('old bytes') : Response.json({ versions: [] })
  })
  await client.versions('a/b')
  await client.version('a/b', 1)
  await client.addVersion('a/b', new File(['new bytes'], 'new.txt'), 4)
  await client.diff('a/b', 1, 3, 'report & id')
  assert.equal(await (await client.download('a/b', 1)).text(), 'old bytes')
  assert.equal(requests[0].url, '/api/contract-assets/a%2Fb/versions')
  assert.equal(requests[1].url, '/api/contract-assets/a%2Fb/versions/1')
  assert.equal(requests[2].body.get('expectedRevision'), '4')
  assert.equal(await requests[2].body.get('file').text(), 'new bytes')
  const query = new URL(requests[3].url, 'http://test').searchParams
  assert.equal(query.get('from'), '1')
  assert.equal(query.get('to'), '3')
  assert.equal(query.get('runId'), 'report & id')
  assert.equal(requests[4].url, '/api/contract-assets/a%2Fb/versions/1/original')
})
