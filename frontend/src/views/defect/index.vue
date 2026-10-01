<template>
  <section class="page" data-module="defect">
    <header class="page-head">
      <div>
        <h2>缺陷登记管理</h2>
        <p class="page-desc">定级阈值在后端只维护一份，列表与详情的判定结论来自同一口径；缺项不放行，超期单独追踪。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="runBatch">批量定级（从断点继续）</button>
        <button class="btn" type="button" @click="exportRows">导出缺陷登记清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card" :class="item.tone">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
        <span v-if="item.hint" class="stat-hint">{{ item.hint }}</span>
      </article>
    </div>

    <div v-if="batchMessage" class="batch-banner" :class="batchOk ? 'ok' : 'warn'">
      {{ batchMessage }}
    </div>

    <div class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}<span v-if="tab.count !== null" class="tab-count">{{ tab.count }}</span>
      </button>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>缺陷编号</span>
        <input v-model="keyword" placeholder="按缺陷编号检索" />
      </label>
      <label class="filter-item">
        <span>缺陷状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>定级判定（唯一口径）</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <span v-if="column === '缺陷编号'">
              <button class="link" type="button" @click="openDetail(row)">{{ row[column] ?? '—' }}</button>
            </span>
            <span v-else :class="{ 'overdue-text': column === '计划消除日' && row['是否超期'] }">
              {{ row[column] ?? '—' }}
            </span>
          </td>
          <td>
            <span class="judge-tag" :class="judgeClass(row)">{{ row['定级判定'] }}</span>
            <div class="judge-desc">{{ row['判定说明'] }}</div>
          </td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无符合条件的缺陷记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="detail" class="modal-mask" @click.self="closeDetail">
      <div class="modal">
        <header class="modal-head">
          <h3>缺陷详情 · {{ detail['缺陷编号'] }}</h3>
          <button type="button" class="link" @click="closeDetail">关闭</button>
        </header>

        <table class="detail-table">
          <tbody>
            <tr v-for="item in detailFields" :key="item">
              <th>{{ item }}</th>
              <td>{{ detail[item] ?? '—' }}</td>
            </tr>
            <tr>
              <th>定级判定</th>
              <td>
                <span class="judge-tag" :class="judgeClass(detail)">{{ detail['定级判定'] }}</span>
                <div class="judge-desc">{{ detail['判定说明'] }}</div>
                <ul v-if="missingItems(detail).length" class="missing-list">
                  <li v-for="miss in missingItems(detail)" :key="miss">缺项：{{ miss }}</li>
                </ul>
              </td>
            </tr>
            <tr v-if="detail['定级时间']">
              <th>最近定级</th>
              <td>{{ detail['定级时间'] }}（第 {{ detail['定级轮次'] }} 次定级，仅保留最新结论）</td>
            </tr>
          </tbody>
        </table>

        <section v-if="detail.status === '待定级'" class="patch-panel">
          <h4>定级缺项补录（缺项未补齐前不能确认定级）</h4>
          <div class="patch-form">
            <label class="filter-item">
              <span>缺陷等级</span>
              <select v-model="patchForm['缺陷等级']">
                <option value="">请选择</option>
                <option v-for="grade in grades" :key="grade" :value="grade">{{ grade }}</option>
              </select>
            </label>
            <label class="filter-item">
              <span>计划消除日</span>
              <input v-model="patchForm['计划消除日']" type="date" placeholder="YYYY-MM-DD" />
            </label>
            <button class="btn primary" type="button" :disabled="saving" @click="savePatch">保存补录</button>
          </div>
        </section>

        <footer class="modal-foot">
          <button
            v-for="action in availableActions(detail)"
            :key="action"
            class="btn"
            :class="{ primary: action === '确认定级' }"
            type="button"
            @click="runAction(action, detail)"
          >
            {{ action }}
          </button>
        </footer>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null | string[]>

