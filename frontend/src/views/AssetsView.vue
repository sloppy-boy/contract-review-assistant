<template>
  <section class="assets">
    <el-alert v-if="store.mode === 'offline'" title="合同资产使用工作区数据，请切换到在线模式。" :closable="false" />
    <template v-else>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <div class="panel import-panel">
        <div><h2>导入合同资产</h2><p>支持 TXT、MD、DOCX、PDF，最多 10 份、每份 10 MiB。扫描件需配置本地 OCR。导入后默认为私有。</p></div>
        <input v-if="canWrite" data-testid="asset-files" type="file" multiple accept=".txt,.md,.docx,.pdf" :disabled="busy" @change="importFiles">
        <ul v-if="importResults.length"><li v-for="(result, index) in importResults" :key="index">{{ result.filename }}：{{ result.error || '导入成功' }}</li></ul>
      </div>
      <div class="asset-layout">
        <aside class="panel">
          <h2>合同库</h2>
          <form @submit.prevent="refresh"><label>标题或内容<input v-model="query" placeholder="输入关键词"></label><label>标签<input v-model="tag" placeholder="全部标签"></label><el-button native-type="submit">筛选与刷新</el-button></form>
          <button v-for="asset in assets" :key="asset.id" class="asset-row" :class="{ selected: selected?.id === asset.id }" @click="selectAsset(asset.id)"><strong>{{ asset.title }}</strong><span>{{ asset.visibility === 'private' ? '私有 / 指定共享' : '租户共享' }} · {{ asset.tags.join('、') || '无标签' }}</span></button>
          <p v-if="!assets.length">暂无可访问的合同。</p>
        </aside>
        <article v-if="selected" class="panel">
          <h2>{{ selected.title }}</h2><p>创建者 {{ selected.createdBy }} · {{ selected.filename }}</p>
          <el-button :disabled="!versionDocument" @click="download">下载所选版本原件</el-button><el-button v-if="canWrite" :disabled="!versionDocument" data-testid="asset-review" @click="reviewVersion">将所选版本送入审查工作台</el-button>
          <section class="section">
            <h3>谈判修订版本</h3>
            <label>查看版本<select :value="versionDocument?.version" data-testid="asset-version" @change="selectVersion(Number($event.target.value))"><option v-for="version in versions" :key="version.version" :value="version.version">v{{ version.version }} · {{ version.filename }} · {{ version.createdBy }} · {{ time(version.createdAt) }}</option></select></label>
            <p v-if="versionDocument">v{{ versionDocument.version }} · 父版本 {{ versionDocument.parentVersion ? `v${versionDocument.parentVersion}` : '无（原稿）' }}<br><small class="hash">SHA-256：{{ versionDocument.contentHash }}</small></p>
            <label v-if="canManage">上传下一版修订稿<input data-testid="asset-revision-file" type="file" accept=".txt,.md,.docx,.pdf" :disabled="busy" @change="uploadVersion"></label>
            <p>每次上传保留新版本的原件、提取段落和来源；历史版本不可改写。</p>
            <form @submit.prevent="compareVersions">
              <div class="version-range"><label>基线版本<select v-model.number="compareFrom"><option v-for="version in versions" :key="version.version" :value="version.version">v{{ version.version }}</option></select></label><label>目标版本<select v-model.number="compareTo"><option v-for="version in versions" :key="version.version" :value="version.version">v{{ version.version }}</option></select></label></div>
              <label>基线审查运行 ID（可选，用于风险影响）<input v-model="reportRunId" maxlength="100" placeholder="填写基线版本已完成的审查运行 ID"></label>
              <el-button native-type="submit" data-testid="asset-compare">比较版本与风险影响</el-button>
            </form>
            <div v-if="comparison" data-testid="asset-diff">
              <p>{{ comparison.notice }}</p><p v-if="!comparison.changes.length">这两个版本的段落文字相同。</p>
              <div v-for="change in comparison.changes" :key="change.id" class="source"><strong>{{ changeLabel(change.type) }}</strong><p v-if="change.before">原 v{{ change.before.version }} · {{ location(change.before.location) }}：{{ change.before.text }}</p><p v-if="change.after">新 v{{ change.after.version }} · {{ location(change.after.location) }}：{{ change.after.text }}</p></div>
              <h3 v-if="comparison.runId">风险影响 · {{ comparison.runId }}</h3><div v-for="impact in comparison.riskImpacts" :key="impact.riskId" class="source"><strong>{{ impact.riskId }} · {{ impactLabel(impact.status) }}</strong><p>{{ impact.quote }}</p><p>{{ impact.basis }}</p><small v-for="ref in impact.references" :key="`${ref.version}-${ref.segmentId}`">v{{ ref.version }} · {{ location(ref.location) }}　</small></div>
            </div>
          </section>
          <form v-if="canManage" class="section" @submit.prevent="save">
            <label>标题<input v-model="editing.title" maxlength="200" required></label><label>标签（逗号分隔）<input v-model="editing.tags" maxlength="1000"></label>
            <label>可见范围<select v-model="editing.visibility"><option value="private">私有 / 指定共享</option><option value="tenant">租户内所有成员</option></select></label>
            <label>指定共享成员<select v-model="editing.sharedWith" multiple><option v-for="member in members" :key="member.subject" :value="member.subject">{{ member.subject }}</option></select></label>
            <el-button native-type="submit" :disabled="busy">保存资产信息</el-button>
          </form>
          <details v-if="versionDocument" class="section"><summary>所选版本提取信息与原文定位</summary><p v-for="warning in versionDocument.warnings" :key="warning">{{ warning }}</p><p>提取方式：{{ versionDocument.method }}。候选元数据需人工核对。</p><pre>{{ JSON.stringify(versionDocument.metadata, null, 2) }}</pre><div v-for="segment in versionDocument.segments" :key="segment.id" class="source"><small>{{ location(segment.location) }}</small><p>{{ segment.text }}</p></div></details>
          <section class="section"><h3>义务、续签与关键日期</h3><p>可记录历史逾期事项。服务运行时每分钟检查到期提醒，也可手动刷新。</p>
            <div v-for="item in obligations" :key="item.id" class="obligation"><span>{{ item.title }} · {{ time(item.dueAt) }} · {{ item.completedAt ? '已完成' : '待处理' }}</span><el-button v-if="canManage && !item.completedAt" :disabled="busy" @click="complete(item.id)">标为完成</el-button></div>
            <form v-if="canManage" @submit.prevent="addObligation"><label>事项<input v-model="obligation.title" data-testid="obligation-title" required maxlength="200"></label><label>类型<select v-model="obligation.kind"><option value="obligation">合同义务</option><option value="renewal">续签</option><option value="key_date">关键日期</option></select></label><label>截止时间（本地时间）<input v-model="obligation.dueAt" data-testid="obligation-due" type="datetime-local" required></label><el-button native-type="submit" data-testid="add-obligation" :disabled="busy">添加事项</el-button></form>
          </section>
        </article>
        <div v-else class="panel"><el-empty description="选择合同以查看原文、权限和履约事项" /></div>
      </div>
      <section class="panel section"><h2>跨合同查询</h2><p>仅检索你有权读取的合同。摘录问答展示匹配原文及引用，需结合完整合同判断。</p>
        <form class="search" @submit.prevent="search"><input v-model="question" data-testid="asset-question" required maxlength="500" placeholder="例如：付款期限"><select v-model="searchMode"><option value="keyword">全文关键词</option><option value="semantic">本地语义模型（需配置）</option></select><el-button native-type="submit" data-testid="asset-search">检索</el-button><el-button data-testid="asset-ask" :disabled="!question.trim()" @click="ask">摘录问答</el-button></form>
        <p v-if="answer" class="answer">{{ answer }}</p><div v-for="(citation, index) in citations" :key="index" class="source"><button class="citation" @click="selectAsset(citation.assetId)">{{ citation.title }} · {{ citation.location }}</button><p>{{ citation.text }}</p></div>
      </section>
      <section class="panel section"><h2>我的履约提醒</h2><el-button @click="refreshNotifications">检查到期事项</el-button><p v-if="!notifications.length">暂无到期提醒。</p><div v-for="item in notifications" :key="item.id" class="obligation"><button @click="selectAsset(item.assetId)">{{ item.obligationTitle }} · {{ item.assetTitle }} · {{ time(item.dueAt) }}</button><el-button v-if="!item.readAt" @click="markRead(item.id)">标为已读</el-button><span v-else>已读</span></div></section>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { apiFetch, store } from '../api.js'
