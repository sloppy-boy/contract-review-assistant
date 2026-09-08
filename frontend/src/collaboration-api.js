// A transport is injected so all browser requests share workspace authentication.
export function createCollaborationClient(transport) {
  const root = '/api/collaboration'
  const requestPath = (id) => `/requests/${encodeURIComponent(id)}`
  async function request(path, method = 'GET', body) {
    const response = await transport(root + path, {
      method,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => `${(item.loc || []).join('.')}: ${item.msg}`).join('；')
        : data.detail
      const error = new Error(detail || `请求失败（HTTP ${response.status}）`)
      error.status = response.status
      throw error
    }
    return data
  }
  const action = (id, suffix, data) => request(`${requestPath(id)}/${suffix}`, 'POST', data)
  return {
    me: () => request('/me'),
    members: () => request('/members'),
    calculateSla: (data) => request('/sla/calculate', 'POST', data),
    list: (status = '') => request(`/requests${status ? `?status=${encodeURIComponent(status)}` : ''}`),
    create: (data) => request('/requests', 'POST', data),
    get: (id) => request(requestPath(id)),
    events: (id) => request(`${requestPath(id)}/events`),
    submit: (id, expectedRevision) => action(id, 'submit', { expectedRevision }),
    assign: (id, data) => action(id, 'assign', data),
    bindRun: (id, data) => action(id, 'run', data),
    requestApproval: (id, expectedRevision) => action(id, 'request-approval', { expectedRevision }),
    decide: (id, data) => action(id, 'decide', data),
    cancel: (id, data) => action(id, 'cancel', data),
    comment: (id, data) => action(id, 'comments', data),
    notifications: (unreadOnly = false) => request(`/notifications${unreadOnly ? '?unread_only=true' : ''}`),
    readNotification: (id) => request(`/notifications/${encodeURIComponent(id)}/read`, 'POST'),
  }
}
