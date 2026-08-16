<template>
  <div class="eval">
    <!-- 口径切换 -->
    <div class="split-bar">
      <el-radio-group v-model="split" size="small">
        <el-radio-button value="test">🎯 held-out test（最终汇报）</el-radio-button>
        <el-radio-button value="dev">🔧 dev（调参）</el-radio-button>
      </el-radio-group>
      <div v-if="data" class="overall-pass">
        <el-tag :type="cur.pass ? 'success' : 'danger'" size="small" effect="dark">
          {{ cur.pass ? '✅ 及格线全部通过' : '❌ 未通过' }}
        </el-tag>
      </div>
    </div>

    <el-alert
      type="info" :closable="false" show-icon class="honest"
      title="诚实声明：所有数字由真实 pipeline 跑出（test 全程只碰一次，不同模板族防过拟合）；严格口径 = 条款+类型+严重度 逐字段一致才命中。"
    />

    <template v-if="data">
      <!-- 关键指标卡 -->
      <div class="kpi-grid">
        <div class="kpi">
          <div class="kpi-label">植入缺陷组召回率</div>
          <div class="kpi-val" :style="{ color: cur.C.recall >= 0.85 ? 'var(--sev-low)' : 'var(--sev-high)' }">
            {{ pct(cur.C.recall) }}
          </div>
          <div class="kpi-track"><div class="kpi-fill ok" :style="{ width: Math.min(cur.C.recall * 100, 100) + '%' }" /></div>
          <div class="kpi-sub">及格线 ≥ 85%</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">干净组误报率</div>
          <div class="kpi-val" :style="{ color: (cur.C.cleanFpRate ?? 1) <= 0.15 ? 'var(--sev-low)' : 'var(--sev-high)' }">
            {{ pct(cur.C.cleanFpRate) }}
          </div>
          <div class="kpi-track"><div class="kpi-fill" :style="{ width: Math.min((cur.C.cleanFpRate ?? 0) * 100, 100) + '%' }" /></div>
          <div class="kpi-sub">及格线 ≤ 15%（越低越好）</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">赢规则基线（F1 增益）</div>
          <div class="kpi-val" style="color: var(--sev-low)">+{{ ((cur.C.f1 - cur.baseline.f1) * 100).toFixed(1) }}pt</div>
          <div class="kpi-track"><div class="kpi-fill ok" :style="{ width: Math.min((cur.C.f1 - cur.baseline.f1) * 100 * 5, 100) + '%' }" /></div>
          <div class="kpi-sub">及格线 ≥ +10 点</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">成本 / 延迟（C 档）</div>
          <div class="kpi-val" style="font-size: 22px">{{ money(cur.C.costPerContract) }}<span class="kpi-unit">/份</span></div>
          <div class="kpi-sub">及格线 &lt; 1 元 · &lt; 60s</div>
        </div>
      </div>

      <!-- 消融对比 -->
      <div class="panel">
        <div class="panel-title">消融实验（复核 agent 价值验证）</div>
        <div class="ablation-bars">
          <div v-for="m in ABLATION" :key="m.mode" class="ab-row">
            <div class="ab-label">{{ m.name }}</div>
            <div class="ab-track">
              <div class="ab-bar recall" :style="{ width: (cur[m.mode].recall * 100).toFixed(1) + '%' }">
                <span class="ab-val">召回 {{ pct(cur[m.mode].recall) }}</span>
              </div>
            </div>
            <div class="ab-track">
              <div class="ab-bar precision" :style="{ width: (cur[m.mode].precision * 100).toFixed(1) + '%' }">
                <span class="ab-val">精确 {{ pct(cur[m.mode].precision) }}</span>
              </div>
            </div>
            <div class="ab-num">F1 {{ pct(cur[m.mode].f1) }}</div>
          </div>
        </div>
        <el-table :data="ablationRows" border stripe size="small" class="ab-table">
          <el-table-column prop="name" label="档位" width="150" />
          <el-table-column prop="recall" label="召回率" />
          <el-table-column prop="precision" label="精确率" />
          <el-table-column prop="f1" label="F1" />
          <el-table-column prop="fp" label="干净组误报率" />
          <el-table-column prop="cost" label="成本/份" />
          <el-table-column prop="latency" label="延迟/份" />
        </el-table>
        <div v-if="cur.C.reviewModule" class="review-note">
          🛡 复核模块级（C 档）：滤掉真误报 <b>{{ cur.C.reviewModule.filteredFalsePositives }}</b>，
          误杀真阳性 <b>{{ cur.C.reviewModule.killedTruePositives }}</b>，
          打回重证改判正确率 <b>{{ pct(cur.C.reviewModule.revertVerdictAccuracy) }}</b>
          —— 复核价值独立量化
        </div>
      </div>

      <!-- 及格线 -->
      <div class="panel">
        <div class="panel-title">及格线（Definition of Done · C 档为准）</div>
        <div v-for="c in checks" :key="c.name" class="check-row">
          <span class="check-icon" :class="c.ok ? 'ok' : 'no'">{{ c.ok ? '✓' : '✗' }}</span>
          <span class="check-name">{{ c.name }}</span>
          <span class="check-val">{{ c.val }}</span>
          <span class="check-line" :class="c.ok ? 'ok' : 'no'">{{ c.ok ? '达标' : '未达标' }}</span>
        </div>
      </div>

      <!-- 边界组 -->
      <div class="panel">
        <div class="panel-title">边界条款组（单独报 · 不进主指标）</div>
        <div class="boundary-grid">
          <div class="b-item" v-for="m in ABLATION" :key="m.mode">
            <div class="b-name">{{ m.name }}</div>
            <div class="b-row"><span>争议识别</span><b>{{ pct(cur[m.mode].boundary?.disputedIdentification) }}</b></div>
            <div class="b-row"><span>严重度倾向一致</span><b>{{ pct(cur[m.mode].boundary?.severityTendencyAgree) }}</b></div>
            <div class="b-row"><span>承认不确定性</span><b>{{ pct(cur[m.mode].boundary?.uncertaintyAcknowledged) }}</b></div>
          </div>
        </div>
      </div>

      <!-- 评测口径与方法 -->
      <div class="panel">
        <el-collapse>
          <el-collapse-item name="method">
            <template #title>
              <span class="panel-title" style="margin: 0">📖 评测口径与方法（防"背答案"设计）</span>
            </template>
            <div class="method-body">
              <div class="method-sec">
                <b>严格计分口径</b>——主指标 = 风险对级：<code>(条款, 风险类型, 严重度)</code> 三元组与标准答案
                <b>逐字段完全一致</b>才计 1 次命中；召回 = 命中 / 植入缺陷总数；精确 = 命中 / 模型输出总数。
                部分分（条款 0.4 + 类型 0.3 + 严重度 0.3）仅作诊断，不进简历数字。
              </div>
              <div class="method-sec">
                <b>金标准集三组</b>——植入缺陷组（官方示范文本骨架 + 按风险矩阵植入缺陷，植入记录=标准答案）、
                干净合同组（示范文本原样，算误报密度）、边界条款组（真实裁判争议点，单独报）。
              </div>
              <div class="method-sec">
                <b>三源解耦（防背答案）</b>——① 植入缺陷混入<b>规则抓不到的变体</b>（占比 78%：组合风险/跨条款推理/表述含糊）；
                ② 规则基线保持朴素（只抓最直接确定性模式）；③ worker 判定独立于植入记录。
              </div>
              <div class="method-sec">
                <b>防过拟合</b>——三组合计切 dev（调参，~70%）与 held-out test（最终汇报，~30%）；
                test 用<b>不同模板族 + 不同种子 + 不同植入组合</b>，全程只碰一次；简历数字一律来自 held-out test。
              </div>
              <div class="method-sec">
                <b>诚实声明</b>——所有数字由真实 pipeline 跑出；人工抽样复核 ~20% 已做（meta.json）；
                盲标交叉验证（Qwen 不同家族）仅质检标注质量，不修正 worker 输出。本系统为初筛助手，输出需人工终审，不构成法律意见。
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </template>
    <el-empty v-else description="评测数据缺失：请先运行 scripts/export_eval_summary.py" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { loadEvalResults } from '../api.js'

