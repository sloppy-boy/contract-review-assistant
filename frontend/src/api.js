// 全局状态 + 在线/离线 API 封装（SPEC 2.8：离线演示模式，缓存为真实导出）
import { reactive } from 'vue'

export const store = reactive({
  mode: 'online',          // online | offline（演示不依赖 API 可用性）
  report: null,
  contractName: '',
  contractType: 'purchase',
  running: false,
  stage: 0,                // 流水线阶段 0~4
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
export async function uploadAndReview(text, contractType) {
  const resp = await fetch('/api/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ text, contract_type: contractType }),
  })
  if (!resp.ok) throw new Error('上传失败（后端未启动？）')
  const { taskId } = await resp.json()
  for (;;) {
    await new Promise((r) => setTimeout(r, 1500))
    const rep = await fetch(`/api/report/${taskId}`).then((x) => x.json())
    if (rep.status === 'done') return rep.report
    if (rep.status === 'failed') throw new Error(rep.error || '流水线失败')
  }
}

export async function loadEvalResults() {
  const resp = await fetch('/eval-results.json')
  if (!resp.ok) return null
  return resp.json()
}
