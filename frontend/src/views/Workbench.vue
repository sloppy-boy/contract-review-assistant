<template>
  <div class="workbench">
    <section v-if="store.running" class="panel stage-panel" aria-label="审查进度" aria-live="polite">
      <div class="section-heading"><div><p class="eyebrow">正在审查</p><h2>让每一处风险，都有据可查。</h2></div><span class="live-status"><i /> 审查进行中</span></div>
      <el-steps :active="stepsActive" align-center finish-status="success" class="steps">
        <el-step v-for="(s, i) in stageNames" :key="s" :title="s" :description="stepDesc(i)" :status="stepStatus(i)" />
      </el-steps>
      <p class="stage-hint">{{ runningText }}<span class="stage-note">通常需要数分钟。你可以切换页面，刷新后也可继续查看。</span></p>
    </section>
    <el-alert v-if="error" type="error" :title="error" show-icon closable class="err" @close="error = ''" />
    <div class="main-row">
      <section class="panel upload-panel">
        <div class="section-heading"><div><p class="eyebrow">NEW REVIEW</p><h2>从一份合同开始</h2></div><span class="step-marker">01 / 02</span></div>
        <p class="section-description">上传文本文件，或直接粘贴正文。确认内容后即可开始审查。</p>
        <el-upload drag :auto-upload="false" :show-file-list="false" accept=".txt,.md" :disabled="store.running" class="uploader" @change="onFile">
          <svg class="upload-icon" viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="M28 6H12v36h26V16L28 6Z" stroke="currentColor" stroke-width="1.5"/><path d="M28 6v10h10M24 33V21m-5 5 5-5 5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
          <div class="upload-hint">{{ reading ? '正在读取文件…' : '将合同拖到这里，或点击选择文件' }}</div>
          <div class="upload-sub">TXT / Markdown 文本文件 · 其他格式可复制正文粘贴</div>
        </el-upload>
        <div class="editor-label"><label for="contract-text">合同正文 <span v-if="fileName" class="filename" :title="fileName">{{ fileName }}</span></label><button v-if="text || reading" type="button" class="text-button" :disabled="store.running" @click="clearDraft">清空</button></div>
        <el-input id="contract-text" v-model="pasteText" type="textarea" :rows="6" resize="vertical" class="paste-box" placeholder="在此粘贴或编辑合同正文…" :disabled="store.running || reading" />
        <div class="editor-foot"><span>{{ fileName ? '文件内容已载入，可直接编辑' : '请保留条款编号，便于对照原文' }}</span><span>{{ text.length.toLocaleString() }} 字符</span></div>
        <div class="contract-options"><div class="field-label"><span class="step-number">02</span> 确认合同类型</div>
          <el-radio-group v-model="store.contractType" :disabled="store.running" aria-label="合同类型">
            <el-radio-button value="purchase">采购合同</el-radio-button><el-radio-button value="sale">销售合同</el-radio-button>
          </el-radio-group>
        </div>
        <details class="advanced-options">
          <summary>高级审查选项 <span>{{ selectedPlaybookObject?.content?.name || '使用内置审查基线' }}</span></summary>
          <div class="advanced-fields">
            <label>审查规则<select v-model="selectedPlaybook" class="playbook-select" :disabled="store.running" aria-label="审查规则"><option value="">内置审查基线</option><option v-for="book in playbooks" :key="`${book.playbookId}:${book.version}`" :value="`${book.playbookId}:${book.version}`">{{ book.content?.name }} · v{{ book.version }}</option></select></label>
            <label>适用法域<select v-model="selectedJurisdiction" :disabled="store.running || !selectedPlaybook" aria-label="法域"><option v-for="item in jurisdictions" :key="item" :value="item">{{ item === 'CN' ? '中国大陆' : item }}</option></select></label>
            <label>业务场景<select v-model="selectedScenario" :disabled="store.running || !selectedPlaybook" aria-label="业务场景"><option v-for="item in scenarios" :key="item" :value="item">{{ item === 'general' ? '通用场景' : item }}</option></select></label>
            <label>生效范围<select v-model="selectedScope" :disabled="store.running || !selectedPlaybook" aria-label="生效范围"><option v-for="item in scopes" :key="item" :value="item">{{ item === '*' ? '全部范围' : item }}</option></select></label>
          </div>
        </details>
        <div v-if="store.mode === 'offline'" class="mode-notice">当前为示例模式。<button type="button" class="text-button" :disabled="store.running" @click="store.mode = 'online'">切换在线审查</button>即可提交自己的合同。</div>
        <div class="submit-row"><span>审查完成后，将自动打开报告</span><el-button type="primary" class="start-btn" :loading="store.running" :disabled="!text.trim() || reading || store.mode !== 'online'" @click="reviewText">{{ store.running ? '正在审查' : '开始审查' }} <span aria-hidden="true">→</span></el-button></div>
      </section>
      <aside class="workbench-aside">
        <section class="panel guide-panel"><p class="eyebrow">REVIEW WITH EVIDENCE</p><h2>看清风险，<br>再作决定。</h2><p>从合同条款到修改建议，<br>让审查结果可以逐条核对。</p><ol class="review-guide"><li><span>01</span><div><b>识别合同风险</b><p>关注付款、交付、违约等关键条款</p></div></li><li><span>02</span><div><b>对照原文与依据</b><p>查看原文摘录、法条引用和证据</p></div></li><li><span>03</span><div><b>形成修改建议</b><p>复制示范条款，导出报告供人工复核</p></div></li></ol></section>
        <section class="panel demo-panel"><div class="section-heading"><h2>先看一份示例</h2><span class="quiet-tag">无需上传</span></div><p class="section-description">浏览已有报告，了解审查结果的呈现方式。</p><div class="demo-grid"><button v-for="d in DEMO_CARDS" :key="d.id" type="button" class="demo-card" :class="d.tone" :disabled="store.running || reading" @click="loadDemo(d.id)"><span class="demo-title"><span class="demo-name">{{ d.name }}</span><span aria-hidden="true">↗</span></span><span class="demo-desc">{{ d.desc }}</span><span class="demo-tag">{{ d.tag }}</span></button></div></section>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { store, DEMO_CONTRACTS, STAGES, fetchPlaybooks, loadDemoReport, resumeReview, uploadAndReview } from '../api.js'

