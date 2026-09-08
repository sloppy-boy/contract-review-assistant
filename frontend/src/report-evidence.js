export const humanStatus = risk => risk.reviewDecision?.decision || risk.humanStatus || (['accepted', 'accepted_with_changes', 'rejected', 'escalated'].includes(risk.reviewStatus) ? risk.reviewStatus : 'pending')
export const confidenceLabel = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 ? `${Math.round(value * 100)}%` : '未提供'
export const sourceName = value => ({ model: '模型判断', playbook: 'Playbook 规则', rule: '确定性规则' }[value] || '来源未提供')
export const riskSources = risk => risk.sources?.length ? risk.sources : [{ source: risk.source || 'model', ...risk.sourceDetails }]

export async function requestWordExport(request, runId, report) {
  const response = await request('/api/export/word', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(runId ? { runId } : { report }),
  })
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `后端导出失败：${response.status}`)
  return response.blob()
}
