<template>
  <section class="playbooks">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div class="panel toolbar"><h2>Playbook 工作台</h2><el-button v-if="canEdit" type="primary" @click="create">新建 Playbook</el-button></div>
    <div class="grid">
      <aside class="panel">
        <button v-for="book in books" :key="book.playbookId" class="book" @click="select(book)">{{ book.content?.name || book.playbookId }}<small>{{ book.content?.contractType }} · v{{ book.version }} · {{ book.status }}</small></button>
        <p v-if="!books.length">暂无 Playbook。</p>
      </aside>
      <article v-if="selected" class="panel">
        <h2>{{ selected.content.name }}</h2>
        <p>{{ selected.content.contractType }} · {{ selected.content.jurisdiction }} · v{{ selected.version }} · {{ selected.status }}</p>
        <div v-for="rule in selected.content.rules" :key="rule.id" class="rule"><strong>{{ rule.riskType }} · {{ rule.severity }}</strong><p>{{ rule.reviewQuestion }}</p><small>触发：{{ rule.triggerCondition }}</small></div>
        <div v-if="events.length" class="events"><h3>版本事件</h3><div v-for="event in events" :key="`${event.type}-${event.createdAt}`" class="event"><b>{{ event.type }}</b><span>{{ event.subject }} · {{ event.createdAt }}</span></div></div>
        <div v-if="canEdit" class="actions"><el-button v-if="selected.status === 'draft'" @click="edit">编辑草稿</el-button><el-button @click="derive">派生新版本</el-button><el-button v-if="selected.status === 'draft'" type="success" @click="publish">发布</el-button><el-button v-if="selected.status === 'active'" type="warning" @click="archive">归档</el-button></div>
      </article>
      <div v-else class="panel"><el-empty description="选择 Playbook 查看版本和规则" /></div>
    </div>
    <el-dialog v-model="formOpen" :title="editingVersion ? `编辑 Playbook 草稿 v${editingVersion}` : '新建 Playbook'" width="min(840px, 92vw)">
      <form class="editor" @submit.prevent="save">
        <label>名称<input v-model="form.name.value" maxlength="200" required></label>
        <div class="form-grid"><label>合同类型<input v-model="form.contractType.value" maxlength="100" required></label><label>法域<input v-model="form.jurisdiction.value" maxlength="100" required></label><label>业务场景<input v-model="form.businessScenario.value" maxlength="100" required></label><label>生效范围（逗号分隔）<input v-model="form.effectiveScope.value" maxlength="500" required></label></div>
        <div class="structured-editor"><div class="structured-head"><b>结构化规则</b><el-button size="small" @click.prevent="addRule">新增规则</el-button></div><div v-for="(rule, index) in parsedRules" :key="index" class="rule-card"><div class="rule-grid"><label>规则 ID<input :value="rule.id" @input="updateRule(index, 'id', $event.target.value)" required></label><label>风险类型<input :value="rule.riskType" @input="updateRule(index, 'riskType', $event.target.value)" required></label><label>严重度<select :value="rule.severity" @change="updateRule(index, 'severity', $event.target.value)"><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label><label>类别<input :value="rule.categoryId" @input="updateRule(index, 'categoryId', $event.target.value)"></label></div><label>触发条件<input :value="rule.triggerCondition" @input="updateRule(index, 'triggerCondition', $event.target.value)" required></label><label>审查问题<input :value="rule.reviewQuestion" @input="updateRule(index, 'reviewQuestion', $event.target.value)" required></label><label>可接受条件<input :value="rule.acceptableCondition" @input="updateRule(index, 'acceptableCondition', $event.target.value)" required></label><label>建议替代条款<textarea :value="rule.suggestedClause" @input="updateRule(index, 'suggestedClause', $event.target.value)" rows="2" required></textarea></label><label>升级策略<input :value="rule.escalationPolicy" @input="updateRule(index, 'escalationPolicy', $event.target.value)" required></label><el-button text type="danger" @click.prevent="removeRule(index)">删除这条规则</el-button></div><p v-if="!parsedRules.length" class="muted">尚未添加规则，草稿可以为空，但发布前至少需要一条。</p></div>
        <details><summary>高级 JSON 编辑</summary><textarea v-model="form.rules.value" rows="8" required spellcheck="false"></textarea></details>
        <div class="dialog-actions"><el-button @click="formOpen = false">取消</el-button><el-button type="primary" native-type="submit" :loading="saving">保存草稿</el-button></div>
      </form>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { apiFetch } from '../api.js'
import { createPlaybookClient } from '../playbook-api.js'
import { createCollaborationClient } from '../collaboration-api.js'

