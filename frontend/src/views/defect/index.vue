<template>
  <section class="page" data-module="defect">
    <header class="page-head">
      <div>
        <h2>缺陷登记管理</h2>
        <p class="page-desc">定级阈值在后端统一维护：列表与详情给出同一份判定；缺等级或缺计划消除日的缺陷无法确认定级。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记机组缺陷</button>
        <button class="btn" type="button" @click="exportRows">导出缺陷登记清单</button>
      </div>
    </header>

    <!-- 待办计数：每次刷新/操作后按后端重算结果回填，不写死 -->
    <div class="stat-row">
      <article
        v-for="card in statCards"
        :key="card.key"
        class="stat-card"
        :class="{ clickable: card.key !== '待办', active: activeTab === card.key }"
        @click="card.key !== '待办' && switchTab(card.key)"
      >
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value" :class="card.cls">{{ card.value }}</strong>
      </article>
    </div>

    <!-- 批量定级：可中断、可续跑，断在第几条直接展示 -->
    <div class="job-panel">
      <div class="job-head">
        <strong>批量定级</strong>
        <button class="btn primary" type="button" :disabled="busy" @click="startJob">发起批量定级</button>
      </div>
      <p v-if="!job" class="job-empty">尚未发起任务。任务按缺陷编号逐条定级，遇到缺等级/缺日期/超阈值的缺陷会停下，补录后可接着走。</p>
      <div v-else class="job-body">
        <div class="job-line">
          <span class="tag" :class="`tag-${jobStatusKey(job.status)}`">{{ job.status }}</span>
          <span>第 {{ job.position }} / {{ job.total }} 条 · 已定级 {{ job.processed }} 条</span>
        </div>
        <p v-if="job.中断说明" class="job-break">{{ job.中断说明 }}</p>
        <template v-if="job.status === '已中断'">
          <div class="job-actions">
            <button class="btn primary" type="button" :disabled="busy" @click="resumeJob">从断点继续</button>
            <button class="btn" type="button" :disabled="busy" @click="openFill(job.current_entry_id)">补录该缺陷</button>
          </div>
          <p class="job-hint">续跑前请先补录卡控项；未补录时继续仍会断在同一条。</p>
        </template>
        <ul v-if="job.items.length" class="job-items">
          <li v-for="item in job.items" :key="`${item.序号}-${item.缺陷编号}`">
            <span>第 {{ item.序号 }} 条</span>
            <span>{{ item.缺陷编号 }}</span>
            <span class="tag" :class="`tag-${itemResultKey(item.结果)}`">{{ item.结果 }}</span>
            <span class="job-item-note">{{ item.说明 }}</span>
          </li>
        </ul>
      </div>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>缺陷编号</span>
        <input v-model="keyword" placeholder="按缺陷编号检索" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>定级判定（统一阈值）</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-overdue': verdict(row).超期 }">
          <td>
            <button class="link" type="button" @click="openDetail(row)">{{ row['缺陷编号'] }}</button>
          </td>
          <td>{{ row['缺陷部位'] ?? '—' }}</td>
          <td>
            <span v-if="row['缺陷等级']">{{ row['缺陷等级'] }}</span>
            <span v-else class="warn-text">缺失</span>
          </td>
          <td>{{ row['发现方式'] ?? '—' }}</td>
          <td>{{ row['发现时间'] ?? '—' }}</td>
          <td>{{ row['报告人'] ?? '—' }}</td>
          <td>
            <span v-if="row['计划消除日']">{{ row['计划消除日'] }}</span>
            <span v-else class="warn-text">缺失</span>
            <span v-if="verdict(row).超期" class="tag tag-overdue">超期</span>
          </td>
          <td>
            <span class="tag" :class="`tag-status-${row.status}`">{{ row.status }}</span>
          </td>
          <td class="verdict-cell">
            <div>{{ verdict(row).定级结论 }}</div>
            <div class="verdict-sub">
              {{ verdict(row).部位分类 }} · 最迟 {{ verdict(row).最迟消除日 ?? '—' }}
            </div>
            <div v-if="!verdict(row).定级可确认 && row.status === '待定级'" class="warn-text">
              <template v-for="(blocker, i) in verdict(row).定级卡控项" :key="i">
                {{ blocker }}<br v-if="i < verdict(row).定级卡控项.length - 1" />
              </template>
            </div>
          </td>
          <td class="row-actions">
            <template v-for="action in allowedActions(row)" :key="action">
              <button class="link" type="button" @click="runAction(action, row)">{{ action }}</button>
            </template>
            <button class="link" type="button" @click="openFill(row.id)">补录</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无符合条件的缺陷记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条缺陷登记记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 详情弹层：判定与列表同一接口同一函数 -->
    <div v-if="detail" class="modal-mask" @click.self="closeDetail">
      <div class="modal">
        <div class="modal-head">
          <strong>缺陷详情 · {{ detail['缺陷编号'] }}</strong>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </div>
        <dl class="detail-grid">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detail[field] || '—' }}</dd>
          </template>
          <dt>当前状态</dt>
          <dd><span class="tag" :class="`tag-status-${detail.status}`">{{ detail.status }}</span></dd>
        </dl>
        <h4>定级判定</h4>
        <dl class="detail-grid">
          <template v-for="line in verdictLines(detail)" :key="line[0]">
            <dt>{{ line[0] }}</dt>
            <dd :class="line[2]">{{ line[1] }}</dd>
          </template>
        </dl>
        <div v-if="detail.定级信息" class="history-box">
          <strong>最近一次定级（重复定级只保留最新）</strong>
          <p>结论：{{ detail.定级信息.定级结论 }}（{{ detail.定级信息.定级时间 }}）</p>
        </div>
      </div>
    </div>

    <!-- 补录缺陷等级 / 计划消除日 -->
    <div v-if="fill" class="modal-mask" @click.self="closeFill">
      <div class="modal">
        <div class="modal-head">
          <strong>补录 · {{ fill['缺陷编号'] }}</strong>
          <button class="btn ghost" type="button" @click="closeFill">关闭</button>
        </div>
        <form class="fill-form" @submit.prevent="submitFill">
          <label class="filter-item">
            <span>缺陷等级</span>
            <select v-model="fillForm['缺陷等级']">
              <option value="">请选择（空值不保存）</option>
              <option v-for="level in levels" :key="level" :value="level">{{ level }}</option>
            </select>
          </label>
          <label class="filter-item">
            <span>计划消除日</span>
            <input v-model="fillForm['计划消除日']" type="date" />
          </label>
          <p class="page-desc">补录后可在原行重试「确认定级」，或在批量定级里「从断点继续」。</p>
          <div class="modal-actions">
            <button class="btn primary" type="submit" :disabled="busy">保存补录</button>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Verdict = {
  定级结论: string
  部位分类: string
  标准缺陷等级: string
  处置时限天数: number | null
  最迟消除日: string | null
  计划消除日有效: boolean
  阈值达标: boolean
  超期: boolean
  定级可确认: boolean
  定级卡控项: string[]
}

