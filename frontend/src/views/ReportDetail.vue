<template>
  <div class="report">
    <!-- 顶部概览 -->
    <div class="overview">
      <div class="stat-card" :class="'sev-high'">
        <div class="stat-num" style="color: var(--sev-high)">{{ s.high }}</div>
        <div class="stat-lbl">🔴 高危</div>
      </div>
      <div class="stat-card" :class="'sev-medium'">
        <div class="stat-num" style="color: var(--sev-medium)">{{ s.medium }}</div>
        <div class="stat-lbl">🟡 中危</div>
      </div>
      <div class="stat-card" :class="'sev-low'">
        <div class="stat-num" style="color: var(--sev-low)">{{ s.low }}</div>
        <div class="stat-lbl">🟢 低危</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ s.total }}</div>
        <div class="stat-lbl">合计风险</div>
      </div>
      <!-- 环形分布图（SVG） -->
      <div class="donut-card">
        <svg viewBox="0 0 120 120" class="donut">
          <circle cx="60" cy="60" r="44" fill="none" stroke="#f3f4f6" stroke-width="18" />
          <circle
            v-for="seg in donutSegs" :key="seg.color"
            cx="60" cy="60" r="44" fill="none"
            :stroke="seg.color" stroke-width="18"
            :stroke-dasharray="`${seg.len} ${100 - seg.len}`"
            :stroke-dashoffset="seg.offset"
            transform="rotate(-90 60 60)"
            class="donut-seg"
          />
          <text x="60" y="57" text-anchor="middle" class="donut-total">{{ s.total }}</text>
          <text x="60" y="74" text-anchor="middle" class="donut-lbl">风险</text>
        </svg>
        <div class="donut-legend">
          <span v-for="seg in donutSegs" :key="seg.label" class="legend-item">
            <i :style="{ background: seg.color }" />{{ seg.label }} {{ seg.len.toFixed(0) }}%
          </span>
        </div>
      </div>
      <div class="export-card">
        <div class="stat-lbl" style="margin-bottom: 8px">导出报告</div>
        <el-button type="primary" plain size="small" @click="exportJson">⬇ JSON</el-button>
        <el-button plain size="small" @click="exportWord">⬇ Word</el-button>
        <div v-if="report.meta?.mock" class="mock-tag">mock 演示数据</div>
        <div v-else class="real-tag">真实 pipeline 输出</div>
      </div>
    </div>

    <!-- 风险分布（按 worker 类目） -->
    <div v-if="catRows.length" class="panel dist-panel">
      <div class="panel-title">风险分布（按审查类目）</div>
      <div class="dist-grid">
        <div v-for="row in catRows" :key="row.id" class="dist-item">
          <div class="dist-name" :title="row.id">{{ row.name }}</div>
          <div class="dist-track">
            <div class="dist-fill" :style="{ width: row.pct + '%' }" />
          </div>
          <div class="dist-count">{{ row.n }}</div>
        </div>
      </div>
    </div>

    <!-- 主体：条款导航 ↔ 风险卡片 -->
    <div class="body">
      <div class="panel nav-panel">
        <div class="panel-title">条款导航 <span class="nav-count">{{ report.clauses.length }}</span></div>
        <el-input v-model="query" size="small" placeholder="搜索条款…" clearable class="nav-search" />
        <el-scrollbar :height="mainHeight" ref="navScroll">
          <div
            v-for="c in filteredClauses" :key="c.clauseId"
            class="clause-nav-item" :class="{ active: activeClause === c.clauseId }"
            @click="onClauseClick(c)"
          >
            <span class="dot" :class="c.riskLevel || 'none'" />
            <span class="cid">{{ c.clauseId }}</span>
            <span class="cquote">{{ c.quote.slice(0, 20) }}</span>
          </div>
        </el-scrollbar>
      </div>

      <div class="panel risk-panel">
        <div class="panel-title">
          风险清单
          <span class="nav-count">按严重度排序 · {{ report.risks.length }} 条</span>
          <el-tag v-if="hasDisputed" type="warning" size="small" effect="plain">含「有争议」项</el-tag>
        </div>
        <el-scrollbar :height="mainHeight">
          <el-empty v-if="!report.risks.length" description="未发现风险条款 ✓" :image-size="80" />
          <div
            v-for="r in report.risks" :key="r.id"
            class="risk-card" :class="`sev-${r.severity}`"
            :id="`risk-${r.clauseId}`"
            @click="onRiskClick(r)"
          >
            <div class="risk-head">
              <el-tag :type="tagType(r.severity)" size="small" effect="dark">{{ sevName(r.severity) }}</el-tag>
              <b class="rt">{{ r.riskType }}</b>
              <el-tag v-if="r.disputed" type="warning" size="small" effect="plain">有争议</el-tag>
              <span class="rcid">条款 {{ r.clauseId }}</span>
              <span class="rworker">{{ workerName(r.worker) }}</span>
            </div>
            <el-collapse class="risk-collapse">
              <el-collapse-item name="quote" title="📜 原文摘录">
                <div class="quote-block">{{ r.clauseQuote }}</div>
              </el-collapse-item>
              <el-collapse-item name="basis" title="⚖️ 法条依据">
                <el-tag size="small" :type="basisType(r.legalBasis.tier)" effect="light">{{ basisName(r.legalBasis.tier) }}</el-tag>
                <div v-if="r.legalBasis.articleId" class="basis">
                  <b>{{ r.legalBasis.articleId }}</b> · {{ r.legalBasis.version }}
                  <div class="quote-block basis-quote">{{ r.legalBasis.quote }}</div>
                </div>
                <div v-else-if="r.legalBasis.tier === 'none'" class="basis dim">提示性质，无明确法条依据（未硬编）</div>
                <div v-else class="basis dim">间接依据：诚信/公平原则等原则性条款（非直接条文）</div>
              </el-collapse-item>
              <el-collapse-item name="advice" title="💡 证据与修改建议">
                <div class="basis"><b>证据：</b>{{ r.evidence }}</div>
                <div class="basis"><b>建议：</b>{{ r.suggestion }}</div>
                <div v-if="r.suggestionClauseText" class="basis">
                  <b>示范条款：</b>
                  <el-button size="small" text type="primary" @click.stop="copy(r.suggestionClauseText)">📋 复制</el-button>
                  <div class="quote-block">{{ r.suggestionClauseText }}</div>
                </div>
                <div v-if="r.status === 'disputed'" class="basis disputed-note">
                  ⚠️ 复核驳回后 worker 坚持：{{ r.reVerifyJustification }}
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>
        </el-scrollbar>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({ report: { type: Object, required: true } })
