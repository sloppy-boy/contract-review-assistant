import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

function storage() {
  const values = new Map()
  return { getItem: key => values.get(key) || '', setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
}

test('first protected request creates and sends a visitor session', async () => {
  globalThis.localStorage = storage()
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), headers: new Headers(options.headers || {}) })
    if (String(url).endsWith('/api/visitor/session')) return { ok: true, status: 200, json: async () => ({ token: 'visitor-one', role: 'visitor' }) }
    return { ok: true, status: 200 }
  }
  const { apiFetch } = await import(new URL(`../src/api.js?visitor=${Date.now()}`, import.meta.url))

  await apiFetch('/api/review-runs')

  assert.equal(calls.length, 2)
  assert.match(calls[0].url, /\/api\/visitor\/session$/)
  assert.equal(calls[1].headers.get('Authorization'), 'Bearer visitor-one')
})

test('visitor request replaces an expired token and retries once', async () => {
  globalThis.localStorage = storage()
  globalThis.localStorage.setItem('cra_visitor_token', 'expired')
  let protectedCalls = 0
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).endsWith('/api/visitor/session')) return { ok: true, status: 200, json: async () => ({ token: 'replacement', role: 'visitor' }) }
    protectedCalls++
    if (protectedCalls === 1) return { ok: false, status: 401 }
    assert.equal(new Headers(options.headers).get('Authorization'), 'Bearer replacement')
    return { ok: true, status: 200 }
  }
  const { apiFetch } = await import(new URL(`../src/api.js?retry=${Date.now()}`, import.meta.url))

  const response = await apiFetch('/api/review-runs')

  assert.equal(response.status, 200)
  assert.equal(protectedCalls, 2)
})

test('public workbench does not ask visitors for a workspace key', () => {
  const source = fs.readFileSync(new URL('../src/views/Workbench.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /工作区访问密钥/)
})

test('history reports authentication and rate-limit failures clearly', async () => {
  globalThis.localStorage = storage()
  globalThis.fetch = async (url) => {
    if (String(url).endsWith('/api/visitor/session')) return { ok: true, status: 200, json: async () => ({ token: 'visitor', role: 'visitor' }) }
    return { ok: false, status: 429, json: async () => ({ detail: '访客审查次数已达上限' }) }
  }
  const { fetchReviewHistory } = await import(new URL(`../src/api.js?history=${Date.now()}`, import.meta.url))

  await assert.rejects(fetchReviewHistory(), /请求过于频繁/)
})