type Row = Record<string, string | number | null> & {
  id: number
  status: string
  判定: Verdict
  定级信息?: { 定级结论: string; 定级时间: string }
}

type JobItem = { 序号: number; 缺陷编号: string; 结果: string; 说明: string }
type Job = {
  id: number
  status: string
  total: number
  processed: number
  cursor: number
  position: number
  中断说明: string
  items: JobItem[]
  current_entry_id?: number
}

const ENDPOINT = '/api/defect'
const columns = ['缺陷编号', '缺陷部位', '缺陷等级', '发现方式', '发现时间', '报告人', '计划消除日', '缺陷状态']
const detailFields = columns.filter((field) => field !== '缺陷状态')
const levels = ['紧急', '重大', '一般']
const tabs = [
  { key: '', label: '全部' },
  { key: '待定级', label: '待定级缺陷' },
  { key: '处置中', label: '处置中缺陷' },
  { key: 'overdue', label: '超期缺陷' },
] as const

const rows = ref<Row[]>([])
const total = ref(0)
const counts = ref<Record<string, number>>({ 待定级: 0, 处置中: 0, 超期: 0, 待办: 0 })
const errorMessage = ref('')
const busy = ref(false)
const keyword = ref('')
const activeTab = ref<(typeof tabs)[number]['key'] | ''>('')
const detail = ref<Row | null>(null)
const fill = ref<Row | null>(null)
const fillForm = ref<Record<string, string>>({})
const job = ref<Job | null>(null)

