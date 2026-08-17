<template>
  <div class="settings">
    <!-- ① 供应商管理 -->
    <div class="panel">
      <div class="panel-title">
        🔑 供应商管理
        <span class="panel-sub">填入各提供方的 API 密钥即可使用其模型；密钥仅存本机 settings.json（已 gitignore）</span>
      </div>
      <div v-for="(p, pid) in providers" :key="pid" class="provider-card">
        <div class="pc-head">
          <div class="pc-name">
            <b>{{ providerName(pid) }}</b>
            <span class="pc-id">{{ pid }}</span>
          </div>
          <el-tag :type="p.hasKey ? 'success' : 'info'" size="small" effect="light">
            {{ p.hasKey ? '✅ 已配置密钥' : '未配置密钥' }}
          </el-tag>
        </div>
        <el-form label-width="96px" size="default" class="pc-form">
          <el-form-item label="Base URL">
            <el-input v-model="p.baseUrl" placeholder="OpenAI 兼容端点，如 https://api.deepseek.com/v1" />
          </el-form-item>
          <el-form-item label="API Key">
            <el-input
              v-model="keys[pid]"
              type="password"
              show-password
              :placeholder="p.hasKey ? '已配置（留空保持不变）' : '粘贴 API Key'"
            />
          </el-form-item>
          <el-form-item label="单价 ¥ / M">
            <div class="price-row">
              <el-input-number v-model="p.priceIn" :min="0" :step="0.1" :precision="2" controls-position="right" />
              <span class="price-sep">/</span>
              <el-input-number v-model="p.priceOut" :min="0" :step="0.1" :precision="2" controls-position="right" />
              <span class="price-hint">输入 / 输出（元/百万 tokens）</span>
            </div>
          </el-form-item>
          <el-form-item label="模型列表">
            <el-select v-model="p.models" multiple filterable allow-create default-first-option class="models-select" placeholder="选择或输入模型名（可“获取模型列表”拉取）">
              <el-option v-for="m in p.models" :key="m" :label="m" :value="m" />
            </el-select>
          </el-form-item>
          <div class="pc-actions">
            <el-button size="small" :loading="loadingModels === pid" @click="loadModels(pid)">🔄 获取模型列表</el-button>
            <el-button size="small" :loading="testing === pid" @click="testConn(pid)">🔌 测试连接</el-button>
            <el-button size="small" type="primary" @click="saveAll">💾 保存全部设置</el-button>
          </div>
        </el-form>
      </div>
    </div>

    <!-- ② 模型路由（审查 / 复核 双模型可独立配置） -->
    <div class="panel">
      <div class="panel-title">
        🧭 模型路由
        <span class="panel-sub">审查（主链路抽取/worker/报告）与复核（对抗复核）可分别选择供应商与模型</span>
      </div>
      <el-form label-width="150px" class="route-form">
        <el-form-item label="审查模型（主链路）">
          <div class="route-row">
            <el-select v-model="route.main.provider" class="route-provider" placeholder="供应商" @change="(v) => onProviderChange('main', v)">
              <el-option v-for="(p, pid) in providers" :key="pid" :label="providerName(pid)" :value="pid" :disabled="!p.hasKey" />
            </el-select>
            <el-select v-model="route.main.model" class="route-model" placeholder="模型" filterable>
              <el-option v-for="m in providerModels(route.main.provider)" :key="m" :label="m" :value="m" />
            </el-select>
            <el-tag v-if="!providers[route.main.provider]?.hasKey" type="warning" size="small">该供应商未配置密钥</el-tag>
          </div>
        </el-form-item>
        <el-form-item label="复核模型（thinking 档）">
          <div class="route-row">
            <el-select v-model="route.review.provider" class="route-provider" placeholder="供应商" @change="(v) => onProviderChange('review', v)">
              <el-option v-for="(p, pid) in providers" :key="pid" :label="providerName(pid)" :value="pid" :disabled="!p.hasKey" />
            </el-select>
            <el-select v-model="route.review.model" class="route-model" placeholder="模型" filterable>
              <el-option v-for="m in providerModels(route.review.provider)" :key="m" :label="m" :value="m" />
            </el-select>
          </div>
          <div class="route-tip">💡 复核负责对抗性过滤与法条查证，建议选 stronger/thinking 档模型（如 deepseek-v4-pro、kimi-k3）</div>
        </el-form-item>
        <div class="route-submit">
          <el-button type="primary" :loading="saving" @click="saveAll">应用模型路由（下次审查即时生效）</el-button>
          <el-button @click="testRoute">🔌 测试当前路由</el-button>
        </div>
      </el-form>
    </div>

    <!-- ③ 通用设置 -->
    <div class="panel">
      <div class="panel-title">
        ⚙️ 通用设置
        <span class="panel-sub">保存后重启后端生效（env 优先于本页配置）</span>
      </div>
      <el-form label-width="150px" class="common-form">
        <el-form-item label="复核档位（REVIEW_MODE）">
          <el-radio-group v-model="common.reviewMode">
            <el-radio-button value="A">A 无复核</el-radio-button>
            <el-radio-button value="B">B 复核直滤</el-radio-button>
            <el-radio-button value="C">C 复核+打回（推荐）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="Worker 输入预算（tokens）">
          <el-input-number v-model="common.workerBudgetTokens" :min="1000" :max="32000" :step="1000" />
        </el-form-item>
        <el-form-item label="法条 Top-K">
          <el-input-number v-model="common.topKArticles" :min="1" :max="10" />
        </el-form-item>
        <el-form-item label="上传大小上限（字符）">
          <el-input-number v-model="common.maxUploadChars" :min="10000" :max="2000000" :step="10000" />
        </el-form-item>
        <el-form-item label="余额预警阈值（元）">
          <el-input-number v-model="common.balanceThreshold" :min="0" :max="100" :step="0.5" :precision="1" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="saveAll">保存通用设置</el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchSettings, saveSettings, fetchProviderModels, testProvider } from '../api.js'

