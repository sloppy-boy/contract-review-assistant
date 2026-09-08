<template>
  <section class="collaboration">
    <el-alert v-if="store.mode === 'offline'" type="info" :closable="false" title="协作事项使用工作区数据，请切换到在线审查后操作。" />
    <template v-else>
      <div class="toolbar">
        <p>从审查发起到审批留痕 <span v-if="me">· 当前身份 {{ me.subject }}</span></p>
        <el-button :loading="loading" @click="loadAll">刷新工作区</el-button>
      </div>
      <el-alert v-if="error" type="error" :title="error" closable @close="error = ''" />
      <div class="workspace" v-loading="loading">
        <aside class="panel sidebar">
          <div class="section-heading"><h2>审查事项</h2><span>{{ requests.length }} 项</span></div>
          <label class="field">筛选状态<select v-model="filter" @change="loadList"><option value="">全部状态</option><option v-for="(label, value) in statuses" :key="value" :value="value">{{ label }}</option></select></label>
          <div class="request-list">
            <button v-for="item in requests" :key="item.id" class="request-row" :class="{ selected: selected?.id === item.id }" @click="selectRequest(item.id)">
              <strong>{{ item.title }}</strong><span>{{ statuses[item.status] }} · {{ item.assignee || '未指派' }}</span>
              <small :class="{ overdue: item.isOverdue }">{{ item.isOverdue ? '已超期 · ' : '' }}{{ item.dueAt ? time(item.dueAt) : '尚未设定截止时间' }}</small>
            </button>
            <p v-if="!requests.length" class="muted">暂无事项，可在下方创建发起单。</p>
          </div>
          <details v-if="isWriter" open class="create-section">
            <summary>新建审查发起单</summary>
            <form @submit.prevent="createRequest">
              <label class="field">事项标题<input v-model="draft.title" data-testid="request-title" required maxlength="200" placeholder="例如：供应商采购合同审查"></label>
              <label class="field">合同类型<select v-model="draft.contractType"><option value="purchase">采购合同</option><option value="sale">销售合同</option></select></label>
              <label class="field">业务说明<textarea v-model="draft.description" rows="3" maxlength="10000" placeholder="说明审查背景、关注事项"></textarea></label>
              <el-button native-type="submit" type="primary" :disabled="busy" data-testid="create-request">创建发起单</el-button>
            </form>
          </details>
        </aside>

        <article class="panel detail" v-if="selected">
          <div class="section-heading"><h2>{{ selected.title }}</h2><el-tag data-testid="request-status" :type="selected.status === 'approved' ? 'success' : 'info'">{{ statuses[selected.status] }}</el-tag></div>
          <p class="muted">{{ selected.contractType === 'sale' ? '销售合同' : '采购合同' }} · 发起人 {{ selected.createdBy }} · {{ time(selected.createdAt) }}</p>
          <p class="description">{{ selected.description || '未填写业务说明' }}</p>
          <div class="facts"><span>处理人 <b>{{ selected.assignee || '待指派' }}</b></span><span>审批人 <b>{{ selected.approver || '待指派' }}</b></span><span :class="{ overdue: selected.isOverdue }">截止时间 <b>{{ time(selected.dueAt) }}</b>{{ selected.isOverdue ? ' · 已超期' : '' }}</span></div>
          <el-button v-if="canOwn && selected.status === 'draft'" data-testid="submit-request" type="primary" :disabled="busy" @click="mutate(() => client.submit(selected.id, selected.revision))">提交审查发起单</el-button>

          <form v-if="canOwn && ['submitted', 'in_review', 'changes_requested'].includes(selected.status)" class="action-block" @submit.prevent="assign">
            <h3>指派与截止时间</h3>
            <div class="inline-form"><label class="field grow">SLA 工作分钟<input v-model.number="slaMinutes" type="number" min="1" max="525600"></label><el-button :disabled="busy || !slaMinutes" @click="calculateSla">按中国工作日历计算</el-button></div>
            <p class="muted">默认周一至周五 09:00–18:00；API 可指定租户时区、工作日和节假日。</p>
            <div class="form-grid">
              <label class="field">处理人<select v-model="assignment.assignee" data-testid="assignee" required><option value="" disabled>选择处理人</option><option v-for="member in reviewers" :key="member.subject" :value="member.subject">{{ member.subject }}</option></select></label>
              <label class="field">审批人<select v-model="assignment.approver" data-testid="approver" required><option value="" disabled>选择审批人</option><option v-for="member in reviewers" :key="member.subject" :value="member.subject">{{ member.subject }}</option></select></label>
              <label class="field">截止时间（本地时间）<input v-model="assignment.dueAt" data-testid="due-at" type="datetime-local" required></label>
            </div>
            <el-button native-type="submit" :disabled="busy" data-testid="assign-request">保存指派</el-button>
          </form>

          <div v-if="canReview && ['in_review', 'changes_requested'].includes(selected.status)" class="action-block">
            <h3>关联审查报告</h3>
            <p class="muted">先在工作台完成合同审查，再从这里关联相同合同类型的报告。</p>
            <form class="inline-form" @submit.prevent="mutate(() => client.bindRun(selected.id, { expectedRevision: selected.revision, runId }))">
              <label class="field grow">已完成的报告<select v-model="runId" required data-testid="run-id"><option value="" disabled>选择报告</option><option v-for="run in matchingRuns" :key="run.id" :value="run.id">{{ run.contract?.name || run.id }} · {{ time(run.createdAt) }}</option></select></label>
              <el-button native-type="submit" :disabled="busy" data-testid="bind-run">关联报告</el-button>
            </form>
            <p v-if="selected.linkedRunId" class="muted">已关联审查运行：{{ selected.linkedRunId }}</p>
            <el-button :disabled="busy || !selected.linkedRunId" type="primary" data-testid="request-approval" @click="mutate(() => client.requestApproval(selected.id, selected.revision))">提交审批并固定报告</el-button>
          </div>

          <details v-if="selected.approvalSnapshot" class="action-block">
            <summary>第 {{ selected.approvalRound }} 轮审批依据 · 已固定</summary>
            <p class="muted">审查报告与人工处置保留为本轮快照，后续报告修改不会改写这里的依据。</p>
            <p v-if="selected.approvalSnapshot.report.meta?.playbookApplication === 'snapshot_only'" class="muted">Playbook 仅绑定快照，自定义规则尚未参与自动判断。</p>
            <div v-for="risk in selected.approvalSnapshot.report.risks || []" :key="risk.id" class="snapshot-risk">
              <strong>{{ risk.riskType || risk.id }}</strong><span> · {{ severity[risk.severity] || risk.severity }}</span>
              <p>{{ risk.clauseQuote }}</p><p>说明：{{ risk.evidence || '暂无' }}</p><p>建议：{{ risk.suggestionClauseText || risk.suggestion || '暂无' }}</p>
              <p>人工处置：{{ selected.approvalSnapshot.dispositions[risk.id]?.decision || '未处置' }}</p>
              <p>处置理由：{{ selected.approvalSnapshot.dispositions[risk.id]?.reason || '未填写' }}</p>
              <p>法律依据：{{ risk.legalBasis || '未提供' }}</p><p v-if="risk.disputed">此风险存在争议，请结合完整证据复核。</p>
            </div>
            <p v-if="!selected.approvalSnapshot.report.risks?.length">此报告没有风险条目，仍需由审批人确认审查结论。</p>
            <details><summary>查看完整冻结证据（含 Playbook 版本与内容摘要）</summary><pre class="frozen-evidence">{{ JSON.stringify(selected.approvalSnapshot, null, 2) }}</pre></details>
          </details>

          <form v-if="canApprove && selected.status === 'pending_approval'" class="action-block" @submit.prevent="decide('approved')">
            <h3>审批决定</h3>
            <label class="field">审批理由<textarea v-model="decisionReason" data-testid="decision-reason" required rows="2" maxlength="10000" placeholder="说明批准或退回的理由"></textarea></label>
            <el-button native-type="submit" type="success" :disabled="busy" data-testid="approve-request">批准</el-button>
            <el-button :disabled="busy || !decisionReason.trim()" @click="decide('changes_requested')">退回补充</el-button>
          </form>

          <section class="action-block">
            <h3>协作时间线</h3>
            <ol class="timeline"><li v-for="event in events" :key="event.id"><div><strong>{{ eventNames[event.type] || event.type }}</strong> · {{ event.subject }} <time>{{ time(event.createdAt) }}</time></div><p v-if="event.data.body">{{ event.data.body }}</p><p v-if="event.data.mentions?.length" class="muted">提及：{{ event.data.mentions.join('、') }}</p><p v-if="event.data.reason">{{ event.data.reason }}</p><p v-if="event.type === 'assigned'" class="muted">处理人 {{ event.data.assignee }} · 审批人 {{ event.data.approver }}</p></li></ol>
            <form v-if="isWriter" @submit.prevent="addComment">
              <label class="field">评论<textarea v-model="commentBody" data-testid="comment-body" rows="2" required maxlength="10000" placeholder="补充说明、提出问题或记录协商结果"></textarea></label>
              <label class="field">@提及成员（可多选）<select v-model="mentions" multiple class="mention-select"><option v-for="member in members" :key="member.subject" :value="member.subject">{{ member.subject }}</option></select></label>
              <el-button native-type="submit" :disabled="busy" data-testid="add-comment">发送评论</el-button>
            </form>
          </section>
          <details v-if="canOwn && !['approved', 'cancelled'].includes(selected.status)" class="cancel-section"><summary>取消此事项</summary><form @submit.prevent="mutate(() => client.cancel(selected.id, { expectedRevision: selected.revision, reason: cancelReason }))"><label class="field">取消原因<input v-model="cancelReason" required maxlength="10000"></label><el-button native-type="submit" type="danger" plain :disabled="busy">确认取消</el-button></form></details>
        </article>
        <div v-else class="panel empty"><el-empty description="选择一项审查事项，或创建新的发起单" /></div>
      </div>

      <section class="panel inbox">
        <div class="section-heading"><h2>我的站内通知</h2><span>{{ notifications.filter(item => !item.readAt).length }} 条未读</span></div>
        <p v-if="!notifications.length" class="muted">暂无通知。指派、审批和评论提及将出现在这里。</p>
        <div v-for="notification in notifications" :key="notification.id" class="notification" :class="{ unread: !notification.readAt }"><div><button class="link" @click="selectRequest(notification.requestId)">{{ notification.title }}</button><p>{{ eventNames[notification.type] || notification.type }} · {{ notification.subject }} · {{ time(notification.createdAt) }}</p></div><el-button v-if="!notification.readAt" size="small" :disabled="busy" @click="readNotification(notification.id)">标为已读</el-button><span v-else class="muted">已读</span></div>
      </section>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { apiFetch, fetchReviewHistory, store } from '../api.js'
