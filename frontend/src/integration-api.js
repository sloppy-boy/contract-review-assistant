export function createIntegrationClient(transport) {
  async function request(path = '', method = 'GET', body) {
    const options = { method, headers: {} }
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json'
      options.body = JSON.stringify(body)
    }
    const response = await transport(`/api/platform/integrations${path}`, options)
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      const error = new Error(data.detail || '外部集成请求失败')
      error.status = response.status
      throw error
    }
    return data
  }
  return {
    list: () => request(),
    deliveries: () => request('/deliveries'),
    save: (kind, payload) => request(`/${encodeURIComponent(kind)}`, 'PUT', payload),
    remove: kind => request(`/${encodeURIComponent(kind)}`, 'DELETE'),
  }
}
