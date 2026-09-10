<template>
  <div class="app">
    <a class="skip-link" href="#main-content">跳转到主要内容</a>
    <aside class="sidebar">
      <div class="brand"><div class="logo" aria-hidden="true">审</div><div><div class="brand-title">合同审查助手</div><div class="brand-sub">CONTRACT REVIEW</div></div></div>
      <nav class="nav" aria-label="主要导航"><p class="nav-caption">工作空间</p><button v-for="t in tabs" :key="t.key" class="nav-item" :class="{ active: activeTab === t.key }" @click="activeTab = t.key" :aria-current="activeTab === t.key ? 'page' : undefined"><span class="nav-icon" aria-hidden="true">{{ t.icon || '·' }}</span>{{ t.label }}<span v-if="t.key === 'workbench' && store.running" class="nav-live" aria-label="审查进行中" /></button></nav>
      <div class="sidebar-bottom"><button class="secondary-nav" :class="{ active: activeTab === 'eval' }" @click="activeTab = 'eval'" :aria-current="activeTab === 'eval' ? 'page' : undefined">了解评测表现 <span aria-hidden="true">↗</span></button><div class="sidebar-note">每一处判断<br>都值得认真核对。<span>合同风险与证据工作台</span></div></div>
    </aside>
    <header class="header"><span class="breadcrumb">工作空间 <span>/</span> {{ pageTitle }}</span><div class="header-right"><span class="session-label"><i />{{ store.principalRole === 'admin' ? '管理工作区' : '访客工作区' }}</span><el-radio-group v-model="store.mode" size="small" :disabled="store.running" aria-label="审查模式" class="mode-switch"><el-radio-button value="online">在线审查</el-radio-button><el-radio-button value="offline">示例体验</el-radio-button></el-radio-group></div></header>
    <div v-if="balanceBanner" class="balance-banner" :class="balanceBanner.type"><span>{{ balanceBanner.text }}</span><el-button size="small" text @click="refreshBalance">重新检测</el-button></div>
    <main id="main-content" class="main" tabindex="-1">
      <div class="page-heading"><div><p class="page-kicker">{{ pageKicker }}</p><h1>{{ pageTitle }}</h1><p class="page-description">{{ pageDescription }}</p></div><span v-if="store.report && activeTab === 'report'" class="report-context" :title="store.contractName">{{ store.contractName }}</span><el-button v-if="activeTab === 'history'" type="primary" @click="activeTab = 'workbench'">新建审查 <span aria-hidden="true">＋</span></el-button></div>
      <KeepAlive>
        <Workbench v-if="activeTab === 'workbench'" ref="workbench" @open-report="openReport" />
      </KeepAlive>
      <template v-if="activeTab === 'report'">
        <ReportDetail v-if="store.report" :key="store.reviewRunId || store.contractName" :report="store.report" :review-run-id="store.reviewRunId" @review-updated="applyReviewUpdate" />
        <div v-else class="panel empty-panel"><span class="empty-symbol" aria-hidden="true">▤</span><h2>你的第一份审查报告，从这里开始</h2><p>上传或粘贴合同，完成审查后即可查看风险与修改建议。</p><el-button type="primary" @click="activeTab = 'workbench'">开始审查合同</el-button><el-button @click="activeTab = 'history'">查看历史记录</el-button></div>
      </template>
      <HistoryView v-if="activeTab === 'history'" :opening="openingRun" @open-run="openHistoryRun" @new-review="activeTab = 'workbench'" />
      <CollaborationView v-if="activeTab === 'collaboration'" /><AssetsView v-if="activeTab === 'assets'" @review="reviewAsset" /><PlaybooksView v-if="activeTab === 'playbooks'" /><EvalBoard v-if="activeTab === 'eval'" /><SettingsView v-if="activeTab === 'settings'" />
    </main>
    <footer class="footer"><span>审查结果仅供初筛参考，需人工终审，不构成法律意见。</span><span class="ver-tag">合同审查助手 · v{{ frontVersion }}</span></footer>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { store, fetchBalance, fetchReviewRun, FRONT_VERSION } from './api.js'
import Workbench from './views/Workbench.vue'
import ReportDetail from './views/ReportDetail.vue'
import EvalBoard from './views/EvalBoard.vue'
import SettingsView from './views/SettingsView.vue'
import HistoryView from './views/HistoryView.vue'
import CollaborationView from './views/CollaborationView.vue'
import AssetsView from './views/AssetsView.vue'
import PlaybooksView from './views/PlaybooksView.vue'

