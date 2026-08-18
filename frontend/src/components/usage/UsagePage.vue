<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

const props = defineProps({
  workspace: { type: Object, default: null },
})

const REFRESH_MS = 5000
const CHART_W = 900
const CHART_H = 260
const AREA_PLOT_INSET = 12
const TOKEN_AXIS_W = 62
const PLOT_TOP = 22
const PLOT_BOTTOM = 224
const MS_PER_HOUR = 60 * 60 * 1000
const MS_PER_DAY = 24 * MS_PER_HOUR
const DAY_WINDOW_DAYS = 30
const HOUR_WINDOW_HOURS = 24
const RANK_LIMIT = 6
const STAGE_FUNNEL_LIMIT = 8
const ANOMALY_MULTIPLIER = 3
const MODEL_COLORS = ['#2d7f75', '#d36b3d', '#5e63c8', '#c09628', '#3e80bb', '#9a5d7b']
const STAGE_COLORS = ['#2f6f73', '#b45f3c', '#6d5bd0', '#c89422', '#3970b8', '#8a596b']

const records = ref([])
const total = ref({})
const loading = ref(false)
const error = ref('')
const precision = ref('day')
const nowMs = ref(Date.now())
const isReframing = ref(false)
const providerFilter = ref('')
const modelFilter = ref('')
const stageFilter = ref('')
const workspaceFilter = ref('')
const groupMode = ref('model')
const hover = ref(null)
const activeBucketKey = ref('')
const activeAreaKey = ref('')
const pageRef = ref(null)
const tooltipRef = ref(null)
const tooltipAnchor = ref({ x: 16, y: 16 })
const tooltipPosition = ref({ x: 16, y: 16 })
let refreshTimer = null
let reframeTimer = null

const filteredRecords = computed(() => records.value.filter(record =>
  (!workspaceFilter.value || workspaceKey(record) === workspaceFilter.value) &&
  (!providerFilter.value || record.provider === providerFilter.value) &&
  (!modelFilter.value || modelName(record) === modelFilter.value) &&
  (!stageFilter.value || stageName(record) === stageFilter.value)
))
const viewKey = computed(() => [
  precision.value,
  providerFilter.value || '*',
  modelFilter.value || '*',
  stageFilter.value || '*',
  workspaceFilter.value || '*',
  groupMode.value,
].join('|'))
const providers = computed(() => unique(records.value.map(record => record.provider)))
const models = computed(() => unique(records.value.map(record => modelName(record))))
const stages = computed(() => unique(records.value.map(record => stageName(record))))
const workspaces = computed(() => unique(records.value.map(record => workspaceKey(record))))
const requests = computed(() => filteredRecords.value.length)
const filteredTotal = computed(() => filteredRecords.value.reduce((acc, record) => {
  acc.input_tokens += Number(record.input_tokens || 0)
  acc.output_tokens += Number(record.output_tokens || 0)
  acc.cached_tokens += Number(record.cached_tokens || 0)
  acc.total_tokens += Number(record.total_tokens || 0)
  addCost(acc, record)
  return acc
}, emptyUsageTotal()))
const cacheRatio = computed(() => {
  const cached = Number(filteredTotal.value.cached_tokens || 0)
  const input = Number(filteredTotal.value.input_tokens || 0)
  return input ? Math.round((cached / input) * 100) : 0
})
const topGroups = computed(() => rankBy(filteredRecords.value, groupName, () => 1).slice(0, RANK_LIMIT))
const windowMeta = computed(() => {
  if (precision.value === 'hour') {
    const endMs = floorHour(nowMs.value)
    return {
      label: 'last 24 hours',
      count: HOUR_WINDOW_HOURS,
      bucketMs: MS_PER_HOUR,
      startMs: endMs - ((HOUR_WINDOW_HOURS - 1) * MS_PER_HOUR),
      endMs: endMs + MS_PER_HOUR - 1,
    }
  }
  const endMs = floorDay(nowMs.value)
  return {
    label: 'last 30 days',
    count: DAY_WINDOW_DAYS,
    bucketMs: MS_PER_DAY,
    startMs: endMs - ((DAY_WINDOW_DAYS - 1) * MS_PER_DAY),
    endMs: endMs + MS_PER_DAY - 1,
  }
})
const chartRecords = computed(() => filteredRecords.value.filter(record => {
  const time = recordTime(record)
  return Number.isFinite(time) && time >= windowMeta.value.startMs && time <= windowMeta.value.endMs
}))
const olderRecords = computed(() => filteredRecords.value.filter(record => {
  const time = recordTime(record)
  return Number.isFinite(time) && time < windowMeta.value.startMs
}))
const olderTotal = computed(() => sumUsage(olderRecords.value))
const chartScopeText = computed(() => precision.value === 'hour'
  ? 'Charts show the last 24 hours, grouped by hour.'
  : 'Charts show the last 30 days, grouped by day.'
)
const historyScopeText = computed(() =>
  `Totals and rankings use all matching history. Before this window: ${fmt(olderRecords.value.length)} calls · ${fmt(olderTotal.value.total_tokens)} tokens.`
)
const chartGroups = computed(() => rankBy(chartRecords.value, groupName, () => 1).slice(0, RANK_LIMIT))
const stageColors = computed(() => colorMap(stages.value, STAGE_COLORS))
const groupColors = computed(() => colorMap(unique([
  ...chartGroups.value.map(item => item.name),
  ...topGroups.value.map(item => item.name),
]), MODEL_COLORS))
const yTicks = computed(() => [1, .75, .5, .25, 0].map(ratio => ({
  value: Math.round(maxTokens.value * ratio),
  top: `${18 + (1 - ratio) * 190}px`,
})))
const tooltipStyle = computed(() => {
  if (!hover.value) return {}
  return {
    left: `${tooltipPosition.value.x}px`,
    top: `${tooltipPosition.value.y}px`,
  }
})

