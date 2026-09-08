export function createPlaybookClient(transport) {
  async function request(path, method = 'GET', body) {
    const response = await transport('/api/playbooks' + path, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) { const e = new Error(data.detail || 'Playbook 请求失败'); e.status = response.status; throw e }
    return data
  }
  return { list: () => request(''), versions: id => request(`/${encodeURIComponent(id)}/versions`), events: id => request(`/${encodeURIComponent(id)}/events`), get: (id, version) => request(`/${encodeURIComponent(id)}/versions/${version}`), create: body => request('', 'POST', body), update: (id, version, body) => request(`/${encodeURIComponent(id)}/versions/${version}`, 'PUT', body), derive: (id, sourceVersion) => request(`/${encodeURIComponent(id)}/versions`, 'POST', { sourceVersion }), publish: (id, version) => request(`/${encodeURIComponent(id)}/versions/${version}/publish`, 'POST'), archive: (id, version) => request(`/${encodeURIComponent(id)}/versions/${version}/archive`, 'POST') }
}