const emit = defineEmits(['open-report'])
const error = ref('')
const fileName = ref('')
const text = ref('')
const pasteText = text
const reading = ref(false)
let fileReadVersion = 0
let visible = true
const stageNames = ['条款抽取', '风险识别', '证据复核', '生成报告']
const playbooks = ref([])
const selectedPlaybook = ref('')
const selectedPlaybookObject = computed(() => playbooks.value.find(book => `${book.playbookId}:${book.version}` === selectedPlaybook.value) || null)
const selectedJurisdiction = ref('CN'), selectedScenario = ref('general'), selectedScope = ref('*')
const jurisdictions = computed(() => [...new Set(['CN', ...playbooks.value.map(book => book.content?.jurisdiction).filter(Boolean)])])
const scenarios = computed(() => [...new Set(['general', ...playbooks.value.map(book => book.content?.businessScenario).filter(Boolean)])])
const scopes = computed(() => [...new Set(['*', ...playbooks.value.flatMap(book => book.content?.effectiveScope || []).filter(Boolean)])])
const reviewPlaybook = computed(() => {
  const book = selectedPlaybookObject.value
  if (!book) return null
  return { ...book, content: { ...book.content, jurisdiction: selectedJurisdiction.value, businessScenario: selectedScenario.value, effectiveScope: [selectedScope.value] } }
})
watch(selectedPlaybookObject, (book) => {
  selectedJurisdiction.value = book?.content?.jurisdiction || 'CN'
  selectedScenario.value = book?.content?.businessScenario || 'general'
  selectedScope.value = book?.content?.effectiveScope?.[0] || '*'
})
watch(() => store.assetDraft, (asset) => {
  if (!asset) return
  text.value = asset.text; fileName.value = asset.filename
}, { immediate: true })
watch(() => store.workspaceApiKey, () => {
  if (store.assetDraft) { text.value = ''; pasteText.value = ''; fileName.value = ''; store.assetDraft = null }
})
const ACTIVE_RUN_KEY = 'cra_active_review_run'

const DEMO_CARDS = [
  { ...DEMO_CONTRACTS[0], name: '高风险合同', desc: '查看重点风险、法条依据与修改建议', tone: 'high', tag: '风险识别' },
  { ...DEMO_CONTRACTS[1], name: '规范合同', desc: '了解较完善的合同如何呈现审查结果', tone: 'clean', tag: '规范参考' },
  { ...DEMO_CONTRACTS[2], name: '争议条款合同', desc: '了解需要进一步人工判断的条款', tone: 'boundary', tag: '争议分析' },
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
  return `${stageNames[store.stage] || '审查准备'}进行中，请稍候…`
})

function handleFile(f) {
  if (!f || store.running) return
  if (!/\.(txt|md)$/i.test(f.name || '')) {
    ElMessage.warning('请选择 TXT 或 Markdown 文本文件；其他格式请复制正文后粘贴。')
    return
  }
  const version = ++fileReadVersion
  reading.value = true
  const reader = new FileReader()
  reader.onload = () => {
    if (version !== fileReadVersion) return
    reading.value = false
    text.value = reader.result || ''
    fileName.value = f.name
    error.value = ''
    ElMessage.success(`已读取：${f.name}（${text.value.length} 字）`)
  }
  reader.onerror = () => {
    if (version !== fileReadVersion) return
    reading.value = false
    ElMessage.error('文件读取失败，请重新选择或粘贴正文')
  }
  reader.readAsText(f)
}