const buckets = computed(() => {
  const byKey = new Map()
  for (let index = 0; index < windowMeta.value.count; index += 1) {
    const bucketMs = windowMeta.value.startMs + (index * windowMeta.value.bucketMs)
    const key = bucketKey(bucketMs, precision.value)
    byKey.set(key, emptyBucket(key, precision.value))
  }
  for (const record of chartRecords.value) {
    const key = bucketKey(record.timestamp, precision.value)
    if (!byKey.has(key)) continue
    const bucket = byKey.get(key)
    const stage = stageName(record)
    const group = groupName(record)
    addUsage(bucket, record)
    bucket.requests += 1
    bucket.stages[stage] = (bucket.stages[stage] || 0) + Number(record.total_tokens || 0)
    if (!bucket.models[group]) {
      bucket.models[group] = {
        name: group,
        provider: record.provider || 'unknown provider',
        model: modelName(record),
        workspace: workspaceLabel(record),
        input_tokens: 0,
        output_tokens: 0,
        cached_tokens: 0,
        total_tokens: 0,
        cost: 0,
        costs_by_currency: {},
        currency: record.currency || '',
        requests: 0,
      }
    }
    addUsage(bucket.models[group], record)
    bucket.models[group].requests += 1
  }
  return [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key))
})
const maxTokens = computed(() => Math.max(1, ...buckets.value.map(bucket => bucket.total_tokens)))
const bucketBaseline = computed(() => {
  const active = buckets.value.filter(bucket => bucket.total_tokens > 0)
  const average = active.reduce((sum, bucket) => sum + bucket.total_tokens, 0) / Math.max(1, active.length)
  return Math.max(1, average)
})
const anomalyBucketKeys = computed(() => new Set(
  buckets.value
    .filter(bucket => bucket.total_tokens > 0 && bucket.total_tokens >= bucketBaseline.value * ANOMALY_MULTIPLIER)
    .map(bucket => bucket.key)
))
const stageBreakdown = computed(() => rankBy(filteredRecords.value, stageName, record => record.total_tokens || 0).slice(0, STAGE_FUNNEL_LIMIT))
const chartUsageTotal = computed(() => sumUsage(chartRecords.value))
const stageFunnel = computed(() => {
  const ranked = rankUsageBy(chartRecords.value, stageName).slice(0, STAGE_FUNNEL_LIMIT)
  const maxStageTokens = Math.max(1, ...ranked.map(item => item.total_tokens))
  return ranked.map(item => ({
    ...item,
    width: `${Math.max(4, Math.round((item.total_tokens / maxStageTokens) * 100))}%`,
    share: Math.round((item.total_tokens / Math.max(1, chartUsageTotal.value.total_tokens)) * 100),
  }))
})
const sessionCosts = computed(() => rankUsageBy(chartRecords.value, sessionName).slice(0, RANK_LIMIT).map(item => ({
  ...item,
  average_tokens: Math.round(item.total_tokens / Math.max(1, item.requests)),
  cache_rate: Math.round((item.cached_tokens / Math.max(1, item.input_tokens)) * 100),
})))
const areaGuideLines = computed(() => [62, 116, 170, 224])
const areaSeries = computed(() => {
  const names = chartGroups.value.map(item => item.name)
  const countMax = Math.max(1, ...buckets.value.flatMap(bucket => names.map(name => bucket.models[name]?.requests || 0)))
  return names.map((name, index) => {
    const points = buckets.value.map((bucket, bucketIndex) => ({
      x: chartX(bucketIndex, buckets.value.length),
      y: chartY(bucket.models[name]?.requests || 0, countMax),
      value: bucket.models[name]?.requests || 0,
      label: bucket.label,
      bucket,
    }))
    return {
      name,
      color: groupColors.value[name] || MODEL_COLORS[index % MODEL_COLORS.length],
      points,
      line: smoothPath(points),
      area: areaPath(points),
      total: chartGroups.value.find(item => item.name === name)?.value || 0,
    }
  })
})

async function loadUsage() {
  loading.value = true
  error.value = ''
  try {
    const response = await fetch('/api/sessions/usage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data.error || `Usage request failed (${response.status})`)
    records.value = Array.isArray(data.records) ? data.records : []
    total.value = data.total || {}
  } catch (reason) {
    error.value = reason.message || 'Failed to load usage'
  } finally {
    nowMs.value = Date.now()
    loading.value = false
  }
}

function scheduleRefresh() {
  window.clearInterval(refreshTimer)
  refreshTimer = window.setInterval(loadUsage, REFRESH_MS)
}

function fmt(value) {
  return Number(value || 0).toLocaleString()
}

function compactTokens(value) {
  const number = Number(value || 0)
  const abs = Math.abs(number)
  if (abs >= 1_000_000_000) return `${trimCompact(number / 1_000_000_000)}B`
  if (abs >= 1_000_000) return `${trimCompact(number / 1_000_000)}M`
  if (abs >= 1_000) return `${trimCompact(number / 1_000)}K`
  return fmt(number)
}

function trimCompact(value) {
  return value >= 10 ? value.toFixed(0) : value.toFixed(1).replace(/\.0$/, '')
}

function money(value) {
  return Number(value || 0).toFixed(6)
}

function formatCosts(item) {
  const costs = item?.costs_by_currency || {}
  const entries = Object.entries(costs)
    .filter(([, value]) => Number(value || 0) !== 0)
    .sort(([left], [right]) => left.localeCompare(right))
  if (entries.length) {
    return entries.map(([currency, value]) => `${currency} ${money(value)}`).join(' · ')
  }
  const cost = Number(item?.cost || 0)
  if (!cost) return '—'
  return `${item?.currency || 'USD'} ${money(cost)}`
}

function unique(values) {
  return [...new Set(values.filter(value => String(value || '').trim()))].sort()
}

function colorMap(names, palette) {
  const map = {}
  names.forEach((name, index) => { map[name] = palette[index % palette.length] })
  return map
}

function addUsage(target, source) {
  target.input_tokens += Number(source.input_tokens || 0)
  target.output_tokens += Number(source.output_tokens || 0)
  target.cached_tokens += Number(source.cached_tokens || 0)
  target.total_tokens += Number(source.total_tokens || 0)
  addCost(target, source)
}

function addCost(target, source) {
  const cost = Number(source.cost || 0)
  const currency = String(source.currency || '').trim()
  target.cost += cost
  if (currency) target.currency = currency
  if (!target.costs_by_currency) target.costs_by_currency = {}
  if (cost && currency) {
    target.costs_by_currency[currency] = Number(((target.costs_by_currency[currency] || 0) + cost).toFixed(6))
  }
}

function emptyUsageTotal() {
  return {
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    total_tokens: 0,
    cost: 0,
    currency: '',
    costs_by_currency: {},
  }
}

function sumUsage(items) {
  return items.reduce((acc, record) => {
    addUsage(acc, record)
    return acc
  }, emptyUsageTotal())
}

function modelName(record) {
  return record.model || 'unknown model'
}

function providerModelName(record) {
  return `${record.provider || 'unknown provider'} / ${modelName(record)}`
}

function workspaceKey(record) {
  const id = record.workspace_id || 'unknown'
  return `${id}::${workspaceLabel(record)}`
}

function workspaceLabel(record) {
  const name = String(record.workspace_name || '').trim()
  const path = String(record.workspace_path || '').split(/[\\/]/).filter(Boolean).pop()
  return name || path || 'Unknown workspace'
}

function workspaceOptionLabel(value) {
  return String(value || '').split('::').slice(1).join('::') || 'Unknown workspace'
}

function groupName(record) {
  return groupMode.value === 'workspace' ? workspaceLabel(record) : providerModelName(record)
}

function stageName(record) {
  return record.stage || 'unknown'
}

function sessionName(record) {
  const id = record.session_id || 'unknown'
  const name = String(record.session_name || 'Untitled session').trim()
  return `#${id} · ${name || 'Untitled session'}`
}

function recordTime(record) {
  return new Date(record.timestamp).getTime()
}

function floorHour(value) {
  const date = new Date(value)
  date.setUTCMinutes(0, 0, 0)
  return date.getTime()
}

function floorDay(value) {
  const date = new Date(value)
  date.setUTCHours(0, 0, 0, 0)
  return date.getTime()
}

function emptyBucket(key, mode) {
  return {
    key,
    label: bucketLabel(key, mode),
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    total_tokens: 0,
    cost: 0,
    currency: '',
    costs_by_currency: {},
    requests: 0,
    stages: {},
    models: {},
  }
}

function bucketKey(value, mode) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'unknown'
  const day = date.toISOString().slice(0, 10)
  return mode === 'hour' ? `${day} ${date.toISOString().slice(11, 13)}:00` : day
}