const providers = reactive({})
const keys = reactive({})          // 新填的 key（不回显已存密钥）
const route = reactive({ main: { provider: '', model: '' }, review: { provider: '', model: '' } })
const common = reactive({ reviewMode: 'C', workerBudgetTokens: 8000, topKArticles: 3, maxUploadChars: 200000, balanceThreshold: 5 })
const loadingModels = ref('')
const testing = ref('')
const saving = ref(false)

const PROVIDER_NAMES = { deepseek: 'DeepSeek 官方', 'opencode-go': 'OpenCode Go（Zen）', siliconflow: '硅基流动' }
const providerName = (pid) => PROVIDER_NAMES[pid] || pid

function providerModels(pid) {
  return providers[pid]?.models || []
}

function onProviderChange(role, pid) {
  // 切换供应商时清空模型，等用户选择
  route[role].model = ''
}

async function loadModels(pid) {
  loadingModels.value = pid
  try {
    const r = await fetchProviderModels(pid)
    providers[pid].models = r.models || []
    ElMessage.success(`已获取 ${r.models.length} 个模型（${r.source === 'live' ? '实时拉取' : '本地预置'}）`)
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally {
    loadingModels.value = ''
  }
}

async function testConn(pid) {
  const model = route.main.provider === pid && route.main.model ? route.main.model : providerModels(pid)[0]
  if (!model) { ElMessage.warning('请先选择（或获取）该供应商的模型再测试'); return }
  testing.value = pid
  try {
    const r = await testProvider(pid, model)
    if (r.ok) {
      ElMessage.success(`✅ ${pid} 连接成功（${model}，tokens in=${r.usage?.in} out=${r.usage?.out}）`)
    } else {
      ElMessage.error(`❌ 连接失败：${r.error || '未知错误'}`)
    }
  } catch (e) {
    ElMessage.error(String(e.message || e))
  } finally {
    testing.value = ''
  }
}

async function testRoute() {
  const { main, review } = route
  if (!main.provider || !main.model) { ElMessage.warning('请先配置审查模型'); return }
  ElMessage.info('测试审查模型…')
  const r1 = await testProvider(main.provider, main.model)
  if (!r1.ok) { ElMessage.error(`审查模型测试失败：${r1.error}`); return }
  ElMessage.success(`✅ 审查模型 ${main.model} 可用`)
  if (review.provider && review.model) {
    ElMessage.info('测试复核模型…')
    const r2 = await testProvider(review.provider, review.model)
    if (r2.ok) ElMessage.success(`✅ 复核模型 ${review.model} 可用`)
    else ElMessage.error(`复核模型测试失败：${r2.error}`)
  }
}

function buildPayload() {
  const providersPayload = {}
  for (const [pid, p] of Object.entries(providers)) {
    providersPayload[pid] = {
      baseUrl: p.baseUrl,
      apiKey: keys[pid] || '',        // 空 = 保留原值
      models: p.models,
      priceIn: p.priceIn,
      priceOut: p.priceOut,
    }
  }
  return {
    providers: providersPayload,
    mainModel: route.main.provider ? { provider: route.main.provider, model: route.main.model } : undefined,
    reviewModel: route.review.provider ? {
      provider: route.review.provider,
      model: route.review.model,
      priceIn: providers[route.review.provider]?.priceIn,
      priceOut: providers[route.review.provider]?.priceOut,
    } : undefined,
    common: { ...common },
  }
}

async function saveAll() {
  saving.value = true
  try {
    const data = await saveSettings(buildPayload())
    // 用返回的脱敏视图刷新本页（key 状态来自 hasKey）
    Object.keys(providers).forEach((k) => delete providers[k])
    Object.keys(keys).forEach((k) => delete keys[k])
    applyServer(data)
    ElMessage.success('设置已保存（密钥保留，仅存本机 settings.json）')
  } catch (e) {
    ElMessage.error(`保存失败：${e.message || e}`)
  } finally {
    saving.value = false
  }
}

function applyServer(data) {
  for (const [pid, p] of Object.entries(data.providers || {})) {
    providers[pid] = reactive({ baseUrl: p.baseUrl, hasKey: p.hasKey, models: p.models || [], priceIn: p.priceIn, priceOut: p.priceOut })
  }
  if (data.mainModel?.provider) {
    route.main.provider = data.mainModel.provider
    route.main.model = data.mainModel.model
  }
  if (data.reviewModel?.provider) {
    route.review.provider = data.reviewModel.provider
    route.review.model = data.reviewModel.model
  }
  if (data.common) Object.assign(common, data.common)
}

onMounted(async () => {
  try {
    applyServer(await fetchSettings())
  } catch (e) {
    ElMessage.error(`读取设置失败：${e.message || e}`)
  }
})
</script>

<style scoped>
.settings { display: flex; flex-direction: column; gap: 16px; max-width: 920px; }
.panel-title { margin-bottom: 12px; display: flex; align-items: baseline; gap: 10px; }
.panel-sub { font-size: 12px; color: var(--ink-3); font-weight: 400; }

.provider-card {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 12px;
  background: #fff;
}
.pc-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.pc-name { display: flex; align-items: baseline; gap: 8px; }
.pc-id { font-size: 11px; color: var(--ink-3); }
.pc-form :deep(.el-form-item) { margin-bottom: 10px; }
.pc-actions { display: flex; gap: 8px; justify-content: flex-end; }

.price-row { display: flex; align-items: center; gap: 8px; }
.price-sep { color: var(--ink-3); }
.price-hint { font-size: 11px; color: var(--ink-3); margin-left: 6px; }
.models-select { width: 100%; }

.route-form { margin-top: 4px; }
.route-row { display: flex; gap: 8px; align-items: center; flex: 1; }
.route-provider { width: 180px; }
.route-model { flex: 1; }
.route-tip { font-size: 12px; color: var(--ink-3); margin-top: 4px; }
.route-submit { margin-left: 150px; display: flex; gap: 8px; }

.common-form :deep(.el-form-item) { margin-bottom: 12px; }
</style>