const statCards = [
  { key: '待办', label: '待办（待定级+处置中）', value: () => counts.value.待办 ?? 0, cls: '' },
  { key: '待定级', label: '待定级缺陷', value: () => counts.value.待定级 ?? 0, cls: '' },
  { key: '处置中', label: '处置中缺陷', value: () => counts.value.处置中 ?? 0, cls: '' },
  { key: 'overdue', label: '超期缺陷', value: () => counts.value.超期 ?? 0, cls: 'warn-text' },
]

function verdict(row: Row): Verdict {
  return row.判定
}

function verdictLines(row: Row): [string, string, string][] {
  const v = verdict(row)
  const lines: [string, string, string][] = [
    ['部位分类', v.部位分类, ''],
    ['标准缺陷等级', v.标准缺陷等级 || '未识别', v.标准缺陷等级 ? '' : 'warn-text'],
    ['定级结论', v.定级结论, ''],
    ['处置时限', v.处置时限天数 == null ? '—' : `${v.处置时限天数} 天`, ''],
    ['最迟消除日', v.最迟消除日 ?? '—', v.阈值达标 ? '' : 'warn-text'],
    ['是否可确认定级', v.定级可确认 ? '可以保存' : '不允许保存', v.定级可确认 ? '' : 'warn-text'],
  ]
  v.定级卡控项.forEach((blocker) => {
    lines.push(['卡控项', blocker, 'warn-text'])
  })
  if (v.超期) {
    lines.push(['超期提示', '已超过计划消除日仍在处置中', 'warn-text'])
  }
  return lines
}

function allowedActions(row: Row): string[] {
  switch (row.status) {
    case '待定级':
      return ['确认定级']
    case '已定级':
      return ['提交消除']
    case '处置中':
      return ['验收消除']
    default:
      return []
  }
}

function resetFilters() {
  keyword.value = ''
  activeTab.value = ''
  void reload()
}

function switchTab(tab: string) {
  activeTab.value = tab as typeof activeTab.value
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '机组缺陷登记入口尚未接入审批流'
}

async function readError(response: Response): Promise<string> {
  try {
    const data = await response.json()
    if (typeof data?.detail === 'string') return data.detail
    if (typeof data?.message === 'string') return data.message
  } catch {
    /* 非 JSON 响应时退回通用提示 */
  }
  return `接口返回 ${response.status}`
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  busy.value = true
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || data.ok === false) {
      // 后端已写明卡在哪个字段/哪条阈值，原样透传给使用者
      // 先刷新数据（reload 会清空提示），再把失败原因写回，避免消息被抹掉
      await Promise.all([reload(), reloadDetail()])
      errorMessage.value = data.message || '接口返回异常'
      return
    }
    await Promise.all([reload(), reloadDetail()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺陷操作失败'
  } finally {
    busy.value = false
  }
}

function buildQuery(): string {
  const params = new URLSearchParams()
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  if (activeTab.value === 'overdue') params.set('overdue', 'true')
  else if (activeTab.value) params.set('status', activeTab.value)
  const query = params.toString()
  return query ? `?${query}` : ''
}

async function reloadSummary() {
  try {
    const response = await request(`${ENDPOINT}/summary`)
    if (response.ok) counts.value = await response.json()
  } catch {
    /* 计数刷新失败不阻断列表 */
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}${buildQuery()}`)
    if (!response.ok) throw new Error(await readError(response))
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await reloadSummary()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺陷列表读取失败'
  }
}

async function openDetail(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error(await readError(response))
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺陷详情读取失败'
  }
}

function closeDetail() {
  detail.value = null
}

async function reloadDetail() {
  if (detail.value) {
    const response = await request(`${ENDPOINT}/${detail.value.id}`)
    if (response.ok) detail.value = await response.json()
  }
}

async function openFill(entryId?: number) {
  if (!entryId) return
  try {
    const response = await request(`${ENDPOINT}/${entryId}`)
    if (!response.ok) throw new Error(await readError(response))
    const entry = await response.json() as Row
    fill.value = entry
    fillForm.value = {
      缺陷等级: entry['缺陷等级'] != null ? String(entry['缺陷等级']) : '',
      计划消除日: entry['计划消除日'] != null ? String(entry['计划消除日']) : '',
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺陷信息读取失败'
  }
}

function closeFill() {
  fill.value = null
}

async function submitFill() {
  if (!fill.value) return
  busy.value = true
  try {
    const values: Record<string, string> = {}
    if (fillForm.value['缺陷等级']) values['缺陷等级'] = fillForm.value['缺陷等级']
    if (fillForm.value['计划消除日']) values['计划消除日'] = fillForm.value['计划消除日']
    const response = await request(`${ENDPOINT}/${fill.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ values }),
    })
    const data = await response.json()
    if (!response.ok || data.ok === false) {
      errorMessage.value = data.message || '补录未保存'
      return
    }
    fill.value = null
    errorMessage.value = ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补录失败'
  } finally {
    busy.value = false
  }
}