import { createAssetClient } from '../asset-api.js'
import { createCollaborationClient } from '../collaboration-api.js'

const emit = defineEmits(['review'])
const client = createAssetClient(apiFetch), directory = createCollaborationClient(apiFetch)
const assets = ref([]), selected = ref(null), obligations = ref([]), notifications = ref([]), importResults = ref([]), members = ref([]), me = ref(null)
const versions = ref([]), versionDocument = ref(null), compareFrom = ref(1), compareTo = ref(1), reportRunId = ref(''), comparison = ref(null)
const error = ref(''), busy = ref(false), query = ref(''), tag = ref(''), question = ref(''), searchMode = ref('keyword'), citations = ref([]), answer = ref('')
const editing = reactive({ title: '', tags: '', visibility: 'private', sharedWith: [] })
const obligation = reactive({ title: '', kind: 'obligation', dueAt: '' })
let epoch = 0, selection = 0, listing = 0, searching = 0, versionSelection = 0, comparing = 0
const canWrite = computed(() => ['admin', 'requester', 'legal_reviewer'].includes(me.value?.role))
const canManage = computed(() => canWrite.value && (me.value?.role === 'admin' || selected.value?.createdBy === me.value?.subject))
const time = value => value ? new Date(value).toLocaleString('zh-CN') : ''
const changeLabel = value => ({ added: '新增段落', deleted: '删除段落', modified: '修改段落' }[value] || value)
const impactLabel = value => ({ still_valid: '原证据仍有效', review_required: '待复核', removed: '原证据已移除' }[value] || value)
const location = value => typeof value === 'string' ? value : Object.entries(value || {}).map(([key, number]) => `${{ paragraph: '段落', table: '表格', row: '行', page: '页' }[key] || key} ${number}`).join(' · ')
function fail(cause) { error.value = cause.message || '操作失败' }
async function refresh() {
  if (store.mode === 'offline') return
  const token = epoch, list = ++listing
  try {
    const [result, identity, team, inbox] = await Promise.all([client.list(query.value, tag.value), directory.me(), directory.members(), client.notifications()])
    if (token !== epoch || list !== listing) return
    assets.value = result.assets; me.value = identity; members.value = team.members; notifications.value = inbox.notifications
  } catch (cause) { if (token === epoch) fail(cause) }
}
async function selectAsset(id) {
  const token = epoch, current = ++selection
  versionSelection++; comparing++; versionDocument.value = null; comparison.value = null; versions.value = []; reportRunId.value = ''
  try {
    const [asset, items, history] = await Promise.all([client.get(id), client.obligations(id), client.versions(id)])
    if (token !== epoch || current !== selection) return
    selected.value = asset; obligations.value = items.obligations
    versions.value = history.versions; versionDocument.value = asset; compareFrom.value = Math.max(1, asset.version - 1); compareTo.value = asset.version
    Object.assign(editing, { title: asset.title, tags: asset.tags.join(', '), visibility: asset.visibility, sharedWith: [...asset.sharedWith] })
    obligation.title = ''; obligation.dueAt = ''
  } catch (cause) { if (token === epoch && current === selection) { selected.value = null; obligations.value = []; fail(cause) } }
}
async function selectVersion(version) {
  const token = epoch, current = selection, request = ++versionSelection, assetId = selected.value?.id
  if (!assetId) return
  versionDocument.value = null
  try { const result = await client.version(assetId, version); if (token === epoch && current === selection && request === versionSelection) versionDocument.value = result }
  catch (cause) { if (token === epoch && current === selection && request === versionSelection) fail(cause) }
}
function reviewVersion() { if (selected.value && versionDocument.value) emit('review', { ...selected.value, ...versionDocument.value, id: selected.value.id }) }
async function uploadVersion(event) {
  const file = event.target.files?.[0], asset = selected.value
  event.target.value = ''
  if (!file || !asset) return
  if (file.size > 10 * 1024 * 1024) { error.value = '每份修订稿不得超过 10 MiB'; return }
  await mutate(() => client.addVersion(asset.id, file, asset.revision))
}
async function compareVersions() {
  const token = epoch, current = selection, request = ++comparing, assetId = selected.value?.id
  if (!assetId) return
  comparison.value = null; error.value = ''
  try { const result = await client.diff(assetId, compareFrom.value, compareTo.value, reportRunId.value); if (token === epoch && current === selection && request === comparing) comparison.value = result }
  catch (cause) { if (token === epoch && current === selection && request === comparing) fail(cause) }
}
async function mutate(operation) {
  if (busy.value || store.mode === 'offline') return
  const token = epoch, current = selection
  busy.value = true; error.value = ''
  try {
    const result = await operation()
    if (token !== epoch) return
    if (current === selection && result?.id) await selectAsset(result.id)
    await refresh()
  } catch (cause) { if (token === epoch) { fail(cause); if (cause.status === 409 && current === selection && selected.value) await selectAsset(selected.value.id) } }
  finally { if (token === epoch) busy.value = false }
}
async function importFiles(event) {
  const files = Array.from(event.target.files || []), token = epoch
  event.target.value = ''
  if (!files.length) return
  if (files.length > 10 || files.some(file => file.size > 10 * 1024 * 1024)) { error.value = '最多上传 10 份，每份不得超过 10 MiB'; return }
  await mutate(async () => { const result = await client.importFiles(files); if (token !== epoch) return; importResults.value = result.results; return result.results.find(item => item.asset)?.asset })
}
async function save() { await mutate(() => client.update(selected.value.id, { expectedRevision: selected.value.revision, title: editing.title, tags: editing.tags.split(/[,，]/).map(s => s.trim()).filter(Boolean), visibility: editing.visibility, sharedWith: [...editing.sharedWith] })) }
async function addObligation() { const due = new Date(obligation.dueAt); if (!Number.isFinite(due.getTime())) { error.value = '请填写有效截止时间'; return }; await mutate(() => client.addObligation(selected.value.id, { expectedRevision: selected.value.revision, title: obligation.title, kind: obligation.kind, dueAt: due.toISOString() })) }
async function complete(id) { await mutate(() => client.completeObligation(selected.value.id, id, selected.value.revision)) }
async function retrieve(asQuestion) {
  const token = epoch, current = ++searching
  error.value = ''; citations.value = []; answer.value = ''
  try { const result = await (asQuestion ? client.ask(question.value) : client.search(question.value, searchMode.value)); if (token === epoch && current === searching) { citations.value = result.citations; answer.value = result.answer || (result.citations.length ? '' : '没有找到可访问的匹配依据。') } }
  catch (cause) { if (token === epoch && current === searching) fail(cause) }
}
const search = () => retrieve(false), ask = () => retrieve(true)
async function refreshNotifications() { const token = epoch; try { const result = await client.notifications(); if (token === epoch) notifications.value = result.notifications } catch (cause) { if (token === epoch) fail(cause) } }
async function markRead(id) { const token = epoch; try { await client.markRead(id); if (token === epoch) await refreshNotifications() } catch (cause) { if (token === epoch) fail(cause) } }
async function download() { const token = epoch, current = selection, asset = selected.value, document = versionDocument.value; if (!asset || !document) return; try { const blob = await client.download(asset.id, document.version); if (token !== epoch || current !== selection) return; const url = URL.createObjectURL(blob), link = window.document.createElement('a'); link.href = url; link.download = document.filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) } catch (cause) { if (token === epoch) fail(cause) } }
watch(() => [store.workspaceApiKey, store.mode], () => { epoch++; selection++; versionSelection++; comparing++; versionDocument.value = null; versions.value = []; comparison.value = null; reportRunId.value = ''; busy.value = false; assets.value = []; selected.value = null; obligations.value = []; notifications.value = []; importResults.value = []; members.value = []; me.value = null; citations.value = []; answer.value = ''; error.value = ''; query.value = ''; tag.value = ''; question.value = ''; Object.assign(editing, { title: '', tags: '', visibility: 'private', sharedWith: [] }); obligation.title = ''; obligation.dueAt = ''; refresh() })
onMounted(refresh)
</script>

