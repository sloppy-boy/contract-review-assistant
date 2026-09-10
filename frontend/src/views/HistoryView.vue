<template>
  <section class="panel history-panel" aria-label="审查历史">
    <div class="history-heading"><div><h2>全部审查记录</h2><p class="section-description">仅展示当前访客会话或工作区可访问的记录；更换浏览器或会话失效后，历史可能不可见。</p></div><span class="quiet-tag">{{ loading ? '读取中…' : `${runs.length} 条记录` }}</span></div>
    <div class="history-tools"><el-select v-model="status" class="status-filter" placeholder="全部状态" aria-label="筛选审查状态" @change="load"><el-option label="全部状态" value="" /><el-option label="排队中" value="queued" /><el-option label="进行中" value="running" /><el-option label="已完成" value="done" /><el-option label="失败" value="failed" /><el-option label="已取消" value="cancelled" /><el-option label="未完成" value="dead_letter" /></el-select><el-button :loading="loading" @click="load">刷新记录</el-button><span v-if="opening" class="opening-hint" role="status">正在打开审查记录…</span></div>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="history-error" />
    <el-table v-else :data="runs" v-loading="loading" @row-click="openRun" class="history-table" :aria-busy="loading">
      <el-table-column label="合同" min-width="160"><template #default="{ row }"><div class="history-contract"><span class="document-mark" aria-hidden="true">▤</span><div><b>{{ row.contract_type === 'sale' ? '销售合同' : '采购合同' }}</b><span class="run-reference">记录 {{ row.id.slice(0, 8) }}</span></div></div></template></el-table-column>
      <el-table-column label="提交时间" min-width="180"><template #default="{ row }">{{ formatTime(row.createdAt) }}</template></el-table-column>
      <el-table-column label="审查状态" min-width="104"><template #default="{ row }"><el-tag size="small" :type="statusType(row.status)" effect="light">{{ statusName(row.status) }}</el-tag></template></el-table-column>
      <el-table-column label="发现风险" min-width="90"><template #default="{ row }"><span class="history-risk">{{ row.summary?.total ?? '—' }}</span><span v-if="row.summary?.total != null" class="muted"> 项</span></template></el-table-column>
      <el-table-column label="耗时" min-width="85"><template #default="{ row }">{{ duration(row.stageTimes) }}</template></el-table-column>
      <el-table-column label="操作" width="120"><template #default="{ row }"><el-button text type="primary" :disabled="opening" @click.stop="openRun(row)">{{ row.status === 'done' ? '查看报告' : ['running', 'queued'].includes(row.status) ? '查看进度' : '查看详情' }} <span aria-hidden="true">→</span></el-button></template></el-table-column>
      <template #empty><div v-if="!loading" class="history-empty"><span class="empty-symbol" aria-hidden="true">◷</span><h3>{{ status ? '暂无符合条件的记录' : '还没有审查记录' }}</h3><p>{{ status ? '试试其他状态，或刷新查看最新结果。' : '提交一份合同后，可在这里回看审查进度和报告。' }}</p><el-button v-if="status" @click="status = ''; load()">查看全部记录</el-button><el-button v-else type="primary" @click="emit('new-review')">开始第一份审查</el-button></div></template>
    </el-table>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchReviewHistory } from '../api.js'

const props = defineProps({ opening: Boolean })
const emit = defineEmits(['open-run', 'new-review'])
const runs = ref([]); const loading = ref(false); const status = ref('')
const statusName = (value) => ({ queued: '排队中', running: '进行中', done: '已完成', failed: '失败', cancelled: '已取消', dead_letter: '未完成' }[value] || value)
const statusType = (value) => ({ running: 'warning', done: 'success', failed: 'danger' }[value] || 'info')
const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const duration = (values) => Array.isArray(values) ? `${(values.reduce((sum, value) => sum + value, 0) / 1000).toFixed(1)} 秒` : '—'
const error = ref('')
let requestVersion = 0
async function load() {
  const version = ++requestVersion
  loading.value = true; error.value = ''; runs.value = []
  try {
    const result = await fetchReviewHistory(status.value)
    if (version === requestVersion) runs.value = result
  } catch (reason) {
    if (version === requestVersion) error.value = reason.message || '历史记录读取失败，请重试'
  } finally { if (version === requestVersion) loading.value = false }
}
const openRun = (run) => { if (!props.opening) emit('open-run', run) }
onMounted(load)
</script>