async function startJob() {
  errorMessage.value = ''
  busy.value = true
  try {
    const response = await request(`${ENDPOINT}/grading-jobs`, { method: 'POST' })
    const data = await response.json()
    if (!response.ok || data.ok === false) {
      errorMessage.value = data.message || '批量定级启动失败'
      return
    }
    job.value = data.entry
    errorMessage.value = ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量定级启动失败'
  } finally {
    busy.value = false
  }
}

async function resumeJob() {
  if (!job.value) return
  busy.value = true
  try {
    const response = await request(`${ENDPOINT}/grading-jobs/${job.value.id}/resume`, {
      method: 'POST',
    })
    const data = await response.json()
    if (!response.ok || data.ok === false) {
      errorMessage.value = data.message || '续跑失败'
      return
    }
    job.value = data.entry
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量定级续跑失败'
  } finally {
    busy.value = false
  }
}

async function loadLatestJob() {
  try {
    const response = await request(`${ENDPOINT}/grading-jobs/latest`)
    if (!response.ok) return
    const data = await response.json()
    if (data.exists) job.value = data
  } catch {
    /* 重开页面时取不到任务不影响列表 */
  }
}

function jobStatusKey(status: string): string {
  return { 进行中: 'running', 已中断: 'blocked', 已完成: 'done' }[status] ?? ''
}

function itemResultKey(result: string): string {
  return { 已定级: 'done', 已中断: 'blocked', 已跳过: 'muted' }[result] ?? ''
}

onMounted(() => {
  void reload()
  void loadLatestJob()
})
</script>

<style scoped>
.warn-text { color: #b42318; }
.stat-card.clickable { cursor: pointer; }
.stat-card.active { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand); }
.row-overdue { background: #fff5f5; }
.verdict-cell { max-width: 300px; }
.verdict-sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
.tag {
  display: inline-block; padding: 1px 8px; border-radius: 10px;
  font-size: 12px; background: #eef2f7; color: #475569; white-space: nowrap;
}
.tag-overdue, .tag-blocked { background: #fee4e2; color: #b42318; }
.tag-done { background: #dcfae6; color: #067647; }
.tag-running { background: #e0efff; color: #1f6feb; }
.tag-muted { background: #f1f5f9; color: #94a3b8; }
.tag-status-待定级 { background: #fef0c7; color: #b54708; }
.tag-status-已定级 { background: #e0efff; color: #1f6feb; }
.tag-status-处置中 { background: #f3e8ff; color: #7e22ce; }
.tag-status-已消除 { background: #dcfae6; color: #067647; }
.job-panel {
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
  padding: 12px; margin-bottom: 12px;
}
.job-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.job-empty { color: var(--muted); font-size: 12px; margin: 4px 0 0; }
.job-line { display: flex; gap: 10px; align-items: center; font-size: 13px; }
.job-break { color: #b42318; font-size: 13px; margin: 6px 0; }
.job-actions { display: flex; gap: 8px; margin: 6px 0; }
.job-hint { color: var(--muted); font-size: 12px; margin: 0; }
.job-items { list-style: none; padding: 8px 0 0; margin: 8px 0 0; border-top: 1px dashed var(--border); font-size: 12px; }
.job-items li { display: flex; gap: 10px; align-items: center; padding: 2px 0; }
.job-item-note { color: var(--muted); }
.modal-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 20;
}
.modal { background: #fff; border-radius: 8px; width: 640px; max-width: 92vw; max-height: 86vh; overflow: auto; padding: 16px; }
.modal-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.detail-grid { display: grid; grid-template-columns: 120px 1fr; gap: 6px 12px; font-size: 13px; margin: 0 0 10px; }
.detail-grid dt { color: var(--muted); }
.detail-grid dd { margin: 0; }
.history-box { border-top: 1px solid var(--border); padding-top: 8px; font-size: 13px; }
.fill-form { display: flex; flex-direction: column; gap: 10px; }
.fill-form select, .fill-form input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-actions { display: flex; justify-content: flex-end; }
h4 { margin: 12px 0 6px; }
</style>
