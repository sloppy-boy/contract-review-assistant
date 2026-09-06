<template>
  <div class="history">
    <div class="panel history-panel">
      <div class="panel-title">审查历史 <span class="nav-count">运行记录保存在本地工作区</span></div>
      <div class="history-tools">
        <el-select v-model="status" size="small" class="status-filter" @change="load">
          <el-option label="全部状态" value="" />
          <el-option label="进行中" value="running" />
          <el-option label="已完成" value="done" />
          <el-option label="失败" value="failed" />
        </el-select>
        <el-button size="small" @click="load">刷新</el-button>
      </div>
      <el-table :data="runs" v-loading="loading" empty-text="暂无审查记录" @row-click="openRun" class="history-table">
        <el-table-column label="时间" min-width="165"><template #default="{ row }">{{ formatTime(row.createdAt) }}</template></el-table-column>
        <el-table-column label="合同类型" min-width="95"><template #default="{ row }">{{ row.contract_type === 'sale' ? '销售合同' : '采购合同' }}</template></el-table-column>
        <el-table-column label="状态" min-width="95"><template #default="{ row }"><el-tag size="small" :type="statusType(row.status)">{{ statusName(row.status) }}</el-tag></template></el-table-column>
        <el-table-column label="风险" min-width="80"><template #default="{ row }">{{ row.summary?.total ?? '—' }}</template></el-table-column>
        <el-table-column label="耗时" min-width="90"><template #default="{ row }">{{ duration(row.stageTimes) }}</template></el-table-column>
        <el-table-column label="操作" width="100"><template #default="{ row }"><el-button text type="primary" @click.stop="openRun(row)">{{ row.status === 'done' ? '查看报告' : '继续查看' }}</el-button></template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchReviewHistory } from '../api.js'

const emit = defineEmits(['open-run'])
const runs = ref([]); const loading = ref(false); const status = ref('')
const statusName = (value) => ({ queued: '排队中', running: '进行中', done: '已完成', failed: '失败' }[value] || value)
const statusType = (value) => ({ running: 'warning', done: 'success', failed: 'danger' }[value] || 'info')
const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const duration = (values) => Array.isArray(values) ? `${(values.reduce((sum, value) => sum + value, 0) / 1000).toFixed(1)} 秒` : '—'
async function load() { loading.value = true; try { runs.value = await fetchReviewHistory(status.value) } catch (error) { ElMessage.error(error.message || '历史记录读取失败') } finally { loading.value = false } }
const openRun = (run) => emit('open-run', run)
onMounted(load)
</script>

<style scoped>
.history-panel { padding: 18px; }.history-tools { display: flex; gap: 8px; margin: 14px 0; }.status-filter { width: 130px; }.history-table { cursor: pointer; }
</style>
