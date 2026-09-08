import test from 'node:test'
import assert from 'node:assert/strict'

test('apiFetch prefixes requests with configured API base URL', async () => {
  const originalFetch = globalThis.fetch
  const originalEnv = process.env.VITE_API_BASE_URL
  process.env.VITE_API_BASE_URL = 'https://api.example.test/'
  globalThis.__CRA_API_BASE_URL = process.env.VITE_API_BASE_URL
  globalThis.localStorage = { getItem: () => '', setItem() {}, removeItem() {} }
  const calls = []
  globalThis.fetch = async (input) => { calls.push(String(input)); return { ok: true } }
  const { apiFetch } = await import(new URL(`../src/api.js?test=${Date.now()}`, import.meta.url))
  await apiFetch('/api/health')
  assert.equal(calls[0], 'https://api.example.test/api/health')
  globalThis.fetch = originalFetch
  if (originalEnv === undefined) delete process.env.VITE_API_BASE_URL
  else process.env.VITE_API_BASE_URL = originalEnv
  delete globalThis.__CRA_API_BASE_URL
})
