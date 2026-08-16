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
    </main>

    <footer class="footer">
      初筛助手 · 输出需人工终审 · 不构成法律意见 · 评测数字均真实跑出于 held-out test
    </footer>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { store } from './api.js'
import Workbench from './views/Workbench.vue'
import ReportDetail from './views/ReportDetail.vue'
import EvalBoard from './views/EvalBoard.vue'

const tabs = [
  { key: 'workbench', label: '工作台', icon: '🛠' },
  { key: 'report', label: '报告详情', icon: '📋' },
  { key: 'eval', label: '评测对比', icon: '📊' },
]
const activeTab = ref('workbench')
const openReport = () => { activeTab.value = 'report' }
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
</style>
