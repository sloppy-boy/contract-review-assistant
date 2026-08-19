// 全局状态 + 在线/离线 API 封装（SPEC 2.8：离线演示模式，缓存为真实导出）
import { reactive } from 'vue'

// 前端版本标识（排查"页面是否最新"用：footer 显示，与后端 /health 对照）
export const FRONT_VERSION = '2026-08-17.3'

export const store = reactive({
  mode: localStorage.getItem('cra_mode') || 'online',  // online | offline（演示不依赖 API 可用性；记忆上次选择）
  report: null,
  contractName: '',
  contractType: 'purchase',
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
})

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

export async function uploadAndReview(text, contractType, onProgress) {
  const resp = await fetch('/api/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ text, contract_type: contractType }),
  })
  if (!resp.ok) throw new Error('上传失败（后端未启动？）')
  const { taskId } = await resp.json()
  let consecutiveErrors = 0
  for (let i = 0; ; i++) {
    if (i >= POLL_LIMIT) throw new Error(`流水线处理超过 10 分钟仍未完成（深度模型审查较慢）。任务仍在后端运行，可稍后刷新页面或重新提交。`)
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
    let rep
    try {
      rep = await fetch(`/api/report/${taskId}`).then((x) => x.json())
      consecutiveErrors = 0
    } catch {
      // 网络抖动/后端瞬断：不放弃轮询，连续失败 5 次才报错
      if (++consecutiveErrors >= 5) throw new Error('与后端连接中断（连续 5 次请求失败），请检查服务是否仍在运行')
      continue
    }
    if (rep.status === 'running') {
      if (onProgress) onProgress(rep)  // 真实进度：stage / stageTimes / stageDetail
      continue
    }
    if (rep.status === 'done') return rep.report
    if (rep.status === 'failed') {
      const err = new Error(rep.error || '流水线失败')
      err.balanceExhausted = !!rep.balanceExhausted  // 余额耗尽 → 弹"停止服务"提示
      throw err
    }
  }
}

// 余额探活（后端 /balance 直连 DeepSeek 账户接口；失败不阻塞使用）
export async function fetchBalance() {
  try {
    const resp = await fetch('/api/balance')
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
    const resp = await fetch('/api/settings')
    if (!resp.ok) return null  // 后端未启动时 Vite 代理返回 5xx（非网络错误），同样静默跳过
    return await resp.json()
  } catch {
    return null  // 网络层失败也静默（设置页仅在在线+后端可用时有意义）
  }
}

export async function saveSettings(payload) {
  const resp = await fetch('/api/settings', {
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
  const resp = await fetch(`/api/providers/${encodeURIComponent(providerId)}/models`)
  if (!resp.ok) throw new Error('模型列表获取失败')
  return resp.json()
}

// 测试供应商连通性（最小 chat 调用）
export async function testProvider(providerId, model) {
  const resp = await fetch(`/api/providers/${encodeURIComponent(providerId)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  })
  if (!resp.ok) throw new Error('测试请求失败')
  return resp.json()
}