const data = ref(null)
const split = ref('test') // 默认 held-out test（最终汇报口径）
onMounted(async () => { data.value = await loadEvalResults() })

const ABLATION = [
  { mode: 'A', name: 'A · 无复核' },
  { mode: 'B', name: 'B · 复核直滤' },
  { mode: 'C', name: 'C · 复核+打回' },
  { mode: 'baseline', name: '规则基线' },
]

const cur = computed(() => data.value?.[split.value] || {})
const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
const money = (v) => (v == null ? '—' : `${v} 元`)

const ablationRows = computed(() =>
  ABLATION.map((m) => ({
    name: m.name,
    recall: pct(cur.value[m.mode]?.recall),
    precision: pct(cur.value[m.mode]?.precision),
    f1: pct(cur.value[m.mode]?.f1),
    fp: pct(cur.value[m.mode]?.cleanFpRate),
    cost: money(cur.value[m.mode]?.costPerContract),
    latency: cur.value[m.mode]?.latencyPerContract == null ? '—' : `${cur.value[m.mode].latencyPerContract}s`,
  }))
)

const checks = computed(() => {
  const d = cur.value
  if (!d?.C) return []
  return [
    { name: '植入缺陷组召回率 ≥ 85%', ok: d.C.recall >= 0.85, val: pct(d.C.recall) },
    { name: '干净组误报率 ≤ 15%', ok: (d.C.cleanFpRate ?? 1) <= 0.15, val: pct(d.C.cleanFpRate) },
    { name: '赢规则基线（召回/F1 ≥ +10 点）', ok: d.winBaseline, val: `召回 ${pct(d.C.recall - d.baseline.recall)} / F1 ${pct(d.C.f1 - d.baseline.f1)}` },
    { name: '成本 < 1 元/份', ok: (d.C.costPerContract ?? 99) < 1, val: money(d.C.costPerContract) },
    { name: '延迟 < 60s/份', ok: (d.C.latencyPerContract ?? 999) < 60, val: `${d.C.latencyPerContract}s` },
  ]
})
</script>