function bucketLabel(key, mode) {
  if (key === 'unknown') return 'unknown'
  return mode === 'hour' ? key.slice(5) : key
}

function rankBy(items, label, value) {
  const map = new Map()
  for (const item of items) {
    const key = label(item)
    map.set(key, (map.get(key) || 0) + Number(value(item) || 0))
  }
  return [...map.entries()]
    .map(([name, totalValue]) => ({ name, value: totalValue }))
    .sort((a, b) => b.value - a.value)
}

function rankUsageBy(items, label) {
  const map = new Map()
  for (const item of items) {
    const key = label(item)
    if (!map.has(key)) {
      map.set(key, {
        name: key,
        input_tokens: 0,
        output_tokens: 0,
        cached_tokens: 0,
        total_tokens: 0,
        cost: 0,
        currency: '',
        costs_by_currency: {},
        requests: 0,
      })
    }
    const entry = map.get(key)
    addUsage(entry, item)
    entry.requests += 1
  }
  return [...map.values()].sort((a, b) => b.total_tokens - a.total_tokens)
}

function segmentStyle(series, tokens, totalTokens) {
  return {
    height: `${Math.max(2, Math.round((tokens / Math.max(1, totalTokens)) * 100))}%`,
    background: groupColors.value[series] || '#536675',
  }
}

function barHeight(bucket) {
  return `${Math.max(8, Math.round((bucket.total_tokens / maxTokens.value) * 176))}px`
}

function chartX(index, count) {
  if (count <= 1) return AREA_PLOT_INSET
  return AREA_PLOT_INSET + (index / (count - 1)) * (CHART_W - (AREA_PLOT_INSET * 2))
}

function chartY(value, max) {
  return PLOT_BOTTOM - (value / Math.max(1, max)) * (PLOT_BOTTOM - PLOT_TOP)
}