const s = computed(() => props.report.summary || { high: 0, medium: 0, low: 0, total: 0 })
const activeClause = ref('')
const query = ref('')

const tagType = (sev) => ({ high: 'danger', medium: 'warning', low: 'success' }[sev] || 'info')
const sevName = (sev) => ({ high: '高危', medium: '中危', low: '低危' }[sev] || sev)
const basisType = (t) => ({ direct: 'success', indirect: 'warning', none: 'info' }[t] || 'info')
const basisName = (t) => ({ direct: '直接依据', indirect: '间接依据', none: '无直接依据' }[t] || t)
const workerName = (w) => (w ? `· ${w.replace('_', ' ')}` : '')
const hasDisputed = computed(() => props.report.risks.some((r) => r.disputed))

// 按类目风险分布（填满空白区，可视化多 worker 扇出产出）
const CAT_NAMES = {
  payment_invoice: '付款/开票', acceptance_delivery: '验收交付', ip: '知识产权',
  data_compliance: '数据合规', breach_liability: '违约责任', termination: '解除权',
  confidentiality: '保密', non_compete: '竞业限制', jurisdiction: '管辖/仲裁',
  notice: '通知送达', force_majeure: '不可抗力', tax: '税费', subcontract: '外包分包',
}
const catRows = computed(() => {
  const by = props.report.summary?.byCategory || {}
  const total = Math.max(Object.values(by).reduce((a, b) => a + b, 0), 1)
  return Object.entries(by)
    .sort((a, b) => b[1] - a[1])
    .map(([id, n]) => ({ id, name: CAT_NAMES[id] || id, n, pct: Math.round((n / total) * 100) }))
})

// 主体高度填满视口（CSS calc 自适应，小窗口用 max() 保底 360px，消除下方空白）
const mainHeight = 'max(360px, calc(100vh - 470px))'

// 环形图分段
const donutSegs = computed(() => {
  const total = Math.max(s.value.total, 1)
  const order = [
    { label: '高危', color: '#dc2626', v: s.value.high },
    { label: '中危', color: '#d97706', v: s.value.medium },
    { label: '低危', color: '#059669', v: s.value.low },
  ]
  let offset = 0
  return order
    .filter((x) => x.v > 0)
    .map((x) => {
      const len = (x.v / total) * 100
      const seg = { ...x, len, offset: -offset }
      offset += len
      return seg
    })
})

// 条款搜索
const filteredClauses = computed(() => {
  const q = query.value.trim()
  if (!q) return props.report.clauses
  return props.report.clauses.filter((c) => c.clauseId.includes(q) || c.quote.includes(q))
})

