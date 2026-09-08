export function createAssetClient(transport) {
  const root = '/api/contract-assets'
  const id = encodeURIComponent
  async function request(path, method = 'GET', body) {
    const options = { method }
    if (body !== undefined) {
      options.body = body instanceof FormData ? body : JSON.stringify(body)
      if (!(body instanceof FormData)) options.headers = { 'Content-Type': 'application/json' }
    }
    const response = await transport(root + path, options)
    if (!response.ok) {
      const result = await response.json().catch(() => ({}))
      const detail = Array.isArray(result.detail) ? result.detail.map(item => item.msg).join('；') : result.detail
      const error = new Error(detail || '合同操作失败')
      error.status = response.status
      throw error
    }
    return response.json()
  }
  return {
    importFiles(files) { const form = new FormData(); files.forEach(file => form.append('files', file)); return request('/import', 'POST', form) },
    list: (q = '', tag = '') => request(`?${new URLSearchParams({ q, tag })}`),
    get: assetId => request(`/${id(assetId)}`),
    versions: assetId => request(`/${id(assetId)}/versions`),
    version: (assetId, version) => request(`/${id(assetId)}/versions/${id(version)}`),
    addVersion(assetId, file, expectedRevision) { const form = new FormData(); form.append('file', file); form.append('expectedRevision', expectedRevision); return request(`/${id(assetId)}/versions`, 'POST', form) },
    diff(assetId, from, to, runId = '') { const query = new URLSearchParams({ from, to }); if (runId.trim()) query.set('runId', runId.trim()); return request(`/${id(assetId)}/diff?${query}`) },
    update: (assetId, body) => request(`/${id(assetId)}`, 'PATCH', body),
    search: (q, mode = 'keyword') => request(`/search?${new URLSearchParams({ q, mode })}`),
    ask: question => request('/ask', 'POST', { question }),
    obligations: assetId => request(`/${id(assetId)}/obligations`),
    addObligation: (assetId, body) => request(`/${id(assetId)}/obligations`, 'POST', body),
    completeObligation: (assetId, obligationId, expectedRevision) => request(`/${id(assetId)}/obligations/${id(obligationId)}/complete`, 'POST', { expectedRevision }),
    notifications: () => request('/notifications'),
    checkReminders: () => request('/reminders/check', 'POST'),
    markRead: notificationId => request(`/notifications/${id(notificationId)}/read`, 'POST'),
    async download(assetId, version) {
      const path = version === undefined ? '/original' : `/versions/${id(version)}/original`
      const response = await transport(`${root}/${id(assetId)}${path}`)
      if (!response.ok) throw new Error('无法下载原件，请刷新访问权限')
      return response.blob()
    },
  }
}