const visitorTabs = [
  { key: 'workbench', label: '合同审查', icon: '01' },
  { key: 'report', label: '审查报告', icon: '02' },
  { key: 'history', label: '历史记录', icon: '03' },
]
const adminTabs = [
  { key: 'collaboration', label: '审查协作' },
  { key: 'assets', label: '合同资产' },
  { key: 'playbooks', label: 'Playbook' },
  { key: 'settings', label: '设置', icon: '⚙️' },
]
const tabs = computed(() => store.principalRole === 'admin' ? [...visitorTabs, ...adminTabs] : visitorTabs)
const activeTab = ref('workbench')
const pageTitle = computed(() => activeTab.value === 'eval' ? '评测表现' : tabs.value.find(t => t.key === activeTab.value)?.label || '工作空间')
const pageKicker = computed(() => ({ workbench: 'REVIEW WORKSPACE', report: 'REVIEW REPORT', history: 'REVIEW HISTORY' }[activeTab.value] || 'WORKSPACE'))
const pageDescription = computed(() => ({ workbench: '把复杂合同，变成可以逐条核对的风险与建议。', report: '先关注重点风险，再结合原文、依据与建议作出判断。', history: '回到之前的审查，继续查看进度与报告。' }[activeTab.value] || '合同审查助手 · 工作空间'))
const workbench = ref(null)
const openingRun = ref(false)
const openReport = () => { activeTab.value = 'report' }
const frontVersion = FRONT_VERSION

function reviewAsset(asset) {
  if (store.running) { ElMessage.warning('请等待当前审查完成后载入合同'); return }
  store.assetDraft = { id: asset.id, text: asset.text, filename: asset.filename }
  activeTab.value = 'workbench'
}

async function openHistoryRun(run) {
  if (openingRun.value) return
  openingRun.value = true
  try {
    const task = await fetchReviewRun(run.id)
    if (task.report) {
      store.reviewRunId = task.id
      store.contractName = task.report.contract?.name || `${run.contract_type === 'sale' ? '销售' : '采购'}合同`
      store.report = task.report
      activeTab.value = 'report'
    } else if (['running', 'queued'].includes(task.status)) {
      if (store.running) { ElMessage.info('当前审查仍在进行，可完成后查看其他任务'); return }
      const saved = { taskId: task.id, contractName: `${run.contract_type === 'sale' ? '销售' : '采购'}合同` }
      localStorage.setItem('cra_active_review_run', JSON.stringify(saved))
      activeTab.value = 'workbench'
      await nextTick()
      workbench.value?.resumePersistedReview(saved)
    } else {
      ElMessage.warning(task.error || '本次审查未完成，请回到合同审查页面重新提交正文。')
    }
  } catch (error) { ElMessage.error(error.message || '报告读取失败，请重试') }
  finally { openingRun.value = false }
}

// ── 余额预警横幅 ─────────────────────────────────────────────
const balanceBanner = computed(() => {
  const online = store.mode === 'online'
  const bal = store.balance
  // 离线/未查询到真实余额（mock 模式返回 null）→ 不显示
  if (!online || bal === null || store.balanceAvailable === null) {
    if (online && store.balanceQueryFailed) {
      return { type: 'query-failed', icon: '⚠️', text: '无法查询 API 余额（供应商接口波动或网络异常），审查功能暂不受影响' }
    }
    return null
  }
  const stopped = store.balanceAvailable === false || bal <= 0
  if (stopped) {
    return { type: 'danger', icon: '⛔', text: 'API 供应商已停止服务（账户余额已用完或不可用）。请充值后点击"重新检测"继续使用。' }
  }
  if (bal <= store.balanceThreshold) {
    return {
      type: 'warning',
      icon: '⚠️',
      text: `余额预警：当前余额 ¥${bal.toFixed(2)}，低于建议阈值 ¥${store.balanceThreshold.toFixed(2)}。余额用完将无法审查，请及时充值。`,
    }
  }
  return null
})

async function refreshBalance() {
  await fetchBalance()
  if (store.balance !== null && store.balance > 0) {
    ElMessage.success(`余额检测完成：¥${store.balance.toFixed(2)}`)
  }
}

function applyReviewUpdate({ findingId, disposition }) {
  const risk = store.report?.risks?.find((item) => item.id === findingId)
  if (!risk) return
  risk.reviewStatus = disposition.decision
  risk.reviewDecision = disposition
}

let balanceTimer
onMounted(() => {
  if (store.principalRole === 'admin') fetchBalance()
  // 每 5 分钟刷新一次余额（余额可能被其他端消耗）
  balanceTimer = setInterval(() => { if (store.principalRole === 'admin') fetchBalance() }, 5 * 60 * 1000)
})
onUnmounted(() => clearInterval(balanceTimer))

// 记住在线/离线模式选择，刷新后不丢
watch(() => store.mode, (m) => localStorage.setItem('cra_mode', m))
</script>