const client = createPlaybookClient(apiFetch)
const identity = createCollaborationClient(apiFetch)
const books = ref([]), selected = ref(null), events = ref([]), error = ref(''), me = ref(null), formOpen = ref(false), saving = ref(false), editingVersion = ref(null)
const form = { name: ref(''), contractType: ref('purchase'), jurisdiction: ref('CN'), businessScenario: ref('general'), effectiveScope: ref('*'), rules: ref('[]') }
const canEdit = computed(() => ['admin', 'legal_reviewer'].includes(me.value?.role))
const parsedRules = computed(() => { try { const parsed = JSON.parse(form.rules.value || '[]'); return Array.isArray(parsed) ? parsed : [] } catch { return [] } })
const blankRule = () => ({ id: `rule-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`, riskType: '', severity: 'medium', triggerCondition: '', reviewQuestion: '', acceptableCondition: '', suggestedClause: '', escalationPolicy: '', categoryId: '' })
function writeRules(rules) { form.rules.value = JSON.stringify(rules, null, 2) }
function addRule() { writeRules([...parsedRules.value, blankRule()]) }
function updateRule(index, field, value) { const rules = parsedRules.value.map(rule => ({ ...rule })); if (!rules[index]) return; rules[index][field] = value; writeRules(rules) }
function removeRule(index) { writeRules(parsedRules.value.filter((_, item) => item !== index)) }
const resetForm = () => { form.name.value = ''; form.contractType.value = 'purchase'; form.jurisdiction.value = 'CN'; form.businessScenario.value = 'general'; form.effectiveScope.value = '*'; form.rules.value = '[]'; events.value = [] }
const contentOf = content => { form.name.value = content.name || ''; form.contractType.value = content.contractType || 'purchase'; form.jurisdiction.value = content.jurisdiction || 'CN'; form.businessScenario.value = content.businessScenario || 'general'; form.effectiveScope.value = (content.effectiveScope || ['*']).join(', '); form.rules.value = JSON.stringify(content.rules || [], null, 2) }
async function refresh() { try { books.value = (await client.list()).playbooks } catch (e) { error.value = e.message } }
async function select(book) { try { const [versions, history] = await Promise.all([client.versions(book.playbookId), client.events ? client.events(book.playbookId) : Promise.resolve({ events: [] })]); selected.value = versions.versions[0]; events.value = history.events || [] } catch (e) { error.value = e.message; events.value = [] } }
function create() { editingVersion.value = null; resetForm(); error.value = ''; formOpen.value = true }
function edit() { if (!selected.value || selected.value.status !== 'draft') return; editingVersion.value = selected.value.version; contentOf(selected.value.content); error.value = ''; formOpen.value = true }
async function save() { let rules; try { rules = JSON.parse(form.rules.value); if (!Array.isArray(rules)) throw new Error('规则必须是 JSON 数组') } catch (e) { error.value = `规则 JSON 无效：${e.message}`; return }; const payload = { name: form.name.value, contractType: form.contractType.value, jurisdiction: form.jurisdiction.value, businessScenario: form.businessScenario.value, effectiveScope: form.effectiveScope.value.split(/[,，]/).map(item => item.trim()).filter(Boolean), rules }; saving.value = true; error.value = ''; try { selected.value = editingVersion.value ? await client.update(selected.value.playbookId, editingVersion.value, payload) : await client.create(payload); formOpen.value = false; await refresh() } catch (e) { error.value = e.message } finally { saving.value = false } }
async function derive() { try { selected.value = await client.derive(selected.value.playbookId, selected.value.version); await refresh() } catch (e) { error.value = e.message } }
async function publish() { try { selected.value = await client.publish(selected.value.playbookId, selected.value.version); await refresh() } catch (e) { error.value = e.message } }
async function archive() { try { selected.value = await client.archive(selected.value.playbookId, selected.value.version); await refresh() } catch (e) { error.value = e.message } }
onMounted(async () => { try { me.value = await identity.me() } catch (e) { error.value = e.message }; await refresh() })
</script>

<style scoped>.toolbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.grid{display:grid;grid-template-columns:300px minmax(0,1fr);gap:20px}.book{display:flex;flex-direction:column;gap:6px;width:100%;padding:15px 8px;text-align:left;border-bottom:1px solid var(--line);cursor:pointer}.book small{color:var(--ink-3)}.rule{border-top:1px solid var(--line);padding:14px 0}.rule p{color:var(--ink-2)}.events{margin-top:24px;border-top:1px solid var(--line);padding-top:14px}.events h3{margin:0 0 8px}.event{display:flex;gap:12px;font-size:12px;padding:4px 0}.event span,.muted{color:var(--ink-3)}.actions,.dialog-actions{margin-top:20px;display:flex;gap:8px}.editor{display:flex;flex-direction:column;gap:12px}.editor label{display:flex;flex-direction:column;gap:6px;font-size:13px}.editor input,.editor textarea,.editor select{font:inherit;border:1px solid var(--line);border-radius:5px;padding:9px;box-sizing:border-box;width:100%}.form-grid,.rule-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.structured-editor{display:flex;flex-direction:column;gap:12px}.structured-head{display:flex;justify-content:space-between;align-items:center}.rule-card{border:1px solid var(--line);border-radius:6px;padding:12px;display:flex;flex-direction:column;gap:10px}.dialog-actions{justify-content:flex-end}@media(max-width:800px){.grid{grid-template-columns:1fr}.form-grid,.rule-grid{grid-template-columns:1fr}}</style>