<style scoped>
.eval { display: flex; flex-direction: column; gap: 16px; }
.split-bar { display: flex; align-items: center; justify-content: space-between; }
.honest { margin: 0; }

.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.kpi {
  background: #fff; border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 14px 18px;
}
.kpi-label { font-size: 12px; color: var(--ink-3); }
.kpi-val { font-size: 28px; font-weight: 700; margin: 6px 0 8px; font-variant-numeric: tabular-nums; }
.kpi-unit { font-size: 12px; font-weight: 400; color: var(--ink-3); margin-left: 4px; }
.kpi-track { height: 6px; background: #f3f4f6; border-radius: 4px; overflow: hidden; }
.kpi-fill { height: 6px; border-radius: 4px; background: linear-gradient(90deg, #fbbf24, var(--sev-medium)); transition: width 0.6s ease; }
.kpi-fill.ok { background: linear-gradient(90deg, #34d399, var(--sev-low)); }
.kpi-sub { font-size: 11px; color: var(--ink-3); margin-top: 6px; }

.ablation-bars { margin-bottom: 14px; }
.ab-row { display: grid; grid-template-columns: 130px 1fr 1fr 70px; gap: 10px; align-items: center; margin: 8px 0; }
.ab-label { font-size: 12px; color: var(--ink-2); text-align: right; }
.ab-track { height: 22px; background: #f3f4f6; border-radius: 6px; overflow: hidden; position: relative; }
.ab-bar { height: 22px; border-radius: 6px; min-width: 2px; display: flex; align-items: center; transition: width 0.6s ease; }
.ab-bar.recall { background: linear-gradient(90deg, #60a5fa, #2563eb); }
.ab-bar.precision { background: linear-gradient(90deg, #a5b4fc, #6366f1); }
.ab-val { font-size: 11px; color: #fff; padding-left: 8px; white-space: nowrap; }
.ab-num { font-size: 12px; font-weight: 600; color: var(--ink); }
.review-note {
  margin-top: 12px; font-size: 13px; color: var(--ink-2);
  background: var(--el-color-primary-light-9); border-radius: 8px; padding: 10px 14px; line-height: 1.7;
}

.check-row { display: flex; align-items: center; gap: 12px; padding: 10px 4px; border-bottom: 1px solid #f3f4f6; }
.check-icon {
  width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 700; color: #fff; flex: none;
}
.check-icon.ok { background: var(--sev-low); }
.check-icon.no { background: var(--sev-high); }
.check-name { font-size: 14px; color: var(--ink); flex: 1; }
.check-val { color: var(--ink-2); font-size: 13px; font-variant-numeric: tabular-nums; }
.check-line { font-size: 12px; padding: 1px 10px; border-radius: 10px; }
.check-line.ok { color: var(--sev-low); background: var(--sev-low-bg); }
.check-line.no { color: var(--sev-high); background: var(--sev-high-bg); }

.boundary-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.b-item { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }
.b-name { font-size: 13px; font-weight: 600; color: var(--ink); margin-bottom: 8px; }
.b-row { display: flex; justify-content: space-between; font-size: 12px; color: var(--ink-2); padding: 3px 0; }
.b-row b { font-variant-numeric: tabular-nums; }

.method-body { font-size: 13px; color: var(--ink-2); line-height: 1.9; }
.method-sec { margin-bottom: 12px; padding: 10px 14px; background: #f8fafc; border-radius: 8px; border: 1px solid var(--line); }
.method-sec code { background: var(--el-color-primary-light-9); color: var(--el-color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
</style>
