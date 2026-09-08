export function createGovernanceClient(transport) {
  async function request(path, method = 'GET') {
    const response = await transport('/api/platform/tasks' + path, { method })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) { const error = new Error(data.detail || '队列请求失败'); error.status = response.status; throw error }
    return data
  }
  return {
    tasks: ({ status = '', kind = '', limit = 100 } = {}) => {
      const query = new URLSearchParams()
      if (status) query.set('status', status)
      if (kind) query.set('kind', kind)
      if (limit) query.set('limit', String(limit))
      return request(query.toString() ? `?${query}` : '')
    },
    getTask: id => request(`/${encodeURIComponent(id)}`),
    retryTask: id => request(`/${encodeURIComponent(id)}/retry`, 'POST'),
    cancelTask: id => request(`/${encodeURIComponent(id)}/cancel`, 'POST'),
    requeueStale: leaseSeconds => request(`/requeue-stale?lease_seconds=${encodeURIComponent(leaseSeconds || 300)}`, 'POST'),
  }
}