<style scoped>
.version-range{display:flex;gap:16px}.version-range label{flex:1}.hash{overflow-wrap:anywhere}
.panel{padding:24px}.import-panel{margin-bottom:20px}.asset-layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:20px}.section{margin-top:24px;padding-top:20px;border-top:1px solid var(--line)}h2{font-size:18px;margin:0 0 14px}h3{font-size:15px}p,small{color:var(--ink-3);line-height:1.6}label{display:flex;flex-direction:column;gap:7px;margin:12px 0;font-size:13px}input,select{font:inherit;width:100%;min-width:0;box-sizing:border-box;padding:9px;border:1px solid var(--line);border-radius:5px;background:#fff}select[multiple]{min-height:85px}.asset-row{display:flex;flex-direction:column;gap:8px;width:100%;padding:16px 8px;text-align:left;border-bottom:1px solid var(--line);cursor:pointer}.asset-row span{font-size:12px;color:var(--ink-3)}.asset-row.selected{background:#edf3f8}.source{border-bottom:1px solid var(--line);padding:12px 0}.source p,.answer,pre{white-space:pre-wrap;overflow-wrap:anywhere}pre{max-height:280px;overflow:auto;font-size:12px}.search{display:flex;gap:10px;align-items:center}.search input{flex:1}.search select{max-width:200px}.obligation{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 0;font-size:13px}.citation{color:var(--el-color-primary);text-align:left;cursor:pointer}@media(max-width:800px){.asset-layout{grid-template-columns:1fr}.search{flex-wrap:wrap}.search input{flex-basis:100%}}
</style>