function smoothPath(points) {
  if (!points.length) return ''
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`
  const parts = [`M ${points[0].x} ${points[0].y}`]
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[Math.max(0, i - 1)]
    const p1 = points[i]
    const p2 = points[i + 1]
    const p3 = points[Math.min(points.length - 1, i + 2)]
    const cp1x = p1.x + (p2.x - p0.x) / 6
    const cp1y = p1.y + (p2.y - p0.y) / 6
    const cp2x = p2.x - (p3.x - p1.x) / 6
    const cp2y = p2.y - (p3.y - p1.y) / 6
    parts.push(`C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p2.x} ${p2.y}`)
  }
  return parts.join(' ')
}

function areaPath(points) {
  if (!points.length) return ''
  return `${smoothPath(points)} L ${points[points.length - 1].x} ${PLOT_BOTTOM} L ${points[0].x} ${PLOT_BOTTOM} Z`
}

function placeTooltip(payload, event) {
  const page = pageRef.value
  if (!page || !event) {
    tooltipAnchor.value = { x: 16, y: 16 }
    tooltipPosition.value = { x: 16, y: 16 }
    hover.value = payload
    return
  }
  const bounds = page.getBoundingClientRect()
  tooltipAnchor.value = {
    x: event.clientX - bounds.left,
    y: event.clientY - bounds.top,
  }
  hover.value = payload
  requestAnimationFrame(updateTooltipPosition)
}

function updateTooltipPosition() {
  const page = pageRef.value
  const tooltip = tooltipRef.value
  if (!page || !tooltip) return
  const pageBounds = page.getBoundingClientRect()
  const tooltipBounds = tooltip.getBoundingClientRect()
  const margin = 12
  const gap = 16
  let x = tooltipAnchor.value.x + gap
  let y = tooltipAnchor.value.y + gap
  if (x + tooltipBounds.width > pageBounds.width - margin) {
    x = tooltipAnchor.value.x - tooltipBounds.width - gap
  }
  if (y + tooltipBounds.height > pageBounds.height - margin) {
    y = tooltipAnchor.value.y - tooltipBounds.height - gap
  }
  const maxX = Math.max(margin, pageBounds.width - tooltipBounds.width - margin)
  const maxY = Math.max(margin, pageBounds.height - tooltipBounds.height - margin)
  tooltipPosition.value = {
    x: Math.max(margin, Math.min(x, maxX)),
    y: Math.max(margin, Math.min(y, maxY)),
  }
}

function areaHoverPayload(point, event) {
  areaBucketHoverPayload(point.bucket, event)
}

function bucketHoverPayload(bucket, event) {
  activeBucketKey.value = bucket.key
  placeTooltip({
    kind: 'bucket',
    title: bucket.label,
    detail: `${fmt(bucket.total_tokens)} tokens · ${fmt(bucket.requests)} API calls${anomalyBucketKeys.value.has(bucket.key) ? ' · spike' : ''}`,
    rows: Object.values(bucket.models).sort((a, b) => b.total_tokens - a.total_tokens),
    cost: bucket.cost,
    costs_by_currency: bucket.costs_by_currency,
  }, event)
}

function areaBucketHoverPayload(bucket, event) {
  activeAreaKey.value = bucket.label
  placeTooltip({
    kind: 'area',
    title: bucket.label,
    detail: `${fmt(bucket.requests)} API calls · ${fmt(bucket.total_tokens)} tokens`,
    rows: Object.values(bucket.models).sort((a, b) => b.requests - a.requests),
  }, event)
}

function clearHover() {
  hover.value = null
  activeBucketKey.value = ''
  activeAreaKey.value = ''
}

function hitRate(item) {
  const input = Number(item.input_tokens || 0)
  return input ? Math.round((Number(item.cached_tokens || 0) / input) * 100) : 0
}

function tokenShare(item, key) {
  return `${Math.max(2, Math.round((Number(item[key] || 0) / Math.max(1, item.total_tokens || 0)) * 100))}%`
}

function clearFilters() {
  workspaceFilter.value = ''
  providerFilter.value = ''
  modelFilter.value = ''
  stageFilter.value = ''
}

watch(viewKey, () => {
  window.clearTimeout(reframeTimer)
  isReframing.value = true
  reframeTimer = window.setTimeout(() => { isReframing.value = false }, 260)
})
watch(() => props.workspace?.id, loadUsage)
onMounted(() => {
  loadUsage()
  scheduleRefresh()
})
onUnmounted(() => {
  window.clearInterval(refreshTimer)
  window.clearTimeout(reframeTimer)
})
</script>

<template>
  <div ref="pageRef" class="usage-page" :class="{ 'is-reframing': isReframing }">
    <header class="usage-page__top">
      <div>
        <h1>Usage</h1>
        <p>Track token spend and request volume across providers, models, and agent states.</p>
      </div>
      <div class="usage-page__summary">
        <span><small>tokens</small><b>{{ fmt(filteredTotal.total_tokens) }}</b></span>
        <span><small>requests</small><b>{{ fmt(requests) }}</b></span>
        <span><small>cache</small><b>{{ cacheRatio }}%</b></span>
        <span><small>cost</small><b>{{ formatCosts(filteredTotal) }}</b></span>
      </div>
    </header>

    <section class="usage-controls" aria-label="Usage filters">
      <div class="usage-controls__segmented">
        <button type="button" :class="{ 'is-active': precision === 'day' }" @click="precision = 'day'">30 days</button>
        <button type="button" :class="{ 'is-active': precision === 'hour' }" @click="precision = 'hour'">24 hours</button>
      </div>
      <div class="usage-controls__segmented" title="Chart color grouping">
        <button type="button" :class="{ 'is-active': groupMode === 'model' }" @click="groupMode = 'model'">By model</button>
        <button type="button" :class="{ 'is-active': groupMode === 'workspace' }" @click="groupMode = 'workspace'">By workspace</button>
      </div>
      <select v-model="workspaceFilter" title="Filter workspace">
        <option value="">All workspaces</option>
        <option v-for="workspace in workspaces" :key="workspace" :value="workspace">{{ workspaceOptionLabel(workspace) }}</option>
      </select>
      <select v-model="providerFilter" title="Filter provider">
        <option value="">All providers</option>
        <option v-for="provider in providers" :key="provider" :value="provider">{{ provider }}</option>
      </select>
      <select v-model="modelFilter" title="Filter model">
        <option value="">All models</option>
        <option v-for="model in models" :key="model" :value="model">{{ model }}</option>
      </select>
      <select v-model="stageFilter" title="Filter state">
        <option value="">All states</option>
        <option v-for="stage in stages" :key="stage" :value="stage">{{ stage }}</option>
      </select>
      <button type="button" class="usage-controls__clear" @click="clearFilters">Clear</button>
      <span v-if="loading" class="usage-controls__loading">Refreshing</span>
    </section>
    <section class="usage-scope" aria-label="Usage time window">
      <span>{{ chartScopeText }}</span>
      <span>{{ historyScopeText }}</span>
    </section>

    <p v-if="error" class="usage-page__error">{{ error }}</p>

    <section class="usage-grid">
      <article class="usage-panel usage-panel--tokens">
        <header>
          <div>
            <h2>Token volume</h2>
            <p>Stacked by {{ groupMode === 'workspace' ? 'workspace' : 'provider and model' }} in the selected chart window.</p>
          </div>
        </header>
        <div class="usage-chart-body">
          <div v-if="loading && !records.length" class="usage-empty">Loading usage...</div>
          <div v-else-if="!chartRecords.length" class="usage-empty">No token usage in this chart window.</div>
          <div v-else class="token-chart" :style="{ '--token-axis-w': `${TOKEN_AXIS_W}px` }">
            <div class="token-axis" aria-hidden="true">
              <span v-for="tick in yTicks" :key="tick.top" :style="{ top: tick.top }">{{ compactTokens(tick.value) }}</span>
            </div>
            <div class="token-bars">
              <span v-for="tick in yTicks" :key="`grid-${tick.top}`" class="token-bars__grid" :style="{ top: tick.top }"></span>
              <div
                v-for="bucket in buckets"
                :key="bucket.key"
                class="token-bars__item"
                :class="{ 'is-hovered': activeBucketKey === bucket.key }"
                @mouseenter="bucketHoverPayload(bucket, $event)"
                @mousemove="bucketHoverPayload(bucket, $event)"
                @mouseleave="clearHover"
              >
                <span v-if="anomalyBucketKeys.has(bucket.key)" class="token-bars__spike" title="Usage spike"></span>
                <div class="token-bars__value">{{ compactTokens(bucket.total_tokens) }}</div>
                <span class="token-bars__guide"></span>
                <div class="token-bars__bar" :style="{ height: barHeight(bucket) }">
                  <span
                    v-for="modelUsage in Object.values(bucket.models)"
                    :key="modelUsage.name"
                    :style="segmentStyle(modelUsage.name, modelUsage.total_tokens, bucket.total_tokens)"
                  />
                </div>
                <div class="token-bars__label">{{ bucket.label }}</div>
              </div>
            </div>
          </div>
        </div>
      </article>

      <article class="usage-panel usage-panel--models">
        <header>
          <div>
            <h2>Model requests</h2>
            <p>API call trend by {{ groupMode === 'workspace' ? 'workspace' : 'model' }} for the selected chart window.</p>
          </div>
        </header>
        <div class="usage-chart-body">
          <div v-if="!chartRecords.length" class="usage-empty">No model request data in this chart window.</div>
          <div v-else class="area-chart">
            <div class="area-legend" aria-label="Model legend">
              <span v-for="series in areaSeries" :key="series.name">
                <i :style="{ background: series.color }"></i>{{ series.name }}
              </span>
            </div>
            <svg :viewBox="`0 0 ${CHART_W} ${CHART_H}`" preserveAspectRatio="none" role="img" aria-label="Model request area chart">
              <line v-for="y in areaGuideLines" :key="y" :x1="AREA_PLOT_INSET" :y1="y" :x2="CHART_W - AREA_PLOT_INSET" :y2="y" class="area-chart__grid" />
              <g v-for="series in areaSeries" :key="series.name">
                <path class="area-chart__fill" :d="series.area" :fill="series.color" />
                <path class="area-chart__line" :d="series.line" :stroke="series.color" />
                <circle
                  v-for="point in series.points"
                  :key="`${series.name}-${point.label}`"
                  class="area-chart__dot"
                  :cx="point.x"
                  :cy="point.y"
                  r="6"
                  :fill="series.color"
                  @mouseenter="areaHoverPayload(point, $event)"
                  @mousemove="areaHoverPayload(point, $event)"
                  @mouseleave="clearHover"
                />
              </g>
              <g class="area-chart__zones">
                <template v-for="(bucket, index) in buckets" :key="bucket.key">
                  <line
                    v-if="activeAreaKey === bucket.label"
                    :x1="chartX(index, buckets.length)"
                    y1="18"
                    :x2="chartX(index, buckets.length)"
                    :y2="PLOT_BOTTOM"
                    class="area-chart__guide"
                  />
                  <rect
                    :x="chartX(index, buckets.length) - ((CHART_W - (AREA_PLOT_INSET * 2)) / Math.max(1, buckets.length - 1)) / 2"
                    y="0"
                    :width="Math.max(42, (CHART_W - (AREA_PLOT_INSET * 2)) / Math.max(1, buckets.length - 1))"
                    :height="CHART_H"
                    class="area-chart__zone"
                    @mouseenter="areaBucketHoverPayload(bucket, $event)"
                    @mousemove="areaBucketHoverPayload(bucket, $event)"
                    @mouseleave="clearHover"
                  />
                </template>
              </g>
            </svg>
          </div>
        </div>
      </article>
    </section>

    <section class="usage-diagnostics">
      <article class="diagnostic-card diagnostic-card--funnel">
        <header>
          <div>
            <h2>State funnel</h2>
            <p>Token pressure by state in the selected chart window.</p>
          </div>
          <span>{{ fmt(chartUsageTotal.total_tokens) }} tokens</span>
        </header>
        <div v-if="!stageFunnel.length" class="usage-empty usage-empty--compact">No state usage in this window.</div>
        <TransitionGroup v-else name="usage-list" tag="div" class="stage-funnel">
          <div v-for="item in stageFunnel" :key="item.name" class="stage-funnel__row">
            <div class="stage-funnel__meta">
              <span>{{ item.name }}</span>
              <b>{{ item.share }}%</b>
            </div>
            <div class="stage-funnel__track">
              <span :style="{ width: item.width, background: stageColors[item.name] || '#536675' }"></span>
            </div>
            <div class="stage-funnel__numbers">
              <span>{{ fmt(item.requests) }} calls</span>
              <span>{{ fmt(item.total_tokens) }} tokens</span>
              <span>{{ formatCosts(item) }}</span>
            </div>
          </div>
        </TransitionGroup>
      </article>

      <article class="diagnostic-card diagnostic-card--sessions">
        <header>
          <div>
            <h2>Costliest tasks</h2>
            <p>Sessions ranked by token usage in the selected chart window.</p>
          </div>
          <span>{{ fmt(sessionCosts.length) }} shown</span>
        </header>
        <div v-if="!sessionCosts.length" class="usage-empty usage-empty--compact">No task usage in this window.</div>
        <TransitionGroup v-else name="usage-list" tag="div" class="session-costs">
          <div v-for="item in sessionCosts" :key="item.name" class="session-cost">
            <div class="session-cost__top">
              <strong>{{ item.name }}</strong>
              <b>{{ formatCosts(item) }}</b>
            </div>
            <div class="session-cost__bar">
              <span class="is-input" :style="{ width: tokenShare(item, 'input_tokens') }"></span>
              <span class="is-output" :style="{ width: tokenShare(item, 'output_tokens') }"></span>
              <span class="is-cache" :style="{ width: tokenShare(item, 'cached_tokens') }"></span>
            </div>
            <div class="session-cost__metrics">
              <span>{{ fmt(item.total_tokens) }} tokens</span>
              <span>{{ fmt(item.requests) }} calls</span>
              <span>{{ fmt(item.average_tokens) }} avg</span>
              <span>{{ item.cache_rate }}% cache</span>
            </div>
          </div>
        </TransitionGroup>
      </article>
    </section>

    <section class="usage-breakdown">
      <article>
        <header>
          <h2>{{ groupMode === 'workspace' ? 'Workspaces' : 'Models' }}</h2>
          <span>{{ fmt(requests) }} requests</span>
        </header>
        <TransitionGroup name="usage-list" tag="div" class="rank-list">
          <div v-for="item in topGroups" :key="item.name" class="rank-row">
          <span class="rank-row__swatch" :style="{ background: groupColors[item.name] }"></span>
          <span>{{ item.name }}</span>
          <b>{{ fmt(item.value) }}</b>
          </div>
        </TransitionGroup>
      </article>
      <article>
        <header>
          <h2>States</h2>
          <span>{{ fmt(filteredTotal.total_tokens) }} tokens</span>
        </header>
        <TransitionGroup name="usage-list" tag="div" class="rank-list">
          <div v-for="item in stageBreakdown" :key="item.name" class="rank-row">
            <span class="rank-row__swatch" :style="{ background: stageColors[item.name] }"></span>
            <span>{{ item.name }}</span>
            <b>{{ fmt(item.value) }}</b>
          </div>
        </TransitionGroup>
      </article>
    </section>

    <Transition name="usage-tip">
      <div v-if="hover" ref="tooltipRef" class="usage-tooltip" :style="tooltipStyle">
        <div class="usage-tooltip__head">
          <strong>{{ hover.title }}</strong>
          <span>{{ hover.detail }}</span>
        </div>
        <div v-if="hover.rows?.length" class="usage-tooltip__rows">
          <div v-for="row in hover.rows" :key="row.name" class="usage-tooltip__row">
            <div class="usage-tooltip__row-top">
              <span class="usage-tooltip__swatch" :style="{ background: groupColors[row.name] || '#536675' }"></span>
              <span class="usage-tooltip__name">{{ row.name }}</span>
              <b>{{ fmt(row.requests) }}</b>
            </div>
            <div class="usage-tooltip__tokenbar" aria-label="Token composition">
              <span class="is-input" :style="{ width: tokenShare(row, 'input_tokens') }"></span>
              <span class="is-output" :style="{ width: tokenShare(row, 'output_tokens') }"></span>
              <span class="is-cache" :style="{ width: tokenShare(row, 'cached_tokens') }"></span>
            </div>
            <div class="usage-tooltip__metrics">
              <span><small>in</small>{{ fmt(row.input_tokens) }}</span>
              <span><small>out</small>{{ fmt(row.output_tokens) }}</span>
              <span><small>cache</small>{{ fmt(row.cached_tokens) }}</span>
              <span><small>hit</small>{{ hitRate(row) }}%</span>
              <span v-if="row.cost"><small>cost</small>{{ formatCosts(row) }}</span>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.usage-page {
  position: relative;
  display: flex;
  min-height: 100%;
  flex-direction: column;
  gap: 18px;
  padding: 28px clamp(18px, 4vw, 44px);
  color: var(--text);
}

.usage-page__top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
}

.usage-page__top h1 {
  margin: 0;
  color: var(--text-h);
  font-size: 28px;
  font-weight: 680;
  letter-spacing: 0;
}

.usage-page__top p,
.usage-panel p {
  margin: 6px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}

.usage-page__summary {
  display: grid;
  min-width: min(520px, 100%);
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.usage-page__summary span,
.usage-panel,
.diagnostic-card,
.usage-breakdown article {
  border: 1px solid rgba(15, 23, 42, .08);
  border-radius: 8px;
  background: #ffffff;
}

.usage-page__summary span {
  padding: 10px 12px;
}

.usage-page__summary small,
.usage-controls__loading {
  color: var(--text-muted);
  font: 700 9px/1 var(--mono);
  text-transform: uppercase;
}

.usage-page__summary b {
  display: block;
  overflow: hidden;
  margin-top: 5px;
  color: var(--text-h);
  font: 700 15px/1.2 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.usage-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.usage-controls__segmented {
  display: flex;
  overflow: hidden;
  border: 1px solid rgba(15, 23, 42, .12);
  border-radius: 6px;
  background: #ffffff;
}

.usage-controls button,
.usage-controls select {
  height: 30px;
  border: 1px solid rgba(15, 23, 42, .12);
  border-radius: 6px;
  background: #ffffff;
  color: var(--text);
  font: 11px var(--mono);
  transition: background-color 160ms ease, border-color 160ms ease, color 160ms ease, box-shadow 160ms ease;
}

.usage-controls__segmented button {
  border: 0;
  border-radius: 0;
  padding: 0 12px;
}

.usage-controls button {
  cursor: pointer;
}

.usage-controls select {
  min-width: 150px;
  padding: 0 8px;
}

.usage-controls select[title="Filter workspace"] {
  min-width: 190px;
}

.usage-controls .is-active {
  background: #22313f;
  color: #ffffff;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .12);
}

.usage-controls__clear {
  padding: 0 12px;
}

.usage-scope {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: -8px;
}

.usage-scope span {
  display: inline-flex;
  min-height: 26px;
  align-items: center;
  padding: 0 10px;
  border: 1px solid rgba(15, 23, 42, .08);
  border-radius: 999px;
  background: rgba(255, 255, 255, .72);
  color: var(--text-muted);
  font: 10px var(--mono);
}

.usage-page__error {
  margin: 0;
  color: var(--red);
  font-size: 12px;
}

.usage-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 18px;
}

.usage-scope,
.usage-grid,
.usage-diagnostics,
.usage-breakdown {
  transition: opacity 220ms ease, transform 260ms cubic-bezier(.2, .8, .2, 1), filter 260ms ease;
  will-change: opacity, transform;
}

.usage-page.is-reframing .usage-scope,
.usage-page.is-reframing .usage-grid,
.usage-page.is-reframing .usage-diagnostics,
.usage-page.is-reframing .usage-breakdown {
  opacity: .72;
  filter: saturate(.92);
  transform: translateY(4px);
}

.usage-panel {
  display: flex;
  min-width: 0;
  min-height: 340px;
  flex-direction: column;
  padding: 18px;
  box-shadow: 0 12px 30px rgba(15, 23, 42, .045);
}

.usage-panel header,
.usage-breakdown header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.usage-panel h2,
.usage-breakdown h2 {
  margin: 0;
  color: var(--text-h);
  font-size: 15px;
  font-weight: 680;
}

.usage-empty {
  display: grid;
  min-height: 244px;
  place-items: center;
  color: var(--text-muted);
  font-size: 13px;
}

.usage-chart-body {
  position: relative;
  display: flex;
  min-height: 268px;
  flex: 1;
  align-items: stretch;
  margin-top: 16px;
  overflow: hidden;
  border: 1px solid rgba(15, 23, 42, .055);
  border-radius: 8px;
  background:
    radial-gradient(circle at 16% 14%, rgba(45, 127, 117, .08), transparent 26%),
    linear-gradient(180deg, rgba(248, 251, 252, .98), #ffffff);
}

.token-chart {
  display: grid;
  width: 100%;
  grid-template-columns: var(--token-axis-w, 62px) minmax(0, 1fr);
}

.token-axis {
  position: relative;
  border-right: 1px solid rgba(15, 23, 42, .075);
  background:
    linear-gradient(90deg, rgba(255, 255, 255, .86), rgba(248, 251, 252, .62)),
    rgba(255, 255, 255, .54);
  box-shadow: inset -10px 0 18px rgba(15, 23, 42, .025);
}

.token-axis span {
  position: absolute;
  right: 12px;
  left: 8px;
  transform: translateY(-50%);
  color: #5d7290;
  font: 700 9px/1 var(--mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
  white-space: nowrap;
}

.token-bars {
  position: relative;
  display: flex;
  min-height: 268px;
  align-items: flex-end;
  gap: clamp(6px, 1vw, 12px);
  overflow-x: hidden;
  padding: 18px 18px 10px;
}

.token-bars__grid {
  position: absolute;
  right: 0;
  left: 0;
  height: 1px;
  background: linear-gradient(90deg, rgba(15, 23, 42, .07), rgba(15, 23, 42, .02));
  pointer-events: none;
}

.token-bars__item {
  position: relative;
  display: grid;
  min-width: 28px;
  flex: 1 1 44px;
  grid-template-rows: 18px 190px 22px;
  align-items: end;
  justify-items: center;
}

.token-bars__value,
.token-bars__label {
  overflow: hidden;
  max-width: 62px;
  color: var(--text-muted);
  font: 9px/1.2 var(--mono);
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.token-bars__value {
  color: #516987;
  font-weight: 700;
}

.token-bars__guide {
  position: absolute;
  top: 18px;
  bottom: 22px;
  left: 50%;
  display: none;
  z-index: 2;
  border-left: 1px dashed rgba(15, 23, 42, .42);
  pointer-events: none;
}

.token-bars__spike {
  position: absolute;
  top: 8px;
  left: 50%;
  z-index: 3;
  width: 7px;
  height: 7px;
  border: 1px solid #ffffff;
  border-radius: 50%;
  background: #c83e3e;
  box-shadow: 0 0 0 3px rgba(200, 62, 62, .14);
  transform: translateX(-50%);
  transition: opacity 180ms ease, transform 220ms ease;
}

.token-bars__spike::after {
  position: absolute;
  top: 8px;
  left: 50%;
  height: 182px;
  border-left: 1px solid rgba(200, 62, 62, .28);
  content: "";
}

.token-bars__item.is-hovered .token-bars__guide {
  display: block;
}

.token-bars__bar {
  display: flex;
  width: min(34px, 72%);
  min-height: 8px;
  flex-direction: column-reverse;
  overflow: hidden;
  border-radius: 6px 6px 2px 2px;
  background: rgba(15, 23, 42, .05);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .42);
  transition: height 360ms cubic-bezier(.2, .8, .2, 1), box-shadow 180ms ease;
  will-change: height;
}

.token-bars__bar span {
  display: block;
  width: 100%;
  min-height: 2px;
  transition: height 360ms cubic-bezier(.2, .8, .2, 1), background-color 180ms ease, opacity 180ms ease;
}

.area-chart {
  width: 100%;
  overflow-x: hidden;
  padding: 10px 0 0;
}

.area-legend {
  display: flex;
  min-height: 22px;
  flex-wrap: wrap;
  gap: 6px 12px;
  align-items: center;
  padding: 0 12px;
}

.area-legend span {
  display: inline-flex;
  min-width: 0;
  max-width: 210px;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  color: var(--text-muted);
  font: 10px var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.area-legend i {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  border-radius: 50%;
}

.area-chart svg {
  display: block;
  width: 100%;
  height: 238px;
  transition: opacity 220ms ease, transform 260ms cubic-bezier(.2, .8, .2, 1);
}

.area-chart__grid {
  stroke: rgba(15, 23, 42, .08);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

.area-chart__fill {
  opacity: .11;
  transition: opacity 180ms ease, fill 180ms ease;
}

.area-chart__line {
  fill: none;
  stroke-width: 2.6;
  stroke-linecap: round;
  stroke-linejoin: round;
  filter: drop-shadow(0 1px 1px rgba(15, 23, 42, .08));
  transition: opacity 180ms ease, stroke 180ms ease, filter 180ms ease;
  vector-effect: non-scaling-stroke;
}

.area-chart__dot {
  cursor: crosshair;
  opacity: 0;
  stroke: #ffffff;
  stroke-width: 2;
  transition: opacity 120ms ease;
  vector-effect: non-scaling-stroke;
}

.area-chart g:hover .area-chart__fill {
  opacity: .22;
}

.area-chart g:hover .area-chart__dot {
  opacity: .95;
}

.usage-page.is-reframing .area-chart svg {
  opacity: .78;
  transform: translateY(3px);
}

.area-chart__guide {
  stroke: rgba(15, 23, 42, .42);
  stroke-dasharray: 5 5;
  stroke-width: 1.2;
  pointer-events: none;
  vector-effect: non-scaling-stroke;
}

.area-chart__zone {
  fill: transparent;
  cursor: crosshair;
  pointer-events: all;
}

.usage-diagnostics {
  display: grid;
  grid-template-columns: minmax(0, .95fr) minmax(0, 1.25fr);
  gap: 14px;
}

.diagnostic-card {
  min-width: 0;
  padding: 15px;
  background: #ffffff;
  box-shadow: 0 10px 24px rgba(15, 23, 42, .035);
}

.diagnostic-card header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.diagnostic-card h2 {
  margin: 0;
  color: var(--text-h);
  font-size: 14px;
  font-weight: 680;
}

.diagnostic-card p {
  margin: 5px 0 0;
  color: var(--text-muted);
  font-size: 12px;
}

.diagnostic-card header > span {
  flex: 0 0 auto;
  color: var(--text-muted);
  font: 10px var(--mono);
}

.usage-empty--compact {
  min-height: 166px;
}

.stage-funnel,
.session-costs {
  position: relative;
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.stage-funnel__row {
  display: grid;
  gap: 6px;
}

.stage-funnel__meta,
.stage-funnel__numbers,
.session-cost__top,
.session-cost__metrics {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.stage-funnel__meta span,
.session-cost__top strong {
  overflow: hidden;
  color: var(--text);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-funnel__meta b,
.session-cost__top b {
  color: var(--text-h);
  font: 700 11px var(--mono);
}

.stage-funnel__track,
.session-cost__bar {
  overflow: hidden;
  height: 8px;
  border-radius: 999px;
  background: rgba(15, 23, 42, .055);
}

.stage-funnel__track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  transition: width 360ms cubic-bezier(.2, .8, .2, 1), background-color 180ms ease;
  will-change: width;
}

.stage-funnel__numbers,
.session-cost__metrics {
  justify-content: flex-start;
  flex-wrap: wrap;
  color: var(--text-muted);
  font: 10px var(--mono);
}

.session-cost {
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid rgba(15, 23, 42, .06);
  border-radius: 7px;
  background: rgba(248, 251, 252, .72);
  transition: background-color 160ms ease, border-color 160ms ease, opacity 220ms ease, transform 260ms cubic-bezier(.2, .8, .2, 1);
}

.session-cost__bar {
  display: flex;
}

.session-cost__bar span {
  min-width: 2px;
  transition: width 360ms cubic-bezier(.2, .8, .2, 1), background-color 180ms ease;
}

.session-cost__bar .is-input { background: #2d7f75; }
.session-cost__bar .is-output { background: #d36b3d; }
.session-cost__bar .is-cache { background: #5e63c8; }

.usage-list-move,
.usage-list-enter-active,
.usage-list-leave-active {
  transition: opacity 220ms ease, transform 260ms cubic-bezier(.2, .8, .2, 1);
}

.usage-list-enter-from,
.usage-list-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

.usage-list-leave-active {
  position: absolute;
  width: 100%;
}

.usage-breakdown {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.usage-breakdown article {
  padding: 14px;
}

.usage-breakdown header span {
  color: var(--text-muted);
  font: 10px var(--mono);
}

.rank-list {
  position: relative;
}

.rank-row {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
  padding: 9px 0;
  border-top: 1px solid rgba(15, 23, 42, .06);
  font-size: 12px;
  transition: opacity 220ms ease, transform 260ms cubic-bezier(.2, .8, .2, 1);
}

.rank-row:first-of-type {
  margin-top: 8px;
}

.rank-row span:nth-child(2) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rank-row b {
  font: 700 11px var(--mono);
}

.rank-row__swatch {
  width: 10px;
  height: 10px;
  border-radius: 3px;
}

.usage-tooltip {
  position: absolute;
  z-index: 30;
  display: grid;
  width: min(440px, calc(100% - 24px));
  max-height: min(430px, calc(100% - 24px));
  gap: 8px;
  overflow: hidden;
  padding: 12px;
  border: 1px solid rgba(15, 23, 42, .12);
  border-radius: 9px;
  background: rgba(255, 255, 255, .96);
  box-shadow: 0 18px 46px rgba(15, 23, 42, .16);
  color: var(--text);
  pointer-events: none;
  backdrop-filter: blur(10px);
}

.usage-tooltip__head {
  display: grid;
  gap: 4px;
}

.usage-tooltip strong {
  color: var(--text-h);
  font-size: 12px;
}

.usage-tooltip span {
  color: var(--text-muted);
  font: 11px var(--mono);
}

.usage-tooltip__rows {
  display: grid;
  gap: 8px;
  overflow-y: auto;
  padding-top: 8px;
  border-top: 1px solid rgba(15, 23, 42, .08);
}

.usage-tooltip__row {
  display: grid;
  gap: 6px;
  min-width: 0;
  padding: 8px;
  border: 1px solid rgba(15, 23, 42, .06);
  border-radius: 7px;
  background: rgba(248, 251, 252, .8);
}

.usage-tooltip__row-top {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  color: var(--text-muted);
  font: 10px var(--mono);
}

.usage-tooltip__row-top b {
  color: var(--text-h);
  font: 700 11px var(--mono);
}

.usage-tooltip__swatch {
  width: 10px;
  height: 10px;
  border-radius: 3px;
}

.usage-tooltip__name {
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.usage-tooltip__tokenbar {
  display: flex;
  overflow: hidden;
  height: 6px;
  border-radius: 999px;
  background: rgba(15, 23, 42, .06);
}

.usage-tooltip__tokenbar span {
  min-width: 2px;
}

.usage-tooltip__tokenbar .is-input { background: #2d7f75; }
.usage-tooltip__tokenbar .is-output { background: #d36b3d; }
.usage-tooltip__tokenbar .is-cache { background: #5e63c8; }

.usage-tooltip__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.usage-tooltip__metrics span {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  max-width: 100%;
  padding: 3px 6px;
  border-radius: 999px;
  background: #ffffff;
  color: var(--text);
  font: 10px var(--mono);
}

.usage-tooltip__metrics small {
  color: var(--text-muted);
  font: 700 8px var(--mono);
  text-transform: uppercase;
}

.usage-tip-enter-active,
.usage-tip-leave-active {
  transition: opacity 120ms ease, transform 120ms ease;
}

.usage-tip-enter-from,
.usage-tip-leave-to {
  opacity: 0;
  transform: translateY(4px);
}

@media (max-width: 1040px) {
  .usage-page__top,
  .usage-grid,
  .usage-diagnostics,
  .usage-breakdown {
    grid-template-columns: 1fr;
  }

  .usage-page__top {
    align-items: flex-start;
    flex-direction: column;
  }
}

@media (max-width: 760px) {
  .token-chart {
    grid-template-columns: min(54px, var(--token-axis-w, 62px)) minmax(0, 1fr);
  }

  .usage-panel {
    padding: 14px;
  }
}

@media (max-width: 720px) {
  .usage-page__summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (prefers-reduced-motion: reduce) {
  .usage-page *,
  .usage-page *::before,
  .usage-page *::after {
    animation-duration: .01ms !important;
    transition-duration: .01ms !important;
  }
}
</style>
