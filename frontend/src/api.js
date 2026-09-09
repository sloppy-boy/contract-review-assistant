// 全局状态 + 在线/离线 API 封装（SPEC 2.8：离线演示模式，缓存为真实导出）
import { reactive } from 'vue'

const API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL || globalThis.__CRA_API_BASE_URL || '').replace(/\/$/, '')

// 前端版本标识（排查"页面是否最新"用：footer 显示，与后端 /health 对照）
export const FRONT_VERSION = '2026-08-17.3'

export const store = reactive({
  mode: localStorage.getItem('cra_mode') || 'online',  // online | offline（演示不依赖 API 可用性；记忆上次选择）
  report: null,
  reviewRunId: null,       // 在线审查的持久化运行 ID；人工裁决与审计事件的锚点
  contractName: '',
  contractType: 'purchase',
  assetDraft: null,
  running: false,
  stage: 0,                // 流水线阶段 0~3（后端真实进度）
  stageStatus: 'idle',     // idle | running | done（后端回传）
  stageTimes: [0, 0, 0, 0], // 各阶段实际耗时 ms（后端回传）
  stageDetail: '',          // 阶段细节（如 worker 完成 "5/13"）
  stageStartedAt: 0,        // 当前阶段开始时刻（epoch ms，前端实时计时）
  // 余额状态（/api/balance 填充；金额预警 + 停止服务提示用）
  balance: null,           // 查询所得余额（元）；null = 未查询到/离线/mock
  balanceAvailable: null,  // 账户是否可用（false = 余额耗尽/停止服务）
  balanceThreshold: 5,     // 预警阈值（元，后端下发）
  balanceQueryFailed: false,
  workspaceApiKey: localStorage.getItem('cra_workspace_api_key') || '',
  visitorToken: localStorage.getItem('cra_visitor_token') || '',
  principalRole: localStorage.getItem('cra_workspace_api_key') ? 'admin' : 'visitor',
})

export function saveWorkspaceApiKey(value) {
  store.workspaceApiKey = value.trim()
  if (store.workspaceApiKey) {
    localStorage.setItem('cra_workspace_api_key', store.workspaceApiKey)
    store.principalRole = 'admin'
  } else {
    localStorage.removeItem('cra_workspace_api_key')
    store.principalRole = 'visitor'
  }
}

let visitorSessionPromise = null

export async function ensureVisitorSession({ force = false } = {}) {
  if (store.workspaceApiKey) return null
  if (!force && store.visitorToken) return store.visitorToken
  if (!visitorSessionPromise) {
    visitorSessionPromise = fetch(`${API_BASE_URL}/api/visitor/session`, { method: 'POST' })
      .then(async (response) => {
        if (!response.ok) throw new Error('访客会话初始化失败，请稍后刷新页面重试')
        const session = await response.json()
        store.visitorToken = session.token
        store.principalRole = session.role || 'visitor'
        localStorage.setItem('cra_visitor_token', session.token)
        return session.token
      })
      .finally(() => { visitorSessionPromise = null })
  }
  return visitorSessionPromise
}

export async function apiFetch(path, options = {}) {
  const isPublicProbe = path === '/api/health' || path === '/api/visitor/session'
  if (!store.workspaceApiKey && !isPublicProbe) await ensureVisitorSession()
  const headers = new Headers(options.headers || {})
  if (store.workspaceApiKey) headers.set('X-API-Key', store.workspaceApiKey)
  else if (store.visitorToken) headers.set('Authorization', `Bearer ${store.visitorToken}`)
  const url = path.startsWith('http') ? path : `${API_BASE_URL}${path}`
  const response = await fetch(url, { ...options, headers })
  if (response.status !== 401 || store.workspaceApiKey || isPublicProbe || options.__visitorRetried) return response
  store.visitorToken = ''
  localStorage.removeItem('cra_visitor_token')
  await ensureVisitorSession({ force: true })
  return apiFetch(path, { ...options, __visitorRetried: true })
}

export const STAGES = ['条款抽取', '风险识别（13 workers 并行）', '对抗复核', '报告生成']

export const DEMO_CONTRACTS = [
  { id: 'demo_high', name: '高危缺陷合同', desc: '植入高风险缺陷（召回演示）' },
  { id: 'demo_clean', name: '干净合同', desc: '示范文本原样（低误报演示）' },
  { id: 'demo_boundary', name: '边界条款合同', desc: '争议条款（有争议标定）' },
]

// 离线：直接读真实导出的缓存报告（严禁手工编写假报告）
export async function loadDemoReport(id) {
  const resp = await fetch(`/reports/${id}.json`)
  if (!resp.ok) throw new Error(`离线报告缺失：${id}`)
  return resp.json()
}

// 在线：/api/upload → 轮询 /api/report/{taskId}（流水线阶段模拟：真实任务无阶段推送，
// 以固定节奏推进进度条可视化多 agent 扇出）
// 轮询 1s 间隔（reasoning 模型全流水线实测 2~6 分钟；上限 600s 兜底防永久挂起）
const POLL_LIMIT = 600
const POLL_INTERVAL_MS = 1000

