import { readFileSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { computed, reactive, ref } from 'vue'

function page(overrides = {}) {
  const source = readFileSync(new URL('../src/views/PlaybooksView.vue', import.meta.url), 'utf8').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const calls = []
  const client = {
    list: async () => ({ playbooks: [] }), versions: async () => ({ versions: [] }),
    create: async body => { calls.push(['create', body]); return { playbookId: 'p1', version: 1, status: 'draft', content: body } },
    update: async (...args) => { calls.push(['update', ...args]); return { playbookId: 'p1', version: 1, status: 'draft', content: args[2] } },
    derive: async () => ({}), publish: async () => ({}), archive: async () => ({}), events: async () => ({ events: [] }), ...overrides,
  }
  const setup = new Function('computed', 'reactive', 'ref', 'onMounted', 'defineEmits', 'apiFetch', 'createPlaybookClient', 'createCollaborationClient', source + '\nreturn { create, save, form, selected, editingVersion, addRule, updateRule, removeRule, parsedRules }')
  const view = setup(computed, reactive, ref, () => {}, () => () => {}, () => {}, () => client, () => ({ me: async () => ({ role: 'admin' }) }))
  return { ...view, calls }
}

test('Playbook 工作台可创建完整草稿', async () => {
  const view = page()
  view.create()
  view.form.name.value = '采购标准'
  view.form.rules.value = JSON.stringify([{ id: 'r1', riskType: '付款', severity: 'medium', triggerCondition: '付款', reviewQuestion: '是否明确？', acceptableCondition: '明确', suggestedClause: '应明确', escalationPolicy: '法务确认' }])
  await view.save()
  assert.equal(view.calls[0][0], 'create')
  assert.equal(view.calls[0][1].name, '采购标准')
  assert.equal(view.calls[0][1].rules[0].id, 'r1')
})

test('Playbook 工作台编辑草稿使用版本和内容更新接口', async () => {
  const view = page()
  view.selected.value = { playbookId: 'p1', version: 2, status: 'draft', content: { name: '旧', contractType: 'purchase', jurisdiction: 'CN', businessScenario: 'general', effectiveScope: ['*'], rules: [] } }
  view.editingVersion.value = 2
  view.form.name.value = '新'
  await view.save()
  assert.deepEqual(view.calls[0], ['update', 'p1', 2, { name: '新', contractType: 'purchase', jurisdiction: 'CN', businessScenario: 'general', effectiveScope: ['*'], rules: [] }])
})

test('规则编辑器支持新增、修改和删除结构化规则', () => {
  const view = page()
  view.create()
  view.addRule()
  assert.equal(view.parsedRules.value.length, 1)
  view.updateRule(0, 'riskType', '付款风险')
  view.updateRule(0, 'severity', 'high')
  assert.equal(view.parsedRules.value[0].riskType, '付款风险')
  assert.equal(view.parsedRules.value[0].severity, 'high')
  view.removeRule(0)
  assert.equal(view.parsedRules.value.length, 0)
})
