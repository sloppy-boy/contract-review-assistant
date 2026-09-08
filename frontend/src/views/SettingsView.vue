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

    <div class="panel queue-panel">
      <div class="panel-title">🧰 任务队列运维 <span class="panel-sub">仅展示控制面元数据；死信可重试，运行中任务可取消</span></div>
      <div class="queue-toolbar"><el-select v-model="queueFilter" clearable placeholder="全部状态" @change="loadQueue"><el-option label="排队中" value="queued" /><el-option label="运行中" value="running" /><el-option label="重试等待" value="retry_wait" /><el-option label="成功" value="succeeded" /><el-option label="死信" value="dead" /><el-option label="已取消" value="cancelled" /></el-select><el-button :loading="queueLoading" @click="loadQueue">刷新</el-button><el-button :loading="queueLoading" @click="requeueStale">回收失联任务</el-button></div>
      <el-alert v-if="queueError" :title="queueError" type="warning" :closable="false" />
      <div v-if="!queueTasks.length" class="queue-empty">暂无任务或当前身份没有运维权限。</div>
      <table v-else class="queue-table"><thead><tr><th>任务</th><th>类型</th><th>状态</th><th>尝试</th><th>最后错误</th><th>操作</th></tr></thead><tbody><tr v-for="task in queueTasks" :key="task.id"><td>{{ task.id }}</td><td>{{ task.kind }}</td><td>{{ task.status }}</td><td>{{ task.attempts }}/{{ task.maxAttempts }}</td><td>{{ task.lastError || '—' }}</td><td><el-button v-if="task.status === 'dead'" size="small" @click="retryQueueTask(task)">重试</el-button><el-button v-if="['queued','retry_wait','running'].includes(task.status)" size="small" type="danger" plain @click="cancelQueueTask(task)">取消</el-button></td></tr></tbody></table>
    </div>

    <div class="panel integration-panel">
      <div class="panel-title">🔗 外部集成 <span class="panel-sub">仅管理员可配置；密钥只写入后端运行库，不会回显</span></div>
      <el-alert title="支持通用签名 Webhook；事件失败会自动重试并记录告警状态。具体企业平台仍需厂商契约联调。" type="info" :closable="false" />
      <form class="integration-form" @submit.prevent="saveIntegration">
        <label>适配器<select v-model="integration.kind"><option v-for="kind in integrationKinds" :key="kind" :value="kind">{{ kind }}</option></select></label>
        <label>HTTPS 地址<input v-model="integration.url" type="url" required placeholder="https://hooks.example.com/events"></label>
        <label>允许的主机（逗号分隔）<input v-model="integration.allowedHosts" required placeholder="hooks.example.com"></label>
        <label>密钥（留空保留原值）<input v-model="integration.secret" type="password" autocomplete="new-password" placeholder="至少 8 个字符"></label>
        <label>超时（秒）<input v-model.number="integration.timeoutSeconds" type="number" min="0.1" max="30" step="0.1"></label>
        <label class="switch-field">启用 <el-switch v-model="integration.enabled" /></label>
        <div><el-button type="primary" native-type="submit" :loading="integrationLoading">保存集成</el-button><el-button @click.prevent="loadIntegrations">刷新</el-button></div>
      </form>
      <table v-if="integrations.length" class="queue-table"><thead><tr><th>适配器</th><th>地址</th><th>状态</th><th>密钥</th><th>操作</th></tr></thead><tbody><tr v-for="item in integrations" :key="item.integrationKind"><td>{{ item.integrationKind }}</td><td>{{ item.url }}</td><td>{{ item.enabled ? '已启用' : '已停用' }}</td><td>{{ item.hasSecret ? '已配置' : '未配置' }}</td><td><el-button size="small" @click="editIntegration(item)">编辑</el-button><el-button size="small" type="danger" plain @click="removeIntegration(item.integrationKind)">删除</el-button></td></tr></tbody></table>
      <p v-else class="queue-empty">暂无外部集成配置。</p>
      <details v-if="deliveries.length" class="delivery-details"><summary>最近投递与告警</summary><table class="queue-table"><thead><tr><th>时间</th><th>事件</th><th>资源</th><th>结果</th><th>尝试</th></tr></thead><tbody><tr v-for="item in deliveries.slice(0, 20)" :key="item.idempotencyKey"><td>{{ item.attemptedAt }}</td><td>{{ item.eventType }}</td><td>{{ item.resourceId }}</td><td :class="{ 'delivery-alert': item.alert }">{{ item.alert ? '需处理' : '成功' }}</td><td>{{ item.attemptCount }}</td></tr></tbody></table></details>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchSettings, saveSettings, fetchProviderModels, testProvider, store, apiFetch } from '../api.js'
import { createGovernanceClient } from '../governance-api.js'
import { createIntegrationClient } from '../integration-api.js'