const ENDPOINT = '/api/defect'
const columns = ['缺陷编号', '缺陷部位', '缺陷等级', '发现方式', '发现时间', '报告人', '计划消除日', '缺陷状态']
const detailFields = ['缺陷编号', '缺陷部位', '缺陷等级', '发现方式', '发现时间', '报告人', '计划消除日', '判定部位分组', '定级时限天数', '剩余天数']
const statuses = ['待定级', '已定级', '处置中', '已消除']
const grades = ['紧急', '重大', '一般', '轻微']
const allActions = ['确认定级', '提交消除', '验收消除']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const activeTab = ref<'all' | 'overdue'>('all')
const overdueCount = ref(0)
const saving = ref(false)

const todo = ref({ 待定级总数: 0, 可立即定级: 0, 缺项卡住: 0, 处置中超期: 0, 断点编号: null as string | null, 断点位置: null as number | null, 断点原因: null as string | null })
const batchMessage = ref('')
const batchOk = ref(true)

const detail = ref<Row | null>(null)
const patchForm = reactive<Record<string, string>>({ 缺陷等级: '', 计划消除日: '' })

const tabs = computed(() => [
  { key: 'all' as const, label: '全部缺陷', count: null },
  { key: 'overdue' as const, label: '超期缺陷', count: overdueCount.value },
])

const stats = computed(() => [
  { label: '待定级待办', value: todo.value.待定级总数, tone: '', hint: `其中 ${todo.value.缺项卡住} 条缺项卡住` },
  { label: '可立即定级', value: todo.value.可立即定级, tone: 'good', hint: '缺陷等级与计划消除日均齐备' },
  { label: '缺项卡住', value: todo.value.缺项卡住, tone: todo.value.缺项卡住 ? 'bad' : '', hint: '补录后从断点继续' },
  { label: '处置中超期', value: todo.value.处置中超期, tone: todo.value.处置中超期 ? 'bad' : '', hint: '超过计划消除日' },
])

function judgeClass(row: Row): string {
  const verdict = String(row['定级判定'] ?? '')
  if (verdict === '阈值内') return 'judge-ok'
  if (verdict === '超阈值') return 'judge-bad'
  return 'judge-na'
}

function missingItems(row: Row): string[] {
  const value = row['定级缺项']
  return Array.isArray(value) ? (value as string[]) : []
}

function availableActions(row: Row): string[] {
  // 按钮也按唯一状态序列给，后端再兜底；处置中不再给确认定级。
  const index = statuses.indexOf(String(row.status))
  if (index < 0 || index >= statuses.length - 1) return []
  return [allActions[index]]
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function switchTab(key: 'all' | 'overdue') {
  activeTab.value = key
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function readBody(response: Response): Promise<{ ok: boolean; message?: string; entry?: Row }> {
  try {
    return await response.json()
  } catch {
    return { ok: false, message: '接口返回无法解析，请稍后重试' }
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    if (activeTab.value === 'overdue') {
      const response = await request(`${ENDPOINT}/overdue`)
      if (!response.ok) throw new Error('超期缺陷读取失败')
      const payload = await response.json()
      rows.value = (payload.items ?? []) as Row[]
      total.value = payload.total ?? rows.value.length
    } else {
      const query = new URLSearchParams()
      if (keyword.value) query.set('keyword', keyword.value)
      if (statusFilter.value) query.set('status', statusFilter.value)
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) throw new Error('缺陷列表读取失败')
      const payload = await response.json()
      rows.value = payload.items ?? []
      total.value = payload.total ?? rows.value.length
    }
    await loadTodo()
    if (detail.value) await refreshDetail(Number(detail.value.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺陷列表读取失败'
  }
}

async function loadTodo() {
  try {
    const response = await request(`${ENDPOINT}/todo`)
    if (response.ok) todo.value = await response.json()
    overdueCount.value = todo.value.处置中超期
  } catch {
    // 待办只影响卡片，不阻断主列表
  }
}

async function refreshDetail(id: number) {
  const response = await request(`${ENDPOINT}/${id}`)
  if (response.ok) detail.value = await response.json()
}

function openDetail(row: Row) {
  detail.value = row
  patchForm['缺陷等级'] = String(row['缺陷等级'] ?? '')
  patchForm['计划消除日'] = String(row['计划消除日'] ?? '')
  void refreshDetail(Number(row.id))
}

function closeDetail() {
  detail.value = null
}

async function savePatch() {
  if (!detail.value) return
  saving.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${detail.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ values: { 缺陷等级: patchForm['缺陷等级'], 计划消除日: patchForm['计划消除日'] } }),
    })
    const body = await readBody(response)
    errorMessage.value = body.ok ? '' : (body.message ?? '补录未生效')
    if (body.entry) detail.value = body.entry
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补录失败'
  } finally {
    saving.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const body = await readBody(response)
    if (!body.ok) throw new Error(body.message ?? '缺陷动作未生效')
    if (detail.value && body.entry) detail.value = body.entry
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺陷操作失败'
    if (detail.value) await refreshDetail(Number(detail.value.id))
  }
}

