<template>
  <div class="workbench">
    <!-- 主体双栏：上传（主卡片） ↔ 演示合同 -->
    <div class="main-row">
      <div class="panel upload-panel">
        <div class="panel-title">上传合同</div>
        <el-upload
          drag
          :auto-upload="false"
          :show-file-list="false"
          accept=".txt,.md"
          class="uploader"
          @change="onFile"
        >
          <div class="upload-icon">📄</div>
          <div class="upload-hint">拖拽合同文件到此处，或点击选择</div>
          <div class="upload-sub">支持 .txt / .md 文本 · 合同数据不落第三方存储</div>
          <div v-if="fileName" class="file-picked">✅ 已读取：<b>{{ fileName }}</b>（{{ text.length }} 字）</div>
        </el-upload>
        <!-- 粘贴输入（P1：规范承诺"拖拽上传或复制文本粘贴"，补真实入口） -->
        <el-input
          v-model="pasteText"
          type="textarea"
          :rows="4"
          resize="none"
          class="paste-box"
          placeholder="或直接粘贴合同文本（纯文本，支持中文）…"
          :disabled="store.running"
        />
        <!-- 设置行：类型 + 开始 -->
        <div class="setting-row">
          <el-radio-group v-model="store.contractType" size="default">
            <el-radio-button value="purchase">采购合同</el-radio-button>
            <el-radio-button value="sale">销售合同</el-radio-button>
          </el-radio-group>
          <el-button type="primary" class="start-btn" :loading="store.running" @click="reviewText">
            🚀 开始审查
          </el-button>
        </div>
        <div class="pipe-desc">
          流水线：条款抽取 → 13 类 worker 并行扇出 → 对抗复核（打回重证）→ 报告
        </div>
      </div>

      <div class="panel demo-panel">
        <div class="panel-title">🎬 演出合同一键载入<span class="nav-count">离线可用</span></div>
        <div class="demo-grid">
          <div
            v-for="d in DEMO_CARDS" :key="d.id"
            class="demo-card" :class="d.tone"
            @click="loadDemo(d.id)"
          >
            <div class="demo-name">{{ d.name }}</div>
            <div class="demo-desc">{{ d.desc }}</div>
            <span class="demo-tag">{{ d.tag }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 流水线阶段进度（后端真实进度：阶段 + 各阶段实际耗时） -->
    <div v-if="store.running || store.report" class="panel stage-panel">
      <div class="panel-title">流水线进度 <span class="nav-count">各阶段实际耗时</span></div>
      <el-steps :active="stepsActive" align-center finish-status="success" class="steps">
        <el-step
          v-for="(s, i) in STAGES" :key="s"
          :title="s"
          :description="stepDesc(i)"
          :status="stepStatus(i)"
        />
      </el-steps>
      <div v-if="store.running" class="stage-hint">
        {{ runningText }}
        <span class="stage-note">深度模型审查约需 3~10 分钟，请耐心等待，完成后自动跳转报告</span>
      </div>
    </div>

    <el-alert v-if="error" type="error" :title="error" closable class="err" @close="error = ''" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { store, DEMO_CONTRACTS, STAGES, loadDemoReport, uploadAndReview } from '../api.js'

const emit = defineEmits(['open-report'])
const error = ref('')
const fileName = ref('')
const text = ref('')
const pasteText = ref('')

const DEMO_CARDS = [
  { ...DEMO_CONTRACTS[0], tone: 'high', tag: '召回演示' },
  { ...DEMO_CONTRACTS[1], tone: 'clean', tag: '低误报' },
  { ...DEMO_CONTRACTS[2], tone: 'boundary', tag: '争议标定' },
]

// ── 真实进度渲染：el-steps active + 每阶段耗时 + 当前阶段实时计时 ──
const nowMs = ref(Date.now())
let clockTimer = null

function startClock() {
  if (clockTimer) return
  clockTimer = setInterval(() => { nowMs.value = Date.now() }, 1000)  // 1s 一跳（整秒计时）
}
function stopClock() {
  clearInterval(clockTimer)
  clockTimer = null
}
watch(() => store.running, (v) => { if (v) startClock(); else stopClock() })
onUnmounted(stopClock)

// 已完成阶段数（el-steps active 语义：已完成 = stage+1；进行中 = stage）
const stepsActive = computed(() => (store.stageStatus === 'done' ? store.stage + 1 : store.stage))

function stepStatus(i) {
  // 已完成判定用 stage 索引（stageTimes 在极快任务下可能为 0ms，不能作为完成依据）
  if (store.stage > i || (store.stage === i && store.stageStatus === 'done')) return 'success'
  if (store.stage === i && store.running) return 'process'
  return 'wait'
}

function stepDesc(i) {
  const t = store.stageTimes?.[i] || 0
  if (t > 0) return t >= 1000 ? `${(t / 1000).toFixed(0)}s` : '<1s'   // 已完成：整数秒
  if (store.stage === i && store.running) {
    const start = store.stageStartedAt || nowMs.value
    return `⏱ ${Math.floor((nowMs.value - start) / 1000)}s`           // 进行中：整秒累加
  }
  // 被跳过的阶段（如 A 档复核）：显示"已跳过"
  if (store.stage > i && /跳过/.test(store.stageDetail || '')) return '已跳过'
  return ''                                                           // 未开始
}

const runningText = computed(() => {
  if (store.stage >= STAGES.length) return '正在生成报告…'
  const detail = store.stageDetail ? `（${store.stageDetail}）` : ''
  return `${STAGES[store.stage]} 进行中${detail}`
})

function handleFile(f) {
  if (!f) return
  // 非文本文件（docx/pdf 为二进制）给出提示，避免读出乱码
  if (!/\.(txt|md)$/i.test(f.name || '')) {
    ElMessage.warning(`「${f.name}」为二进制格式，当前演示以 .txt 文本为主，将按文本读取（可能乱码）`)
  }
  const reader = new FileReader()
  reader.onload = () => {
    text.value = reader.result || ''
    fileName.value = f.name
    ElMessage.success(`已读取：${f.name}（${text.value.length} 字）`)
  }
  reader.onerror = () => ElMessage.error('文件读取失败，请重试')
  reader.readAsText(f)
}

function onFile(uploadFile) {
  handleFile(uploadFile?.raw)
}

// 页面级拖拽：整个工作台区域都能拖入文件（主流上传体验），
// 并阻止浏览器默认"打开文件"行为
function onDragOver(e) { e.preventDefault() }
function onDrop(e) {
  e.preventDefault()
  handleFile(e.dataTransfer?.files?.[0])
}

onMounted(() => {
  document.addEventListener('dragover', onDragOver)
  document.addEventListener('drop', onDrop)
})
onUnmounted(() => {
  document.removeEventListener('dragover', onDragOver)
  document.removeEventListener('drop', onDrop)
})

async function reviewText() {
  const content = text.value.trim() || pasteText.value.trim()
  if (!content) { ElMessage.warning('请先上传或粘贴合同文本'); return }
  if (store.mode === 'offline') { ElMessage.info('离线演示模式请使用右侧演出合同一键载入'); return }

  // ── 余额检查（在线模式）：耗尽 → 弹"停止服务"并阻止；低余额 → 预警确认 ──
  if (store.balance !== null && store.balanceAvailable !== null) {
    const stopped = store.balanceAvailable === false || store.balance <= 0
    if (stopped) {
      ElMessageBox.alert(
        'API 供应商账户余额已用完或不可用，服务已停止。请充值后返回工作台重试。',
        'API 供应商停止服务',
        { type: 'error', confirmButtonText: '我知道了' },
      )
      return
    }
    if (store.balance <= store.balanceThreshold) {
      try {
        await ElMessageBox.confirm(
          `当前余额 ¥${store.balance.toFixed(2)} 低于预警值 ¥${store.balanceThreshold.toFixed(2)}，余额用完将无法继续审查。仍要继续吗？`,
          '余额不足预警',
          { type: 'warning', confirmButtonText: '继续审查', cancelButtonText: '取消' },
        )
      } catch { return }  // 用户取消
    }
  }

  store.running = true
  store.stage = 0
  store.stageStatus = 'idle'
  store.stageTimes = [0, 0, 0, 0]
  store.stageDetail = ''
  store.stageStartedAt = Date.now()
  error.value = ''
  try {
    // onProgress：后端真实进度（stage/stageTimes/stageDetail）→ 渲染与报告产出同步
    const report = await uploadAndReview(content, store.contractType, (rep) => {
      if (typeof rep.stage === 'number') {
        store.stage = rep.stage
        store.stageStatus = rep.stageStatus || 'running'
        store.stageTimes = rep.stageTimes || [0, 0, 0, 0]
        store.stageDetail = rep.stageDetail || ''
        // 计时基准：仅当后端给出 stageStartedAt 时覆盖；否则保持首次设置
        // （后端 idle 阶段没有该字段时若每次重置 → 计时从 0 乱跳）
        if (rep.stageStartedAt) store.stageStartedAt = rep.stageStartedAt
        else if (!store.stageStartedAt) store.stageStartedAt = Date.now()
      }
    })
    store.report = report
    store.contractName = fileName.value || (pasteText.value.trim() ? '粘贴合同' : '上传合同')
    store.stage = STAGES.length - 1
    store.stageStatus = 'done'
    ElMessage.success('审查完成，已跳转报告')
    emit('open-report')
  } catch (e) {
    // 余额耗尽：任务失败携带 balanceExhausted → 弹"停止服务"提示（不再莫名跳空报告）
    if (e.balanceExhausted) {
      error.value = 'API 供应商停止服务：余额不足，本次审查未完成'
      ElMessageBox.alert(
        `本次审查未能完成：${e.message || 'API 供应商余额不足'}。\n请充值后再试。`,
        'API 供应商停止服务',
        { type: 'error', confirmButtonText: '我知道了' },
      )
    } else {
      error.value = String(e.message || e)
    }
  } finally {
    store.running = false
  }
}

async function loadDemo(id) {
  store.running = true
  store.stage = 0
  store.stageStatus = 'done'   // 离线缓存瞬时完成：全部阶段绿勾
  store.stageTimes = [200, 200, 200, 200]
  store.stageDetail = ''
  error.value = ''
  try {
    const report = await loadDemoReport(id)
    store.report = report
    store.contractName = id
    store.stage = STAGES.length - 1
    ElMessage.success(`已载入 ${id}（真实 pipeline 缓存）`)
    emit('open-report')
  } catch (e) {
    error.value = String(e.message || e)
  } finally {
    store.running = false
  }
}
</script>

<style scoped>
.workbench { display: flex; flex-direction: column; gap: 16px; }
.panel-title { margin-bottom: 12px; }

/* 主体双栏：上传主卡片 ↔ 演示合同 */
.main-row { display: grid; grid-template-columns: 1.6fr 1fr; gap: 16px; align-items: stretch; }
.upload-panel, .demo-panel { margin: 0; display: flex; flex-direction: column; }

/* 上传卡片 */
.uploader :deep(.el-upload) { width: 100%; }
.uploader :deep(.el-upload-dragger) {
  padding: 34px 20px;
  border-radius: var(--radius);
  border: 2px dashed #c3d6f9;
  background: var(--el-color-primary-light-9);
  transition: all 0.2s ease;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.uploader :deep(.el-upload-dragger:hover) { border-color: var(--el-color-primary); background: #e3edfe; }
.upload-icon { font-size: 36px; margin-bottom: 8px; }
.upload-hint { font-size: 15px; color: var(--el-color-primary); font-weight: 600; }
.upload-sub { font-size: 12px; color: var(--ink-3); margin-top: 6px; }
.file-picked { margin-top: 12px; font-size: 13px; color: var(--sev-low); background: var(--sev-low-bg); border-radius: 6px; padding: 6px 12px; }
.paste-box { margin-top: 12px; }
.paste-box :deep(.el-textarea__inner) { font-size: 13px; line-height: 1.6; }

/* 设置行（上传卡片底部）：类型 + 开始 内联 */
.setting-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; }
.start-btn { margin: 0; }
.pipe-desc { margin-top: 10px; font-size: 11px; color: var(--ink-3); line-height: 1.6; }

/* 演示合同：竖排三卡（右侧栏） */
.demo-grid { display: flex; flex-direction: column; gap: 10px; flex: 1; }
.demo-card {
  border: 1px solid var(--line);
  border-left-width: 4px;
  border-radius: 8px;
  padding: 13px 15px;
  cursor: pointer;
  transition: all 0.15s ease;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.demo-card:hover { box-shadow: var(--shadow-lg); transform: translateX(2px); }
.demo-card.high { border-left-color: var(--sev-high); }
.demo-card.clean { border-left-color: var(--sev-low); }
.demo-card.boundary { border-left-color: var(--sev-medium); }
.demo-name { font-size: 14px; font-weight: 600; color: var(--ink); }
.demo-desc { font-size: 12px; color: var(--ink-3); line-height: 1.5; }
.demo-tag { align-self: flex-start; font-size: 11px; padding: 1px 10px; border-radius: 10px; background: #f3f4f6; color: var(--ink-2); }

.stage-panel { margin-top: 0; }
.steps { margin: 8px 0 4px; }
.stage-hint { text-align: center; font-size: 12px; color: var(--ink-3); margin-top: 8px; }
.stage-note { margin-left: 8px; color: var(--ink-3); opacity: 0.75; }
.err { margin-top: 4px; }
</style>