const providers = reactive({})
const keys = reactive({})          // 新填的 key（不回显已存密钥）
const route = reactive({ main: { provider: '', model: '' }, review: { provider: '', model: '' } })
const common = reactive({ reviewMode: 'C', workerBudgetTokens: 8000, topKArticles: 3, maxUploadChars: 200000, balanceThreshold: 5 })
const loadingModels = ref('')
const testing = ref('')
const saving = ref(false)
const governance = createGovernanceClient(apiFetch)
const queueTasks = ref([]), queueFilter = ref(''), queueLoading = ref(false), queueError = ref('')
const integrationClient = createIntegrationClient(apiFetch)
const integrations = ref([]), deliveries = ref([]), integrationLoading = ref(false)
const integrationKinds = ['generic', 'enterprise_im', 'ticket', 'procurement_crm', 'electronic_signature']
const integration = reactive({ kind: 'generic', url: '', allowedHosts: '', secret: '', timeoutSeconds: 5, enabled: false })

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

async function loadSettings() {
  if (store.mode === 'offline') return  // 离线演示不依赖后端，跳过设置拉取
  try {
    const data = await fetchSettings()
    if (data) applyServer(data)
  } catch (e) {
    ElMessage.error(`读取设置失败：${e.message || e}`)
  }
}

async function loadQueue() {
  if (store.mode === 'offline') return
  queueLoading.value = true; queueError.value = ''
  try { queueTasks.value = (await governance.tasks({ status: queueFilter.value || undefined, kind: 'review' })).tasks || [] } catch (e) { queueTasks.value = []; queueError.value = e.message } finally { queueLoading.value = false }
}
async function retryQueueTask(task) { try { await governance.retryTask(task.id); ElMessage.success('死信已重新入队'); await loadQueue() } catch (e) { ElMessage.error(e.message) } }
async function cancelQueueTask(task) { try { await governance.cancelTask(task.id); ElMessage.success('任务已取消'); await loadQueue() } catch (e) { ElMessage.error(e.message) } }
async function requeueStale() { try { const result = await governance.requeueStale(300); ElMessage.success(`已回收 ${result.requeued?.length || 0} 个失联任务`); await loadQueue() } catch (e) { ElMessage.error(e.message) } }
async function loadIntegrations() {
  if (store.mode === 'offline') return
  try {
    const [config, history] = await Promise.all([integrationClient.list(), integrationClient.deliveries()])
    integrations.value = config.integrations || []; deliveries.value = history.deliveries || []
  } catch (e) { if (e.status !== 403) ElMessage.error(e.message) }
}
function editIntegration(item) {
  integration.kind = item.integrationKind; integration.url = item.url; integration.allowedHosts = (item.allowedHosts || []).join(', ')
  integration.secret = ''; integration.timeoutSeconds = item.timeoutSeconds; integration.enabled = item.enabled
}
async function saveIntegration() {
  integrationLoading.value = true
  try {
    await integrationClient.save(integration.kind, { integrationKind: integration.kind, url: integration.url, allowedHosts: integration.allowedHosts.split(/[,，]/).map(item => item.trim()).filter(Boolean), secret: integration.secret || undefined, timeoutSeconds: integration.timeoutSeconds, enabled: integration.enabled })
    integration.secret = ''; ElMessage.success('集成配置已保存'); await loadIntegrations()
  } catch (e) { ElMessage.error(e.message) } finally { integrationLoading.value = false }
}
async function removeIntegration(kind) {
  try { await ElMessageBox.confirm(`删除 ${kind} 集成配置？`, '确认删除', { type: 'warning' }); await integrationClient.remove(kind); ElMessage.success('集成配置已删除'); await loadIntegrations() } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message || e) }
}

onMounted(async () => { await loadSettings(); await loadQueue(); await loadIntegrations() })
// 从离线切回在线时重新加载后端设置
watch(() => store.mode, (m) => { if (m === 'online') { loadSettings(); loadQueue(); loadIntegrations() } })
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
.queue-toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
.queue-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.queue-table th, .queue-table td { border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: middle; }
.queue-empty { color: var(--ink-3); font-size: 13px; padding: 16px 0; }
.integration-form { display: grid; grid-template-columns: 1fr 2fr; gap: 12px 16px; margin: 16px 0; }
.integration-form label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--ink-2); }
.integration-form input, .integration-form select { font: inherit; border: 1px solid var(--line); border-radius: 5px; padding: 9px; box-sizing: border-box; width: 100%; }
.switch-field { flex-direction: row !important; align-items: center; gap: 10px !important; }
.delivery-details { margin-top: 16px; }
.delivery-alert { color: #b45309; font-weight: 600; }
@media (max-width: 720px) { .integration-form { grid-template-columns: 1fr; } }
</style>