async function runBatch() {
  errorMessage.value = ''
  batchMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/batch-grade`, { method: 'POST' })
    const body = await response.json()
    batchOk.value = Boolean(body.ok)
    batchMessage.value = body.message ?? '批量定级未执行'
    await reload()
  } catch (error) {
    batchOk.value = false
    batchMessage.value = error instanceof Error ? error.message : '批量定级失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.tab-bar { display: flex; gap: 8px; margin-bottom: 12px; }
.tab-btn {
  border: 1px solid var(--border); background: #fff; border-radius: 6px 6px 0 0;
  padding: 6px 14px; cursor: pointer; font-size: 13px; border-bottom: none;
}
.tab-btn.active { background: var(--brand); color: #fff; border-color: var(--brand); }
.tab-count { margin-left: 6px; font-size: 12px; opacity: 0.85; }
.judge-tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; white-space: nowrap; }
.judge-ok { background: #e7f6ec; color: #1a7f37; }
.judge-bad { background: #fdecec; color: #b42318; }
.judge-na { background: #f1f3f7; color: #64748b; }
.judge-desc { color: var(--muted); font-size: 12px; margin-top: 2px; max-width: 340px; }
.overdue-text { color: #b42318; font-weight: 600; }
.stat-card.good .stat-value { color: #1a7f37; }
.stat-card.bad .stat-value { color: #b42318; }
.stat-hint { display: block; color: var(--muted); font-size: 11px; margin-top: 2px; }
.batch-banner { border-radius: 6px; padding: 8px 12px; font-size: 13px; margin-bottom: 12px; }
.batch-banner.ok { background: #e7f6ec; color: #1a7f37; border: 1px solid #b7e0c3; }
.batch-banner.warn { background: #fdf3e7; color: #9a5200; border: 1px solid #f3d4ad; }
.modal-mask {
  position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 20;
}
.modal { background: #fff; border-radius: 8px; width: 720px; max-width: 92vw; max-height: 88vh; overflow: auto; padding: 16px 20px; }
.modal-head { display: flex; justify-content: space-between; align-items: center; }
.modal-head h3 { margin: 0; font-size: 16px; }
.detail-table { width: 100%; border-collapse: collapse; margin-top: 10px; }
.detail-table th, .detail-table td { border: 1px solid var(--border); padding: 6px 10px; font-size: 13px; text-align: left; vertical-align: top; }
.detail-table th { width: 130px; background: #f8fafc; color: var(--muted); }
.missing-list { margin: 6px 0 0; padding-left: 18px; color: #b42318; font-size: 12px; }
.patch-panel { margin-top: 14px; border-top: 1px dashed var(--border); padding-top: 10px; }
.patch-panel h4 { margin: 0 0 8px; font-size: 13px; }
.patch-form { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
</style>