async function pollReview(taskId, onProgress) {
  let consecutiveErrors = 0
  for (let i = 0; ; i++) {
    if (i >= POLL_LIMIT) {
      const error = new Error('审查仍在后端运行，可稍后返回工作台继续查看结果。')
      error.taskId = taskId
      throw error
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
    let rep
    try {
      rep = await apiFetch(`/api/report/${taskId}`).then((x) => x.json())
      consecutiveErrors = 0
    } catch {
      if (++consecutiveErrors >= 5) {
        const error = new Error('与后端连接中断；审查任务已保留，可稍后返回工作台继续查看。')
        error.taskId = taskId
        throw error
      }
      continue
    }
    if (rep.status === 'running' || rep.status === 'queued') {
      onProgress?.(rep)
      continue
    }
    if (rep.status === 'done') return { report: rep.report, taskId }
    if (rep.status === 'failed') {
      const error = new Error(rep.error || '流水线失败')
      error.balanceExhausted = !!rep.balanceExhausted
      error.taskId = taskId
      throw error
    }
  }
}

export async function uploadAndReview(text, contractType, onProgress, onTaskCreated, playbook = null) {
  const fields = { text, contract_type: contractType }
  if (playbook?.playbookId && playbook?.version) {
    fields.playbook_id = playbook.playbookId
    fields.playbook_version = String(playbook.version)
    fields.jurisdiction = playbook.content?.jurisdiction || 'CN'
    fields.business_scenario = playbook.content?.businessScenario || 'general'
    fields.effective_scope = playbook.content?.effectiveScope?.[0] || '*'
  }
  const resp = await apiFetch('/api/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(fields),
  })
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}))
    if (resp.status === 401) throw new Error('访问密钥无效或未填写，请在工作台填写工作区访问密钥')
    throw new Error(data.detail || `上传失败（HTTP ${resp.status}）`)
  }
  const { taskId } = await resp.json()
  onTaskCreated?.(taskId)
  return pollReview(taskId, onProgress)
}

export async function fetchPlaybooks(contractType = '') {
  const resp = await apiFetch('/api/playbooks?activeOnly=true')
  if (!resp.ok) throw new Error('Playbook 列表读取失败')
  const books = (await resp.json()).playbooks || []
  return books.filter(book => book.status === 'active' && (!contractType || book.content?.contractType === contractType))
}

export function resumeReview(taskId, onProgress) {
  return pollReview(taskId, onProgress)
}

export async function fetchReviewHistory(status = '') {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  const resp = await apiFetch(`/api/review-runs${query}`)
  if (!resp.ok) {
    if (resp.status === 401) throw new Error('访客会话已失效，请刷新页面重试')
    if (resp.status === 429) throw new Error('请求过于频繁，请稍后再试')
    throw new Error('历史记录读取失败')
  }
  return (await resp.json()).runs
}

export async function fetchReviewRun(taskId) {
  const resp = await apiFetch(`/api/report/${taskId}`)
  if (!resp.ok) throw new Error('审查记录读取失败')
  return resp.json()
}

export async function saveFindingDisposition(taskId, findingId, decision, reason = '') {
  const resp = await apiFetch(`/api/review-runs/${taskId}/findings/${findingId}/disposition`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, reason }),
  })
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}))
    throw new Error(data.detail || '保存人工裁决失败')
  }
  return resp.json()
}

// 余额探活（后端 /balance 直连 DeepSeek 账户接口；失败不阻塞使用）
export async function fetchBalance() {
  try {
    const resp = await apiFetch('/api/balance')
    if (!resp.ok) return
    const data = await resp.json()
    store.balance = data.balance ?? null
    store.balanceAvailable = data.available ?? null
    store.balanceThreshold = data.threshold ?? 5
    store.balanceQueryFailed = !!data.error && data.available === null && data.balance === null
  } catch {
    store.balance = null
    store.balanceAvailable = null
  }
}

export async function loadEvalResults() {
  const resp = await fetch('/eval-results.json')
  if (!resp.ok) return null
  return resp.json()
}

// ================================================================ 设置 / 供应商管理
// 设置页：读设置（脱敏）→ providers（baseUrl/hasKey/models/价格）+ 模型路由
export async function fetchSettings() {
  try {
    const resp = await apiFetch('/api/settings')
    if (!resp.ok) return null  // 后端未启动时 Vite 代理返回 5xx（非网络错误），同样静默跳过
    return await resp.json()
  } catch {
    return null  // 网络层失败也静默（设置页仅在在线+后端可用时有意义）
  }
}

export async function saveSettings(payload) {
  const resp = await apiFetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    const d = await resp.json().catch(() => ({}))
    throw new Error(d.detail || '保存设置失败')
  }
  return resp.json()
}

// 拉取某供应商实时模型列表（失败回退本地预置）
export async function fetchProviderModels(providerId) {
  const resp = await apiFetch(`/api/providers/${encodeURIComponent(providerId)}/models`)
  if (!resp.ok) throw new Error('模型列表获取失败')
  return resp.json()
}

// 测试供应商连通性（最小 chat 调用）
export async function testProvider(providerId, model) {
  const resp = await apiFetch(`/api/providers/${encodeURIComponent(providerId)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  })
  if (!resp.ok) throw new Error('测试请求失败')
  return resp.json()
}