import { createCollaborationClient } from '../collaboration-api.js'

const client = createCollaborationClient(apiFetch)
const statuses = { draft: '草稿', submitted: '待指派', in_review: '审查中', pending_approval: '待审批', changes_requested: '待补充', approved: '已批准', cancelled: '已取消' }
const eventNames = { created: '创建事项', submitted: '提交审查', assigned: '指派更新', run_bound: '关联报告', approval_requested: '提交审批', approved: '审批通过', changes_requested: '退回补充', cancelled: '取消事项', commented: '发表评论', sla_pre_due: 'SLA 即将到期', sla_due: 'SLA 已到期', sla_overdue_escalation: 'SLA 逾期升级' }
const severity = { high: '高风险', medium: '中风险', low: '低风险' }
const me = ref(null), members = ref([]), requests = ref([]), notifications = ref([]), completedRuns = ref([])
const selected = ref(null), events = ref([]), filter = ref(''), loading = ref(false), busy = ref(false), error = ref('')
const draft = reactive({ title: '', contractType: 'purchase', description: '' })
const assignment = reactive({ assignee: '', approver: '', dueAt: '' })
const runId = ref(''), decisionReason = ref(''), cancelReason = ref(''), commentBody = ref(''), mentions = ref([]), slaMinutes = ref(480)
let selectionToken = 0, workspaceToken = 0, listToken = 0, loadToken = 0
const isWriter = computed(() => ['admin', 'legal_reviewer', 'requester'].includes(me.value?.role))
const isReviewer = computed(() => ['admin', 'legal_reviewer'].includes(me.value?.role))
const canOwn = computed(() => isWriter.value && (me.value?.role === 'admin' || selected.value?.createdBy === me.value?.subject))
const canReview = computed(() => isReviewer.value && (me.value?.role === 'admin' || selected.value?.assignee === me.value?.subject))
const canApprove = computed(() => isReviewer.value && selected.value?.approver === me.value?.subject)
const reviewers = computed(() => members.value.filter(member => ['admin', 'legal_reviewer'].includes(member.role)))
const matchingRuns = computed(() => completedRuns.value.filter(run => run.contract_type === selected.value?.contractType))
const time = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未设定'
function localDate(value) { const date = new Date(value); date.setMinutes(date.getMinutes() - date.getTimezoneOffset()); return date.toISOString().slice(0, 16) }
function showError(cause) { error.value = cause.message || '操作失败'; ElMessage.error(error.value) }
async function loadList() { const token = ++listToken, workspace = workspaceToken; try { const result = await client.list(filter.value); if (workspace === workspaceToken && token === listToken) requests.value = result.requests } catch (cause) { if (workspace === workspaceToken && token === listToken) showError(cause) } }
async function loadAll() {
  if (store.mode === 'offline') return
  const workspace = workspaceToken, token = ++loadToken, list = ++listToken
  loading.value = true; error.value = ''
  try {
    const [identity, directory, result, inbox, runs] = await Promise.all([client.me(), client.members(), client.list(filter.value), client.notifications(), fetchReviewHistory('done')])
    if (workspace !== workspaceToken || token !== loadToken) return
    if (list === listToken) requests.value = result.requests
    me.value = identity; members.value = directory.members; notifications.value = inbox.notifications; completedRuns.value = runs
    if (selected.value) await selectRequest(selected.value.id)
  } catch (cause) { if (workspace === workspaceToken && token === loadToken) showError(cause) } finally { if (workspace === workspaceToken && token === loadToken) loading.value = false }
}
async function selectRequest(id) {
  const token = ++selectionToken
  try {
    const [request, timeline] = await Promise.all([client.get(id), client.events(id)])
    if (token !== selectionToken) return
    const changed = selected.value?.id !== id
    selected.value = request; events.value = timeline.events
    assignment.assignee = request.assignee || ''; assignment.approver = request.approver || ''
    assignment.dueAt = localDate(request.dueAt || Date.now() + 3 * 86400000)
    runId.value = request.linkedRunId || ''
    if (changed) { decisionReason.value = ''; cancelReason.value = ''; commentBody.value = ''; mentions.value = [] }
  } catch (cause) { if (token === selectionToken) showError(cause) }
}
async function mutate(operation) {
  if (busy.value || store.mode === 'offline') return false
  const workspace = workspaceToken, selection = selectionToken
  busy.value = true; error.value = ''
  try {
    const request = await operation()
    if (workspace !== workspaceToken) return false
    const stayed = selection === selectionToken
    await Promise.all([loadList(), stayed ? selectRequest(request.id) : Promise.resolve()])
    const refreshedSelection = selectionToken
    const inbox = await client.notifications()
    if (workspace !== workspaceToken) return false
    notifications.value = inbox.notifications
    return stayed && refreshedSelection === selectionToken && selected.value?.id === request.id
  } catch (cause) {
    if (workspace !== workspaceToken) return false
    showError(cause)
    if (cause.status === 409 && selection === selectionToken && selected.value) await selectRequest(selected.value.id)
    return false
  } finally { if (workspace === workspaceToken) busy.value = false }
}
async function createRequest() { if (await mutate(() => client.create({ ...draft }))) { draft.title = ''; draft.description = '' } }
async function assign() {
  const dueAt = new Date(assignment.dueAt)
  if (!Number.isFinite(dueAt.getTime()) || dueAt <= new Date()) { showError(new Error('请选择未来的截止时间')); return }
  await mutate(() => client.assign(selected.value.id, { expectedRevision: selected.value.revision, assignee: assignment.assignee, approver: assignment.approver, dueAt: dueAt.toISOString() }))
}
async function calculateSla() {
  try { const result = await client.calculateSla({ startedAt: new Date().toISOString(), businessMinutes: slaMinutes.value }); assignment.dueAt = localDate(result.dueAt) }
  catch (cause) { showError(cause) }
}
async function decide(decision) { if (await mutate(() => client.decide(selected.value.id, { expectedRevision: selected.value.revision, decision, reason: decisionReason.value }))) decisionReason.value = '' }
async function addComment() { if (await mutate(() => client.comment(selected.value.id, { expectedRevision: selected.value.revision, body: commentBody.value, mentions: [...mentions.value] }))) { commentBody.value = ''; mentions.value = [] } }
async function readNotification(id) { const workspace = workspaceToken; try { await client.readNotification(id); if (workspace !== workspaceToken) return; const inbox = await client.notifications(); if (workspace === workspaceToken) notifications.value = inbox.notifications } catch (cause) { if (workspace === workspaceToken) showError(cause) } }
watch(() => [store.workspaceApiKey, store.mode], () => { workspaceToken++; selectionToken++; busy.value = false; loading.value = false; selected.value = null; events.value = []; me.value = null; members.value = []; requests.value = []; notifications.value = []; completedRuns.value = []; loadAll() })
onMounted(loadAll)
</script>

