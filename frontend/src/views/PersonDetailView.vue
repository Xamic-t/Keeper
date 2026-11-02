<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { FactOut, LedgerOut, PersonDetail } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

interface TimelineItem {
  at: string
  precision: 'minute' | 'day' | string
  kind: string
  title: string
  detail: string | null
  amount: string | null
  direction: string | null
}

const route = useRoute()
const router = useRouter()

const personId = Number(route.params.id)
const detail = ref<PersonDetail | null>(null)
const facts = ref<FactOut[]>([])
const ledger = ref<LedgerOut[]>([])
const timeline = ref<TimelineItem[]>([])
const loading = ref(true)
const rewriting = ref(false)
const message = ref('')

// ── 编辑状态 ────────────────────────────────────────────
// 每个区块单独一个开关：一次只改一处，出错时用户知道是哪一块没保存
const editingInfo = ref(false)
const editingRelation = ref(false)
const editingFactId = ref<number | null>(null)
const showLedgerForm = ref(false)
const saving = ref(false)

const infoForm = reactive({ nickname: '', note: '' })
const relationForm = reactive({ stage: '', taboo: '', birthday_solar: '' })
const factForm = reactive({ content: '', weight: 1 })
const ledgerForm = reactive({
  direction: 'out',
  kind: 'gift',
  amount: '',
  occurred_at: todayInput(),
  note: '',
})

const STAGES = [
  { value: 'new', label: '新识' },
  { value: 'familiar', label: '熟悉' },
  { value: 'core', label: '核心' },
  { value: 'cold', label: '冷淡' },
]
const KINDS = [
  { value: 'gift', label: '送礼' },
  { value: 'tip', label: '打赏' },
  { value: 'order', label: '订单' },
  { value: 'payment', label: '付款' },
  { value: 'refund', label: '退款' },
]
const DIRECTIONS = [
  { value: 'out', label: '我给他' },
  { value: 'in', label: '他给我' },
]

const stageLabel = computed(
  () => STAGES.find((item) => item.value === detail.value?.relationship.stage)?.label ?? '未知',
)
const stageIsManual = computed(() => detail.value?.relationship.stage_source === 'manual')
/** 公历直接显示；农历按库里存的 month/day/is_leap 拼出来，别显示"没记"骗人。 */
const birthdayText = computed(() => {
  const rel = detail.value?.relationship
  if (!rel) return ''
  if (rel.birthday_type === 'lunar' && rel.birthday_lunar) {
    const month = rel.birthday_lunar.month
    const day = rel.birthday_lunar.day
    const leap = rel.birthday_lunar.is_leap ? '闰' : ''
    return month ? `农历${leap}${month}月${day}日` : '没记'
  }
  return rel.birthday_solar ?? '没记'
})