function clearDraft() {
  if (store.running) return
  fileReadVersion++
  reading.value = false
  text.value = ''; fileName.value = ''; error.value = ''
}

function onFile(uploadFile) {
  handleFile(uploadFile?.raw)
}

// 页面级拖拽：整个工作台区域都能拖入文件（主流上传体验），
// 并阻止浏览器默认"打开文件"行为
function onDragOver(e) { e.preventDefault() }
function onDrop(e) {
  e.preventDefault()
  if (visible && !e.target.closest?.('.uploader')) handleFile(e.dataTransfer?.files?.[0])
}

async function loadAvailablePlaybooks() {
  if (store.mode !== 'online') return
  try {
    playbooks.value = await fetchPlaybooks(store.contractType)
    if (!playbooks.value.some(book => `${book.playbookId}:${book.version}` === selectedPlaybook.value)) selectedPlaybook.value = ''
  } catch { playbooks.value = []; selectedPlaybook.value = '' }
}
watch(() => [store.contractType, store.mode], loadAvailablePlaybooks)
function restoreReview() {
  if (store.running || store.mode !== 'online') return
  const saved = localStorage.getItem(ACTIVE_RUN_KEY)
  if (!saved) return
  try { return resumePersistedReview(JSON.parse(saved)) }
  catch { localStorage.removeItem(ACTIVE_RUN_KEY) }
}
onActivated(() => {
  visible = true
  if (store.running) startClock()
  else return restoreReview()
})
onDeactivated(() => { visible = false; stopClock() })
onMounted(async () => {
  document.addEventListener('dragover', onDragOver)
  document.addEventListener('drop', onDrop)
  restoreReview()
  await loadAvailablePlaybooks()
})
onUnmounted(() => {
  document.removeEventListener('dragover', onDragOver)
  document.removeEventListener('drop', onDrop)
})

async function reviewText() {
  if (store.running || reading.value) return
  const content = text.value.trim()
  if (!content) { ElMessage.warning('请先上传或粘贴合同文本'); return }
  if (store.mode === 'offline') { ElMessage.info('离线演示模式请使用右侧示例合同一键载入'); return }

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
    const contractName = fileName.value || (pasteText.value.trim() ? '粘贴合同' : '上传合同')
    const result = await uploadAndReview(content, store.contractType, applyProgress, (taskId) => {
      localStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify({ taskId, contractName }))
    }, reviewPlaybook.value)
    finishReview(result, contractName)
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

function applyProgress(rep) {
  if (typeof rep.stage !== 'number') return
  store.stage = rep.stage
  store.stageStatus = rep.stageStatus || 'running'
  store.stageTimes = rep.stageTimes || [0, 0, 0, 0]
  store.stageDetail = rep.stageDetail || ''
  if (rep.stageStartedAt) store.stageStartedAt = rep.stageStartedAt
  else if (!store.stageStartedAt) store.stageStartedAt = Date.now()
}

function finishReview(result, contractName) {
  localStorage.removeItem(ACTIVE_RUN_KEY)
  store.report = result.report
  store.reviewRunId = result.taskId
  store.contractName = contractName
  store.stage = STAGES.length - 1
  store.stageStatus = 'done'
  ElMessage.success('审查完成，已跳转报告')
  emit('open-report')
}

async function resumePersistedReview(saved) {
  if (!saved?.taskId || store.running) return
  store.running = true
  store.stageStartedAt = Date.now()
  error.value = ''
  try {
    finishReview(await resumeReview(saved.taskId, applyProgress), saved.contractName || '恢复的合同')
  } catch (e) {
    error.value = e.message || '审查恢复失败'
  } finally {
    store.running = false
  }
}

async function loadDemo(id) {
  if (store.running || reading.value) return
  store.running = true
  store.stage = 0
  store.stageStatus = 'done'   // 离线缓存瞬时完成：全部阶段绿勾
  store.stageTimes = [0, 0, 0, 0]
  store.stageDetail = ''
  error.value = ''
  try {
    const report = await loadDemoReport(id)
    store.report = report
    store.reviewRunId = null
    store.contractName = report.contract?.name || DEMO_CARDS.find(item => item.id === id)?.name || '示例合同'
    store.stageTimes = report.meta?.stageTimes || [0, 0, 0, 0]
    store.stage = STAGES.length - 1
    ElMessage.success('已打开示例报告')
    emit('open-report')
  } catch (e) {
    error.value = String(e.message || e)
  } finally {
    store.running = false
  }
}
defineExpose({ resumePersistedReview })
</script>