<style scoped>
.frozen-evidence{white-space:pre-wrap;overflow-wrap:anywhere;max-height:480px;overflow:auto;font-size:12px}
.toolbar,.section-heading,.facts,.notification,.inline-form{display:flex;align-items:center;justify-content:space-between;gap:12px}.toolbar{margin-bottom:16px}.toolbar p,.muted{color:var(--ink-3);font-size:13px}.workspace{display:grid;grid-template-columns:minmax(260px,340px) minmax(0,1fr);gap:20px;margin-top:16px}.sidebar,.detail,.inbox{padding:22px}.section-heading h2{font-size:18px;margin:0}.section-heading>span{font-size:12px;color:var(--ink-3)}.field{display:flex;flex-direction:column;gap:7px;margin:14px 0;font-size:13px;color:var(--ink-2)}input,select,textarea{font:inherit;border:1px solid var(--line);border-radius:5px;padding:9px;background:white;color:var(--ink);min-width:0;width:100%;box-sizing:border-box}textarea{resize:vertical}input:focus,select:focus,textarea:focus{outline:2px solid #93b5da;outline-offset:1px}.request-list{max-height:380px;overflow:auto;margin:14px 0}.request-row{display:flex;flex-direction:column;gap:7px;text-align:left;padding:13px 10px;width:100%;border-bottom:1px solid var(--line);cursor:pointer}.request-row.selected{background:#edf3f8;border-left:3px solid var(--el-color-primary)}.request-row span,.request-row small{font-size:12px;color:var(--ink-3)}.request-row strong{overflow-wrap:anywhere}.create-section,.action-block{border-top:1px solid var(--line);margin-top:22px;padding-top:18px}summary{font-weight:600;cursor:pointer;font-size:14px}.description{white-space:pre-wrap;color:var(--ink-2);line-height:1.6}.facts{align-items:start;flex-wrap:wrap;padding:15px 0;margin-bottom:12px;font-size:12px}.facts span{display:flex;flex-direction:column;gap:7px}.facts b{font-size:13px}.overdue,.request-row small.overdue{color:#b45309}.action-block h3{font-size:15px;margin:0}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 15px}.form-grid .field:last-child{grid-column:1/-1}.grow{flex:1}.inline-form{align-items:center}.timeline{list-style:none;padding:0 0 0 13px;border-left:2px solid var(--line);font-size:13px;line-height:1.6}.timeline li{margin:18px 0}.timeline time{display:block;font-size:11px;color:var(--ink-3)}.timeline p{white-space:pre-wrap;overflow-wrap:anywhere;margin:5px 0}.mention-select{height:86px}.cancel-section{margin-top:24px;color:var(--ink-3)}.inbox{margin-top:20px}.notification{padding:14px 0;border-bottom:1px solid var(--line);font-size:13px}.notification p{margin:5px 0 0;color:var(--ink-3);font-size:12px}.notification.unread .link{font-weight:600}.link{color:var(--el-color-primary);cursor:pointer;text-align:left}.snapshot-risk{padding:12px 0;border-bottom:1px solid var(--line);font-size:13px}.snapshot-risk p{white-space:pre-wrap}.empty{display:flex;align-items:center;justify-content:center;min-height:400px}.detail{min-width:0}button:focus-visible{outline:2px solid #93b5da}@media(max-width:850px){.workspace{grid-template-columns:1fr}.request-list{max-height:240px}.form-grid{grid-template-columns:1fr}.toolbar{align-items:start}.inline-form{flex-wrap:wrap}}
</style>