// 双向联动：点条款 → 滚动到风险卡片
function onClauseClick(c) {
  activeClause.value = c.clauseId
  if (c.riskLevel) {
    document.getElementById(`risk-${c.clauseId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

// 双向联动：点风险卡片 → 高亮条款导航并滚动
function onRiskClick(r) {
  activeClause.value = r.clauseId
  // S15：规范化精确匹配条款号（'第三条'/'第3条'/'3' 归一为 '3'），
  // 避免短条款号（如 '3'）用 includes 误中 '13/30/3.1' 等其它导航项
  const norm = (s) => String(s || '').replace(/[第条\s.]/g, '')
  const target = norm(r.clauseId)
  const items = [...document.querySelectorAll('.clause-nav-item')]
  let hit = items.find((n) => norm(n.querySelector('.cid')?.textContent) === target)
  // 兜底：精确不中且条款号足够辨识时，再尝试文本包含匹配（保留旧体验）
  if (!hit && target.length >= 2) {
    hit = items.find((n) => n.textContent.includes(r.clauseId))
  }
  hit?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

function copy(text) {
  navigator.clipboard?.writeText(text).then(() => ElMessage.success('已复制示范条款'))
}

function exportJson() {
  const blob = new Blob([JSON.stringify(props.report, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${props.report.contract?.name || 'report'}.json`
  a.click()
}

async function exportWord() {
  // 后端 python-docx 生成正式 Word 审阅报告（在线/离线报告均可导出）
  try {
    const resp = await fetch('/api/export/word', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report: props.report }),
    })
    if (!resp.ok) throw new Error(`后端导出失败：${resp.status}`)
    const blob = await resp.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${props.report.contract?.name || 'report'}.docx`
    a.click()
    ElMessage.success('Word 报告已导出')
  } catch (e) {
    ElMessage.error(`Word 导出失败：${e.message || e}（请确认后端已启动）`)
  }
}
</script>

<style scoped>
.report { display: flex; flex-direction: column; gap: 16px; }

/* 顶部概览 */
.overview {
  display: grid;
  grid-template-columns: repeat(4, minmax(110px, 1fr)) 1.4fr 1.1fr;
  gap: 12px;
}
.stat-card, .donut-card, .export-card {
  background: #fff; border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 14px 18px;
  display: flex; flex-direction: column; justify-content: center;
}
.stat-card { border-top: 3px solid var(--line); }
.stat-card.sev-high { border-top-color: var(--sev-high); }
.stat-card.sev-medium { border-top-color: var(--sev-medium); }
.stat-card.sev-low { border-top-color: var(--sev-low); }
.donut-card { flex-direction: row; align-items: center; gap: 14px; }
.donut { width: 96px; height: 96px; flex: none; }
.donut-seg { transition: stroke-dasharray 0.4s ease; }
.donut-total { font-size: 22px; font-weight: 700; fill: var(--ink); }
.donut-lbl { font-size: 10px; fill: var(--ink-3); }
.donut-legend { display: flex; flex-direction: column; gap: 5px; }
.legend-item { font-size: 12px; color: var(--ink-2); display: flex; align-items: center; gap: 6px; }
.legend-item i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.export-card { align-items: stretch; gap: 6px; }
.mock-tag, .real-tag { font-size: 11px; text-align: center; border-radius: 10px; padding: 2px 0; }
.mock-tag { color: var(--ink-3); background: #f3f4f6; }
.real-tag { color: var(--sev-low); background: var(--sev-low-bg); }

/* 风险分布（按类目） */
.dist-panel { margin-top: 0; }
.dist-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 8px 24px; }
.dist-item { display: flex; align-items: center; gap: 8px; }
.dist-name { width: 90px; font-size: 12px; color: var(--ink-2); text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dist-track { flex: 1; background: #f3f4f6; border-radius: 5px; height: 10px; overflow: hidden; }
.dist-fill { height: 10px; border-radius: 5px; background: linear-gradient(90deg, #60a5fa, var(--el-color-primary)); transition: width 0.6s ease; }
.dist-count { width: 26px; font-size: 12px; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }

/* 主体两栏 */
.body { display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: stretch; }
.nav-panel, .risk-panel { padding: 14px 16px; }
.nav-count { font-size: 12px; color: var(--ink-3); font-weight: 400; margin-left: 6px; }
.nav-search { margin-bottom: 10px; }

.risk-head { display: flex; align-items: center; gap: 8px; padding: 10px 14px 0; }
.risk-head .rt { flex: 1; font-size: 14px; color: var(--ink); }
.rcid { color: var(--ink-3); font-size: 12px; }
.rworker { color: var(--ink-3); font-size: 11px; }
.risk-collapse { padding: 0 14px 12px; }
.risk-collapse :deep(.el-collapse-item__header) { font-size: 13px; color: var(--ink-2); }
.basis { margin: 8px 0; font-size: 13px; color: var(--ink-2); line-height: 1.7; }
.basis-quote { margin-top: 6px; font-size: 12px; color: var(--ink-2); }
.basis.dim { color: var(--ink-3); font-style: italic; }
.disputed-note { color: var(--sev-medium); background: var(--sev-medium-bg); border-radius: 6px; padding: 8px 10px; }
</style>
