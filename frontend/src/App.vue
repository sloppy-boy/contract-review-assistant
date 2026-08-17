<template>
  <div class="app">
    <!-- 品牌头部 -->
    <header class="header">
      <div class="brand">
        <div class="logo">📑</div>
        <div>
          <div class="brand-title">合同审查助手</div>
          <div class="brand-sub">多 Agent 协同 · 法条可溯源 · 真实评测</div>
        </div>
      </div>
      <div class="header-right">
        <el-tag v-if="store.report" size="small" effect="dark" class="ctag" :type="'primary'">
          当前报告：{{ store.contractName || '—' }}
        </el-tag>
        <div class="mode-switch">
          <el-radio-group v-model="store.mode" size="small">
            <el-radio-button value="online">🖥 在线模式</el-radio-button>
            <el-radio-button value="offline">📦 离线演示</el-radio-button>
          </el-radio-group>
        </div>
      </div>
    </header>

    <!-- 余额预警横幅（在线模式；余额耗尽 → 红色停止服务；低余额 → 黄色预警） -->
    <div v-if="balanceBanner" class="balance-banner" :class="balanceBanner.type">
      <span class="banner-icon">{{ balanceBanner.icon }}</span>
      <span class="banner-text">{{ balanceBanner.text }}</span>
      <el-button
        v-if="balanceBanner.type === 'danger' && store.mode === 'online'"
        size="small" text class="banner-action" @click="refreshBalance"
      >重新检测</el-button>
      <el-button
        v-if="balanceBanner.type === 'query-failed' && store.mode === 'online'"
        size="small" text class="banner-action" @click="refreshBalance"
      >重试</el-button>
    </div>

    <!-- 主导航 -->
    <nav class="nav">
      <div
        v-for="t in tabs" :key="t.key"
        class="nav-item" :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key"
      >
        <span class="nav-icon">{{ t.icon }}</span>{{ t.label }}
      </div>
    </nav>

    <!-- 内容区 -->
    <main class="main">
      <Workbench v-show="activeTab === 'workbench'" @open-report="openReport" />
      <template v-if="activeTab === 'report'">
        <ReportDetail v-if="store.report" :report="store.report" />
        <div v-else class="panel empty-panel">
          <el-empty description="暂无报告，请先在工作台审查一份合同（可一键载入演出合同）" />
        </div>
      </template>
      <EvalBoard v-show="activeTab === 'eval'" />
      <SettingsView v-show="activeTab === 'settings'" />
    </main>

    <footer class="footer">
      初筛助手 · 输出需人工终审 · 不构成法律意见 · 评测数字均真实跑出于 held-out test
      <span class="ver-tag">v{{ frontVersion }}</span>
    </footer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { store, fetchBalance, FRONT_VERSION } from './api.js'
import Workbench from './views/Workbench.vue'
import ReportDetail from './views/ReportDetail.vue'
import EvalBoard from './views/EvalBoard.vue'
import SettingsView from './views/SettingsView.vue'

const tabs = [
  { key: 'workbench', label: '工作台', icon: '🛠' },
  { key: 'report', label: '报告详情', icon: '📋' },
  { key: 'eval', label: '评测对比', icon: '📊' },
  { key: 'settings', label: '设置', icon: '⚙️' },
]
const activeTab = ref('workbench')
const openReport = () => { activeTab.value = 'report' }
const frontVersion = FRONT_VERSION

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

onMounted(() => {
  fetchBalance()
  // 每 5 分钟刷新一次余额（余额可能被其他端消耗）
  setInterval(fetchBalance, 5 * 60 * 1000)
})
</script>

<style scoped>
.app { min-height: 100vh; display: flex; flex-direction: column; }
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 32px;
  background: linear-gradient(120deg, #1e3a8a 0%, #2563eb 55%, #3b82f6 100%);
  color: #fff;
  box-shadow: 0 2px 12px rgba(30, 58, 138, 0.25);
}
.brand { display: flex; align-items: center; gap: 14px; }
.logo {
  width: 44px; height: 44px; border-radius: 12px;
  background: rgba(255, 255, 255, 0.15);
  display: flex; align-items: center; justify-content: center;
  font-size: 24px;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.2);
}
.brand-title { font-size: 20px; font-weight: 700; letter-spacing: 1px; }
.brand-sub { font-size: 12px; color: rgba(255, 255, 255, 0.75); margin-top: 2px; }
.header-right { display: flex; align-items: center; gap: 16px; }
.ctag { background: rgba(255, 255, 255, 0.18); border: 1px solid rgba(255, 255, 255, 0.25); color: #fff; }
.mode-switch :deep(.el-radio-button__inner) {
  background: rgba(255, 255, 255, 0.12);
  color: #e5e7eb;
  border-color: rgba(255, 255, 255, 0.22);
}
.mode-switch :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: #fff; color: #2563eb; border-color: #fff; font-weight: 600;
}

.nav {
  display: flex;
  gap: 4px;
  padding: 0 32px;
  background: #fff;
  border-bottom: 1px solid var(--line);
}
.nav-item {
  padding: 14px 20px;
  cursor: pointer;
  font-size: 14px;
  color: var(--ink-2);
  border-bottom: 2px solid transparent;
  transition: all 0.15s ease;
  display: flex; align-items: center; gap: 6px;
}
.nav-item:hover { color: var(--el-color-primary); }
.nav-item.active {
  color: var(--el-color-primary);
  font-weight: 600;
  border-bottom-color: var(--el-color-primary);
}
.nav-icon { font-size: 15px; }

/* 余额预警横幅 */
.balance-banner {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 32px;
  font-size: 13px;
  border-bottom: 1px solid transparent;
}
.balance-banner.danger { background: #fef2f2; color: #b91c1c; border-bottom-color: #fecaca; }
.balance-banner.warning { background: #fffbeb; color: #92400e; border-bottom-color: #fde68a; }
.balance-banner.query-failed { background: #f8fafc; color: #475569; border-bottom-color: #e2e8f0; }
.banner-icon { font-size: 15px; }
.banner-text { flex: 1; line-height: 1.5; }
.banner-action { color: inherit !important; text-decoration: underline; }

.main { flex: 1; padding: 20px 32px; max-width: 1440px; width: 100%; margin: 0 auto; box-sizing: border-box; }
.empty-panel { padding: 60px 20px; }
.footer {
  text-align: center;
  font-size: 12px;
  color: var(--ink-3);
  padding: 18px;
  border-top: 1px solid var(--line);
  background: #fff;
}
.ver-tag { margin-left: 8px; font-size: 11px; color: var(--ink-3); opacity: 0.7; }
</style>