function todayInput() {
  // 日期输入框只认 YYYY-MM-DD，用本地时间拼，别用 toISOString（会差一天）
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

async function load() {
  loading.value = true
  try {
    const [person, factList, ledgerList, timelineList] = await Promise.all([
      api.get<PersonDetail>(`/persons/${personId}`),
      api.get<FactOut[]>(`/persons/${personId}/facts`),
      api.get<LedgerOut[]>(`/persons/${personId}/ledger`),
      api.get<TimelineItem[]>(`/persons/${personId}/timeline`),
    ])
    detail.value = person
    facts.value = factList
    ledger.value = ledgerList
    timeline.value = timelineList
  } catch (err) {
    message.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

/** 有上一页就退回去；直接输网址进来的（没有历史）就回人列表 */
function goBack() {
  const canGoBack = Boolean(window.history.state?.back)
  if (canGoBack) router.back()
  else router.push({ name: 'persons' })
}

function pad(n: number) {
  return String(n).padStart(2, '0')
}

/** 时间线上的一格：能到分钟就显示分钟，只有日期的就只显示日期 */
function formatAt(item: TimelineItem) {
  const at = new Date(item.at)
  if (Number.isNaN(at.getTime())) return ''
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const dayDiff = Math.round(
    (startOfToday.getTime() - new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime()) /
      86400000,
  )

  if (item.precision === 'day') {
    return at.getFullYear() === now.getFullYear()
      ? `${at.getMonth() + 1}月${at.getDate()}日`
      : `${at.getFullYear()}年${at.getMonth() + 1}月${at.getDate()}日`
  }

  const clock = `${pad(at.getHours())}:${pad(at.getMinutes())}`
  if (dayDiff === 0) return `今天 ${clock}`
  if (dayDiff === 1) return `昨天 ${clock}`
  if (at.getFullYear() === now.getFullYear()) {
    return `${at.getMonth() + 1}月${at.getDate()}日 ${clock}`
  }
  return `${at.getFullYear()}年${at.getMonth() + 1}月${at.getDate()}日 ${clock}`
}

function kindLabel(kind: string) {
  if (kind === 'ledger') return '往来'
  if (kind === 'event') return '事件'
  return '事实'
}

function fail(err: unknown, fallback: string) {
  message.value = err instanceof Error ? err.message : fallback
}

// ── 改基本信息 ──────────────────────────────────────────
function startEditInfo() {
  if (!detail.value) return
  infoForm.nickname = detail.value.nickname
  infoForm.note = detail.value.note ?? ''
  editingInfo.value = true
  message.value = ''
}

async function saveInfo() {
  if (!infoForm.nickname.trim()) {
    message.value = '昵称不能为空'
    return
  }
  saving.value = true
  try {
    await api.patch(`/persons/${personId}`, {
      nickname: infoForm.nickname.trim(),
      // 备注允许清空：传空串时后端存空字符串，比 null 更符合"我确实写了个空"
      note: infoForm.note.trim(),
    })
    editingInfo.value = false
    message.value = '已保存'
    await load()
  } catch (err) {
    fail(err, '没保存成功')
  } finally {
    saving.value = false
  }
}

// ── 改关系状态 ──────────────────────────────────────────
function startEditRelation() {
  const rel = detail.value?.relationship
  if (!rel) return
  relationForm.stage = rel.stage
  relationForm.taboo = rel.taboo.join('、')
  relationForm.birthday_solar = rel.birthday_solar ?? ''
  editingRelation.value = true
  message.value = ''
}

async function saveRelation() {
  saving.value = true
  try {
    const body: Record<string, unknown> = {
      stage: relationForm.stage,
      // 中文顿号、逗号、空格都当分隔符：用户不会记得该用哪个
      taboo: relationForm.taboo
        .split(/[、,，\s]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    }
    if (relationForm.birthday_solar) {
      // 类型和值必须配套，后端有校验
      body.birthday_type = 'solar'
      body.birthday_solar = relationForm.birthday_solar
    }
    await api.patch(`/persons/${personId}/relationship`, body)
    editingRelation.value = false
    message.value = '关系状态已更新'
    await load()
  } catch (err) {
    fail(err, '没保存成功')
  } finally {
    saving.value = false
  }
}

// ── 事实增删改 ──────────────────────────────────────────
function startEditFact(fact: FactOut) {
  editingFactId.value = fact.id
  factForm.content = fact.content
  factForm.weight = fact.weight
  message.value = ''
}

async function saveFact(id: number) {
  if (!factForm.content.trim()) {
    message.value = '内容不能为空'
    return
  }
  saving.value = true
  try {
    await api.patch(`/facts/${id}`, {
      content: factForm.content.trim(),
      weight: factForm.weight,
    })
    editingFactId.value = null
    message.value = '事实已更新'
    await load()
  } catch (err) {
    fail(err, '没保存成功')
  } finally {
    saving.value = false
  }
}

async function deleteFact(fact: FactOut) {
  if (!confirm(`删掉这条事实？\n\n${fact.content}`)) return
  saving.value = true
  try {
    await api.delete(`/facts/${fact.id}`)
    message.value = '已删除'
    await load()
  } catch (err) {
    fail(err, '没删掉')
  } finally {
    saving.value = false
  }
}

// ── 往来 ────────────────────────────────────────────────
async function addLedger() {
  const amount = Number(ledgerForm.amount)
  if (!Number.isFinite(amount) || amount <= 0) {
    message.value = '金额要填一个大于 0 的数'
    return
  }
  saving.value = true
  try {
    await api.post('/ledger', {
      person_id: personId,
      direction: ledgerForm.direction,
      kind: ledgerForm.kind,
      amount: amount.toFixed(2),
      occurred_at: ledgerForm.occurred_at,
      note: ledgerForm.note.trim() || null,
    })
    showLedgerForm.value = false
    ledgerForm.amount = ''
    ledgerForm.note = ''
    message.value = '已记一笔'
    await load()
  } catch (err) {
    fail(err, '没记上')
  } finally {
    saving.value = false
  }
}

async function deleteLedger(item: LedgerOut) {
  if (!confirm(`删掉这笔往来？${item.direction === 'in' ? '+' : '-'}${item.amount}`)) return
  saving.value = true
  try {
    await api.delete(`/ledger/${item.id}`)
    message.value = '已删除'
    await load()
  } catch (err) {
    fail(err, '没删掉')
  } finally {
    saving.value = false
  }
}

// ── 其它 ────────────────────────────────────────────────
async function rewriteSummary() {
  rewriting.value = true
  message.value = ''
  try {
    await api.post(`/persons/${personId}/summary/rewrite`)
    message.value = '综述已重新生成'
    await load()
  } catch (err) {
    message.value = err instanceof Error ? err.message : '生成失败（可能还没配模型 Key）'
  } finally {
    rewriting.value = false
  }
}

/** 导出成 JSON 下载。手机上也能存到「文件」里。 */
async function exportJson() {
  try {
    const data = await api.get<Record<string, unknown>>(`/persons/${personId}/export`)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${detail.value?.nickname ?? 'person'}-${personId}.json`
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    fail(err, '导出失败')
  }
}

async function removePerson() {
  if (!confirm('删掉这个人？相关数据会全部清除，且不可恢复。')) return
  try {
    await api.delete(`/persons/${personId}`)
    router.push({ name: 'persons' })
  } catch (err) {
    fail(err, '没删掉')
  }
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <!-- 返回：手机上原本没有任何回上层的入口 -->
    <button
      class="mb-2 -ml-1 flex items-center gap-1 rounded-md px-1 py-1 text-sm text-ink-soft transition hover:text-ink"
      @click="goBack"
    >
      <span aria-hidden="true">←</span>
      <span>人</span>
    </button>

    <div v-if="loading" class="text-sm text-ink-soft">加载中…</div>

    <template v-else-if="detail">
      <div class="flex items-start justify-between gap-3">
        <PageHeader :title="detail.nickname" :subtitle="detail.relationship.roles.join(' / ')" />
        <button class="btn btn-ghost shrink-0" @click="startEditInfo">编辑</button>
      </div>

      <p v-if="message" class="mb-3 rounded-lg bg-bg-alt px-3 py-2 text-sm text-ink-soft">
        {{ message }}
      </p>

      <!-- 改基本信息 -->
      <section v-if="editingInfo" class="card mb-4 space-y-3 p-4">
        <label class="block text-xs text-ink-soft">
          称呼
          <input v-model="infoForm.nickname" class="input mt-1" maxlength="50" />
        </label>
        <label class="block text-xs text-ink-soft">
          备注
          <textarea
            v-model="infoForm.note"
            class="input mt-1 h-20 resize-none"
            maxlength="2000"
            placeholder="怎么认识的、该注意什么"
          ></textarea>
        </label>
        <div class="flex gap-2">
          <button class="btn btn-primary" :disabled="saving" @click="saveInfo">保存</button>
          <button class="btn btn-ghost" @click="editingInfo = false">取消</button>
        </div>
      </section>

      <!-- 综述 -->
      <section class="card mb-4 p-4">
        <div class="mb-2 flex items-center justify-between">
          <h2 class="text-sm font-medium text-ink-soft">人物综述</h2>
          <button
            class="text-sm text-accent hover:text-accent-deep"
            :disabled="rewriting"
            @click="rewriteSummary"
          >
            {{ rewriting ? '生成中…' : detail.summary_stale ? '重新生成' : '重写' }}
          </button>
        </div>
        <p v-if="detail.summary" class="leading-relaxed">{{ detail.summary }}</p>
        <p v-else class="text-sm text-ink-soft">还没有综述。确认几条事实后就能生成。</p>
      </section>

      <!-- 关键信息 -->
      <section class="card mb-4 divide-y divide-border">
        <div class="flex items-center justify-between px-4 py-3 text-sm">
          <span class="text-ink-soft">阶段</span>
          <span class="flex items-center gap-2">
            {{ stageLabel }}
            <span v-if="stageIsManual" class="tag">手动</span>
            <button class="text-xs text-accent hover:text-accent-deep" @click="startEditRelation">
              改
            </button>
          </span>
        </div>
        <div class="flex items-center justify-between px-4 py-3 text-sm">
          <span class="text-ink-soft">生日</span>
          <span>{{ birthdayText }}</span>
        </div>
        <div class="flex items-center justify-between px-4 py-3 text-sm">
          <span class="text-ink-soft">收 / 付</span>
          <span>{{ detail.relationship.total_in }} / {{ detail.relationship.total_out }}</span>
        </div>
        <div
          v-if="detail.relationship.taboo.length"
          class="flex items-center justify-between px-4 py-3 text-sm"
        >
          <span class="text-ink-soft">禁忌</span>
          <span class="text-accent-deep">{{ detail.relationship.taboo.join('、') }}</span>
        </div>
        <div class="flex items-center justify-between px-4 py-3 text-sm">
          <span class="text-ink-soft">已记住</span>
          <span>{{ detail.counts.facts }} 条事实 · {{ detail.counts.ledger }} 笔往来</span>
        </div>
      </section>

      <!-- 改关系状态 -->
      <section v-if="editingRelation" class="card mb-4 space-y-3 p-4">
        <label class="block text-xs text-ink-soft">
          阶段
          <select v-model="relationForm.stage" class="input mt-1">
            <option v-for="item in STAGES" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <label class="block text-xs text-ink-soft">
          生日（公历，可留空）
          <input v-model="relationForm.birthday_solar" type="date" class="input mt-1" />
        </label>
        <label class="block text-xs text-ink-soft">
          禁忌（用顿号或逗号分开）
          <input
            v-model="relationForm.taboo"
            class="input mt-1"
            placeholder="比如：别提体重、不吃辣"
          />
        </label>
        <div class="flex gap-2">
          <button class="btn btn-primary" :disabled="saving" @click="saveRelation">保存</button>
          <button class="btn btn-ghost" @click="editingRelation = false">取消</button>
        </div>
      </section>

      <!-- 标签 -->
      <section v-if="detail.tags.length" class="mb-4 flex flex-wrap gap-1">
        <span v-for="tag in detail.tags" :key="tag" class="tag">{{ tag }}</span>
      </section>

      <!-- 时间线 -->
      <section class="mb-6">
        <h2 class="mb-2 text-sm font-medium text-ink-soft">时间线</h2>
        <div v-if="timeline.length" class="card">
          <div
            v-for="(item, index) in timeline"
            :key="index"
            class="flex gap-3 px-4 py-3"
            :class="index > 0 ? 'border-t border-border' : ''"
          >
            <div class="w-24 shrink-0 pt-0.5 text-right">
              <div class="text-xs tabular-nums text-ink-soft">{{ formatAt(item) }}</div>
              <div class="mt-0.5 text-[11px] text-ink-soft/60">{{ kindLabel(item.kind) }}</div>
            </div>
            <div class="min-w-0 flex-1">
              <div class="text-sm leading-relaxed">{{ item.title }}</div>
              <p v-if="item.detail" class="mt-1 text-xs text-ink-soft">{{ item.detail }}</p>
            </div>
          </div>
        </div>
        <EmptyState v-else title="时间线还是空的" hint="确认几条事实后，这里按时间排出来" />
      </section>

      <!-- 事实 -->
      <section class="mb-6">
        <h2 class="mb-2 text-sm font-medium text-ink-soft">事实</h2>
        <div v-if="facts.length" class="card divide-y divide-border">
          <div v-for="fact in facts" :key="fact.id" class="px-4 py-3">
            <template v-if="editingFactId === fact.id">
              <textarea
                v-model="factForm.content"
                class="input h-20 resize-none"
                maxlength="2000"
              ></textarea>
              <div class="mt-2 flex items-center gap-3">
                <label class="flex items-center gap-2 text-xs text-ink-soft">
                  权重
                  <input
                    v-model.number="factForm.weight"
                    type="range"
                    min="0"
                    max="1"
                    step="0.1"
                    class="w-28"
                  />
                  <span class="tabular-nums">{{ factForm.weight.toFixed(1) }}</span>
                </label>
                <div class="ml-auto flex gap-2">
                  <button class="btn btn-primary" :disabled="saving" @click="saveFact(fact.id)">
                    保存
                  </button>
                  <button class="btn btn-ghost" @click="editingFactId = null">取消</button>
                </div>
              </div>
            </template>
            <template v-else>
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0 flex-1 text-sm">{{ fact.content }}</div>
                <div class="flex shrink-0 gap-2 text-xs">
                  <button class="text-ink-soft hover:text-accent" @click="startEditFact(fact)">
                    改
                  </button>
                  <button class="text-ink-soft hover:text-danger" @click="deleteFact(fact)">
                    删
                  </button>
                </div>
              </div>
              <div class="mt-1 flex items-center gap-2 text-xs text-ink-soft">
                <span class="tag">{{ fact.category }}</span>
                <span>权重 {{ fact.weight.toFixed(2) }}</span>
                <span v-if="fact.evidence" class="truncate">出处：{{ fact.evidence }}</span>
              </div>
            </template>
          </div>
        </div>
        <EmptyState v-else title="还没有确认的事实" hint="投喂截图后，去「确认」里过一遍" />
      </section>

      <!-- 往来 -->
      <section class="mb-6">
        <div class="mb-2 flex items-center justify-between">
          <h2 class="text-sm font-medium text-ink-soft">往来</h2>
          <button class="text-sm text-accent hover:text-accent-deep" @click="showLedgerForm = !showLedgerForm">
            {{ showLedgerForm ? '收起' : '记一笔' }}
          </button>
        </div>

        <form v-if="showLedgerForm" class="card mb-3 space-y-3 p-4" @submit.prevent="addLedger">
          <div class="flex gap-2">
            <select v-model="ledgerForm.direction" class="input">
              <option v-for="item in DIRECTIONS" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
            <select v-model="ledgerForm.kind" class="input">
              <option v-for="item in KINDS" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </div>
          <div class="flex gap-2">
            <input
              v-model="ledgerForm.amount"
              class="input"
              inputmode="decimal"
              placeholder="金额"
            />
            <input v-model="ledgerForm.occurred_at" type="date" class="input" />
          </div>
          <input v-model="ledgerForm.note" class="input" placeholder="备注（可空）" maxlength="2000" />
          <button class="btn btn-primary" type="submit" :disabled="saving">保存</button>
        </form>

        <div v-if="ledger.length" class="card divide-y divide-border">
          <div
            v-for="item in ledger"
            :key="item.id"
            class="flex items-center justify-between px-4 py-3 text-sm"
          >
            <span>{{ item.occurred_at }} · {{ item.kind }}</span>
            <span class="flex items-center gap-3">
              <span :class="item.direction === 'in' ? 'text-success' : 'text-ink'">
                {{ item.direction === 'in' ? '+' : '-' }}{{ item.amount }}
              </span>
              <button class="text-xs text-ink-soft hover:text-danger" @click="deleteLedger(item)">
                删
              </button>
            </span>
          </div>
        </div>
        <EmptyState v-else title="还没有往来记录" />
      </section>

      <div class="flex justify-between">
        <button class="btn btn-ghost" @click="exportJson">导出 JSON</button>
        <button class="btn btn-ghost text-accent-deep" @click="removePerson">删除这个人</button>
      </div>
    </template>

    <div v-else class="text-sm text-ink-soft">找不到这个人。</div>
  </div>
</template>
