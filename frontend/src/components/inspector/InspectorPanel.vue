<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { gsap } from 'gsap'
import { animate, stagger } from 'animejs'
import GitPanel from './GitPanel.vue'
import TerminalPanel from './TerminalPanel.vue'

const props = defineProps({
  tab: { type: String, default: 'evidence' },
  run: { type: Object, required: true },
  runs: { type: Array, default: () => [] },
  taskAnalyses: { type: Array, default: () => [] },
  usage: { type: Object, default: () => ({}) },
  todos: { type: Array, required: true },
  tools: { type: Array, required: true },
  mcpServers: { type: Array, default: () => [] },
  subagents: { type: Array, default: () => [] },
  width: { type: Number, default: 392 },
  tabs: { type: Array, default: () => [] },
  workspaceKey: { type: [String, Number], default: '' },
})
const emit = defineEmits([
  'close',
  'resize',
])

const root = ref(null)
const confidenceBar = ref(null)
const copyTaskStatus = ref('')
let copyTaskTimer
let stopPanelResize
const percent = computed(() => Math.round((props.run.confidence ?? .5) * 100))
const supportCount = computed(() => props.run.evidence.filter(item => item.stance === 'support').length)
const opposeCount = computed(() => props.run.evidence.filter(item => item.stance === 'oppose').length)
const visibleRuns = computed(() => props.runs.length ? props.runs : [props.run])
const phases = [
  ['support', 'Support'],
  ['oppose', 'Oppose'],
  ['audit', 'Audit'],
  ['evaluate', 'Evaluate'],
]
const fallbackTabs = [
  {
    id: 'evidence',
    label: 'Evidence',
    icon: '◎',
    color: '#1756d1',
    soft: '#e8f0ff',
    description: 'Hypotheses, supporting facts, and verdicts for this run.',
  },
]
const taskGroupKinds = ['goal', 'work', 'acceptance', 'behavior', 'boundary', 'constraint', 'hypothesis', 'unknown']
const taskKindLabels = {
  goal: 'Goals',
  work: 'Work',
  acceptance: 'Acceptance criteria',
  behavior: 'Behavior contract',
  boundary: 'Boundaries',
  constraint: 'Constraints',
  hypothesis: 'Hypotheses',
  unknown: 'Unknowns',
}
const activeTaskAnalysis = computed(() => {
  const arr = props.taskAnalyses || []
  return arr.length ? arr[arr.length - 1] : null
})
const panelTabs = computed(() => props.tabs.length ? props.tabs : fallbackTabs)
const activeTab = computed(() => panelTabs.value.find(item => item.id === props.tab) || panelTabs.value[0])
const panelStyle = computed(() => ({
  '--inspector-width': `${props.width}px`,
  '--inspector-accent': activeTab.value?.color || '#1756d1',
  '--inspector-accent-soft': activeTab.value?.soft || '#e8f0ff',
}))
const activeDescription = computed(() => activeTab.value?.description || 'Details for the current session.')

function analysisRowsFor(analysis) {
  if (!analysis) return []
  const updates = Array.isArray(analysis.task_updates) ? analysis.task_updates : []
  if (updates.length) return dedupeTaskRows([
    ...updates.filter(item => item?.text && item.kind !== 'clue'),
    ...missingUnknownRows(analysis, updates),
  ])
  const rows = [
    { kind: 'goal', text: analysis.intent?.summary, status: 'goal' },
    ...(analysis.acceptance_criteria || []).map(item => ({ id: item.id, kind: 'acceptance', text: item.text, status: 'pending' })),
    ...behaviorTaskRows(analysis),
    ...(analysis.constraints || []).map(text => ({ kind: 'constraint', text, status: 'constraint' })),
    ...(analysis.unknowns || []).map(item => ({
      id: item.id,
      kind: 'unknown',
      text: typeof item === 'string' ? item : item.question || item.text,
      status: item.blocking === false ? 'deferred' : 'unknown',
      answers: item.answers,
    })),
  ].filter(item => item.text)
  return dedupeTaskRows(rows)
}
function remainingTaskCountFor(analysis) {
  const rows = analysisRowsFor(analysis)
  return rows.filter(item =>
    ['unknown', 'blocked', 'added', 'updated'].includes(item.status || '')
  ).length
}

function taskProgressFor(analysis) {
  const rows = analysisRowsFor(analysis)
  const investigationRows = rows.filter(item => item.kind === 'unknown')
  const completed = investigationRows.filter(item => ['known', 'deferred'].includes(item.status || '')).length
  const total = investigationRows.length
  const percent = total ? Math.round((completed / total) * 100) : 0
  return { completed, total, percent }
}

function groupedTasksFor(analysis) {
  const rows = analysisRowsFor(analysis)
  const groups = Object.fromEntries(taskGroupKinds.map(kind => [kind, []]))
  for (const row of rows) {
    const kind = row.kind || 'unknown'
    if (groups[kind]) groups[kind].push(row)
    else groups.unknown.push(row)
  }
  return groups
}

function taskKindLabel(kind) {
  return taskKindLabels[kind] || kind
}

function dedupeTaskRows(rows) {
  const seen = new Set()
  return rows.filter(row => {
    const key = row.id || `${row.kind || 'unknown'}:${row.text}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function missingUnknownRows(analysis, rows) {
  const present = new Set(rows.map(row => String(row.id || '').split(':').pop()))
  return (analysis.unknowns || [])
    .filter(item => item?.id && !present.has(item.id))
    .map(item => ({
      id: `${analysis.id || 'task'}:${item.id}`,
      kind: 'unknown',
      text: item.question || item.text,
      status: item.blocking === false ? 'deferred' : 'unknown',
      answers: item.answers,
    }))
    .filter(item => item.text)
}

function taskCopyPayload(analysis) {
  const { task_updates, clues, ...task } = analysis || {}
  return {
    copied_at: new Date().toISOString(),
    task,
    rows: analysisRowsFor(analysis),
    clue_count: Array.isArray(clues) ? clues.length : 0,
  }
}

async function writeClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.left = '-9999px'
  area.style.top = '0'
  document.body.appendChild(area)
  area.select()
  try {
    document.execCommand('copy')
  } finally {
    document.body.removeChild(area)
  }
}

async function copyCurrentTask() {
  const analysis = activeTaskAnalysis.value
  if (!analysis) return
  clearTimeout(copyTaskTimer)
  try {
    await writeClipboard(JSON.stringify(taskCopyPayload(analysis), null, 2))
    copyTaskStatus.value = 'Copied'
  } catch {
    copyTaskStatus.value = 'Copy failed'
  } finally {
    copyTaskTimer = setTimeout(() => { copyTaskStatus.value = '' }, 1800)
  }
}

function taskStatusIcon(status) {
  if (status === 'known' || status === 'added' || status === 'updated') return 'check'
  if (status === 'blocked') return 'blocked'
  if (status === 'deferred') return 'deferred'
  if (status === 'unknown') return 'unknown'
  return 'pending'
}

function behaviorTaskRows(analysis) {
  const behavior = analysis.behavior_contract || {}
  const groups = [
    ['inputs', 'behavior'],
    ['outputs', 'behavior'],
    ['success_behaviors', 'behavior'],
    ['failure_behaviors', 'behavior'],
    ['boundaries', 'boundary'],
  ]
  return groups.flatMap(([key, kind]) => (behavior[key] || []).map((text, index) => ({
    id: `behavior:${key}:${index + 1}`,
    kind,
    text,
    status: 'pending',
  })))
}

function taskAnswers(value) {
  if (!Array.isArray(value)) return []
  return value.map(answer => typeof answer === 'object' && answer !== null ? answer : { text: answer })
    .map(answer => ({
      source: String(answer.source || 'investigation'),
      text: String(answer.text || answer.answer || '').trim(),
      reason: String(answer.reason || ''),
      trace: Array.isArray(answer.trace) ? answer.trace.map(String) : [],
    }))
    .filter(answer => answer.text)
}

function clampPanelWidth(value) {
  const max = Math.min(640, window.innerWidth - 40)
  return Math.min(max, Math.max(320, value))
}

function startResize(event) {
  event.preventDefault()
  const move = (moveEvent) => {
    emit('resize', clampPanelWidth(window.innerWidth - moveEvent.clientX))
  }
  const up = () => {
    stopPanelResize?.()
    stopPanelResize = null
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up, { once: true })
  stopPanelResize = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
}

onUnmounted(() => {
  clearTimeout(copyTaskTimer)
  stopPanelResize?.()
})

watch(percent, (value, previous = 50) => {
  if (!confidenceBar.value) return
  animate(confidenceBar.value, {
    scaleX: [previous / 100, value / 100],
    duration: 680,
    ease: 'outElastic(1, .65)',
  })
})

watch(() => props.run.evidence.length, async () => {
  await nextTick()
  const cards = root.value?.querySelectorAll('.evidence-card')
  const last = cards?.[cards.length - 1]
  if (last) animate(last, { translateY: [12, 0], scale: [.97, 1], opacity: [0, 1], duration: 420, ease: 'outBack' })
})

watch(() => props.tab, async () => {
  await nextTick()
  const children = root.value?.querySelectorAll('.inspector__content > *')
  if (children?.length) animate(children, { translateY: [6, 0], opacity: [0, 1], delay: stagger(24), duration: 260, ease: 'outCubic' })
})

watch(() => props.runs.length, (newLen, oldLen) => {
  if (newLen <= oldLen || !props.runs.length) return
  props.runs.forEach((itemRun, i) => { itemRun.open = i === props.runs.length - 1 })
})

function toggleHypothesis(itemRun) {
  itemRun.open = !itemRun.open
}

function toggleTask(task) {
  task.open = !task.open
}

watch(() => props.taskAnalyses.length, (newLen, oldLen) => {
  if (newLen <= oldLen || !props.taskAnalyses.length) return
  props.taskAnalyses.forEach((task, i) => { task.open = i === props.taskAnalyses.length - 1 })
})

function taskEnter(el, done) {
  el.style.overflow = 'hidden'
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    done()
    return
  }
  gsap.fromTo(el, { height: 0, autoAlpha: 0 }, {
    height: el.scrollHeight,
    autoAlpha: 1,
    duration: 0.38,
    ease: 'power3.out',
    onComplete: () => {
      gsap.set(el, { height: 'auto', clearProps: 'overflow' })
      done()
    },
  })
}

function taskLeave(el, done) {
  el.style.overflow = 'hidden'
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    done()
    return
  }
  gsap.to(el, {
    height: 0,
    autoAlpha: 0,
    duration: 0.28,
    ease: 'power2.inOut',
    onComplete: done,
  })
}

function hypothesisEnter(el, done) {
  el.style.overflow = 'hidden'
  gsap.fromTo(el, { height: 0, autoAlpha: 0 }, {
    height: el.scrollHeight,
    autoAlpha: 1,
    duration: .38,
    ease: 'power3.out',
    onComplete: () => {
      gsap.set(el, { height: 'auto', clearProps: 'overflow' })
      done()
    },
  })
  const cards = el.querySelectorAll('.evidence-card, .confidence-card, .verdict-card, .relation-list')
  if (cards.length) {
    animate(cards, {
      translateY: [14, 0],
      opacity: [0, 1],
      delay: stagger(35, { from: 'first' }),
      duration: 360,
      ease: 'outCubic',
    })
  }
}

function hypothesisLeave(el, done) {
  el.style.overflow = 'hidden'
  const cards = el.querySelectorAll('.evidence-card, .confidence-card, .verdict-card, .relation-list')
  if (cards.length) {
    animate(cards, {
      translateY: [0, -8],
      opacity: [1, 0],
      delay: stagger(18, { from: 'last' }),
      duration: 200,
      ease: 'inCubic',
    })
  }
  gsap.fromTo(el, { height: el.scrollHeight, autoAlpha: 1 }, {
    height: 0,
    autoAlpha: 0,
    duration: .3,
    ease: 'power2.inOut',
    delay: cards.length ? .12 : 0,
    onComplete: done,
  })
}

function countEvidence(run, stance) {
  return run.evidence.filter(item => item.stance === stance).length
}

function runResultClass(itemRun) {
  if (itemRun.verdict) return `is-${itemRun.verdict.verdict}`
  return itemRun.status === 'running' ? 'is-running' : ''
}

function serverClass(status) {
  if (status === 'running') return 'running'
  if (status === 'error') return 'error'
  return 'idle'
}

function onRowEnter(el) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  gsap.to(el, { scale: 1.012, backgroundColor: '#f0f5ff', borderColor: '#a3c2ef', duration: 0.16, ease: 'power2.out', overwrite: 'auto' })
}

function onRowLeave(el) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  gsap.to(el, { scale: 1, backgroundColor: '#ffffff', borderColor: '#d7e2ef', duration: 0.2, ease: 'power2.out', overwrite: 'auto' })
}

</script>

<template>
  <aside ref="root" class="inspector" :style="panelStyle">
    <button class="inspector__resize" type="button" aria-label="Resize inspector" @pointerdown="startResize"></button>
    <header class="inspector__head">
      <div class="inspector__title">
        <span class="inspector__mark">{{ activeTab.icon }}</span>
        <div class="inspector__title-copy">
          <strong>{{ activeTab.label }}</strong>
          <small>{{ activeDescription }}</small>
        </div>
      </div>
      <button class="inspector__close" type="button" aria-label="Close inspector" @click="emit('close')">×</button>
    </header>

    <div class="inspector__content">
      <template v-if="tab === 'evidence'">
        <section class="usage-card">
          <span>Session usage</span>
          <strong>{{ usage.currency || 'USD' }} {{ Number(usage.cost || 0).toFixed(6) }}</strong>
          <p>↑ {{ usage.input_tokens || 0 }} · ↓ {{ usage.output_tokens || 0 }} · cache {{ usage.cached_tokens || 0 }}</p>
        </section>

        <div
          v-for="itemRun in visibleRuns"
          v-show="runs.length && itemRun.hypothesis"
          :key="itemRun.id"
          class="hypothesis-row"
          :class="[runResultClass(itemRun), { 'is-open': itemRun.open }]"
        >
          <button class="hypothesis-row__summary" @click="toggleHypothesis(itemRun)">
            <span>{{ itemRun.hypothesis }}</span>
            <span class="hypothesis-row__meta">
              <b>{{ Math.round((itemRun.confidence ?? .5) * 100) }}%</b>
              <i class="hypothesis-row__chevron"></i>
            </span>
          </button>
          <Transition @enter="hypothesisEnter" @leave="hypothesisLeave">
            <div v-show="itemRun.open" class="hypothesis-row__body">
              <section class="confidence-card" :class="`is-${itemRun.status}`">
                <div class="confidence-card__top">
                  <span>Hypothesis confidence</span>
                  <strong>{{ Math.round((itemRun.confidence ?? .5) * 100) }}%</strong>
                </div>
                <div class="confidence-card__track"><i :style="{ transform: `scaleX(${itemRun.confidence ?? .5})` }"></i></div>
                <p>{{ itemRun.hypothesis }}</p>
                <div class="confidence-card__counts">
                  <span class="support">+{{ countEvidence(itemRun, 'support') }} support</span>
                  <span class="oppose">−{{ countEvidence(itemRun, 'oppose') }} oppose</span>
                </div>
              </section>
              <article v-for="item in itemRun.evidence" :key="item.id" class="evidence-card" :class="`is-${item.stance}`">
                <span class="evidence-card__sign">{{ item.stance === 'support' ? '+' : '−' }}</span>
                <div>
                  <strong>{{ item.claim }}</strong>
                  <small>{{ item.source_uri }}</small>
                  <p>{{ item.excerpt }}</p>
                </div>
                <b>{{ Math.round(item.strength * 100) }}</b>
              </article>
              <section v-if="itemRun.verdict" class="verdict-card" :class="`is-${itemRun.verdict.verdict}`">
                <small>Verdict</small>
                <strong>{{ itemRun.verdict.verdict }}</strong>
                <p>{{ itemRun.verdict.summary }}</p>
              </section>
            </div>
          </Transition>
        </div>

        <section v-if="!runs.length" class="confidence-card" :class="`is-${run.status}`">
          <div class="confidence-card__top">
            <span>Hypothesis confidence</span>
            <strong>{{ percent }}%</strong>
          </div>
          <div class="confidence-card__track"><i ref="confidenceBar"></i></div>
          <p>{{ run.hypothesis || 'Send a hypothesis to begin gathering evidence.' }}</p>
          <div class="confidence-card__counts">
            <span class="support">+{{ supportCount }} support</span>
            <span class="oppose">−{{ opposeCount }} oppose</span>
          </div>
          <div class="phase-strip">
            <span
              v-for="[key, label] in phases"
              :key="key"
              :class="{ active: run.phase === key }"
            >{{ label }}</span>
          </div>
        </section>

        <TransitionGroup v-if="!runs.length" name="evidence-list" tag="div" class="evidence-list">
          <article v-for="item in run.evidence" :key="item.id" class="evidence-card" :class="`is-${item.stance}`">
            <span class="evidence-card__sign">{{ item.stance === 'support' ? '+' : '−' }}</span>
            <div>
              <strong>{{ item.claim }}</strong>
              <small>{{ item.source_uri }}</small>
              <p>{{ item.excerpt }}</p>
            </div>
            <b>{{ Math.round(item.strength * 100) }}</b>
          </article>
        </TransitionGroup>

        <section v-if="!runs.length && run.relations.length" class="relation-list">
          <strong>Relationships</strong>
          <div v-for="(edge, index) in run.relations" :key="`${edge.source_id}-${edge.target_id}-${index}`">
            <span>{{ edge.source_id }}</span>
            <i>{{ edge.relation }}</i>
            <span>{{ edge.target_id }}</span>
          </div>
        </section>

        <section v-if="!runs.length && run.verdict" class="verdict-card" :class="`is-${run.verdict.verdict}`">
          <small>Verdict</small>
          <strong>{{ run.verdict.verdict }}</strong>
          <p>{{ run.verdict.summary }}</p>
        </section>
      </template>

      <template v-else-if="tab === 'terminal'">
        <TerminalPanel />
      </template>

      <template v-else-if="tab === 'git'">
        <GitPanel :workspace-key="workspaceKey" />
      </template>

      <template v-else-if="tab === 'mcp'">
        <div class="inspector__section-head"><strong>MCP</strong><span>{{ mcpServers.length }} configured</span></div>
        <article v-for="server in mcpServers" :key="server.id" class="mcp-row" :class="serverClass(server.status)">
          <div class="mcp-row__main">
            <i></i>
            <span><strong>{{ server.name }}</strong><small>{{ server.status }} · {{ server.status_message || 'not started' }}</small></span>
            <b>{{ server.tools?.length || 0 }}</b>
          </div>
          <div class="mcp-row__tools">
            <span v-for="tool in server.tools" :key="tool.name">{{ tool.name }}</span>
          </div>
        </article>
        <p v-if="!mcpServers.length" class="workspace-note">Open the MCP page from the left sidebar to install a server.</p>
      </template>

      <template v-else-if="tab === 'subagents'">
        <div class="inspector__section-head"><strong>Subagents</strong><span>{{ subagents.length }} agents</span></div>
        <article v-for="agent in subagents" :key="agent.id || agent.name + agent.task" class="subagent-row" :class="agent.status">
          <span class="subagent-row__dot"></span>
          <div class="subagent-row__body">
            <div class="subagent-row__head">
              <strong>{{ agent.name }}</strong>
              <span class="subagent-row__state">{{ agent.status }}</span>
            </div>
            <small>{{ agent.task }}</small>
            <p v-if="agent.result">{{ agent.result }}</p>
          </div>
        </article>
        <p v-if="!subagents.length" class="workspace-note">Subagent activity will appear here when a run delegates work.</p>
      </template>

      <template v-else-if="tab === 'tasks'">
        <div class="inspector__section-head">
          <strong>Tasks</strong>
          <div class="inspector__section-actions">
            <span>{{ taskAnalyses.length }} task(s)</span>
            <button type="button" class="section-action" :disabled="!activeTaskAnalysis" @click="copyCurrentTask">
              {{ copyTaskStatus || 'Copy task' }}
            </button>
          </div>
        </div>

        <div v-for="(task, taskIdx) in taskAnalyses" :key="task.id || taskIdx" class="task-block">
          <button class="task-block__summary" @click="toggleTask(task)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>
            <span class="task-block__title">{{ task.intent?.summary || 'Task ' + (taskIdx + 1) }}</span>
            <span class="task-block__meta">
              <span v-if="taskProgressFor(task).total" class="task-block__progress" :title="`${taskProgressFor(task).completed}/${taskProgressFor(task).total} unknowns resolved`">
                <i :style="{ width: taskProgressFor(task).percent + '%' }"></i>
              </span>
              <small class="task-block__count">{{ taskProgressFor(task).completed }}/{{ taskProgressFor(task).total }}</small>
              <small class="task-block__type">{{ task.intent?.type || 'other' }}</small>
              <i class="task-block__chevron"></i>
            </span>
          </button>

          <Transition appear @enter="taskEnter" @leave="taskLeave">
            <div v-show="task.open" class="task-block__body">
            <div v-if="taskProgressFor(task).total" class="tk-progress">
              <div class="tk-progress-bar"><i :style="{ width: taskProgressFor(task).percent + '%' }"></i></div>
              <span>{{ taskProgressFor(task).completed }}/{{ taskProgressFor(task).total }} unknowns resolved · {{ taskProgressFor(task).percent }}%</span>
            </div>

            <template v-if="analysisRowsFor(task).length">
              <div v-for="(items, kind) in groupedTasksFor(task)" :key="kind">
                <div v-if="items.length" class="tk-group">
                  <div class="tk-group-head">
                    <span class="tk-group-icon" :class="`tk-group-icon--${kind}`">
                      <svg v-if="kind === 'goal'" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>
                      <svg v-else-if="kind === 'acceptance'" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 12l2 2 4-4"/><rect x="3" y="3" width="18" height="18" rx="3"/></svg>
                      <svg v-else-if="kind === 'behavior'" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg>
                      <svg v-else-if="kind === 'boundary'" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                      <svg v-else-if="kind === 'constraint'" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="12" cy="12" r="3"/></svg>
                      <svg v-else width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01"/></svg>
                    </span>
                    <span class="tk-group-label">{{ taskKindLabel(kind) }}</span>
                    <span class="tk-group-count">{{ items.length }}</span>
                  </div>

                  <div class="tk-items">
                    <div
                      v-for="item in items"
                      :key="item.id || `${item.kind}:${item.text}`"
                      class="tk-item"
                      :class="`tk-item--${kind} tk-item--${item.status || 'pending'}`"
                    >
                      <span class="tk-item-status" :class="`tk-item-status--${taskStatusIcon(item.status)}`">
                        <svg v-if="taskStatusIcon(item.status) === 'check'" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M5 13l4 4L19 7"/></svg>
                        <svg v-else-if="taskStatusIcon(item.status) === 'blocked'" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                        <svg v-else-if="taskStatusIcon(item.status) === 'deferred'" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                        <svg v-else-if="taskStatusIcon(item.status) === 'unknown'" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3M12 17h.01"/></svg>
                        <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/></svg>
                      </span>

                      <div class="tk-item-body">
                        <p class="tk-item-text">{{ item.text }}</p>
                        <small v-if="item.reason" class="tk-item-reason">{{ item.reason }}</small>
                        <ul v-if="taskAnswers(item.answers).length" class="tk-item-answers">
                          <li v-for="(answer, i) in taskAnswers(item.answers)" :key="`${answer.source}:${i}`">
                            <b>{{ answer.source }}</b>
                            <span>{{ answer.text }}</span>
                          </li>
                        </ul>
                        <div v-if="item.trace?.length" class="tk-item-trace">
                          <span v-for="(t, i) in item.trace" :key="i" class="tk-trace-step">
                            {{ t }}
                            <span v-if="i < item.trace.length - 1" class="tk-trace-arrow">&#8594;</span>
                          </span>
                        </div>
                      </div>

                      <span class="tk-item-badge" :class="`is-${taskStatusIcon(item.status)}`">{{ item.status }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </template>
            </div>
          </Transition>
        </div>

      </template>

      <template v-else-if="tab === 'tools'">
        <div class="inspector__section-head"><strong>Built-in tools</strong><span>{{ tools.length }} available</span></div>
        <article v-for="tool in tools" :key="tool.name" class="inspector-tool">
          <b>{{ tool.name.slice(0, 1).toUpperCase() }}</b>
          <div><strong>{{ tool.name }}</strong><small>{{ tool.description }}</small></div>
          <span>{{ Object.keys(tool.parameters?.properties || {}).length }}</span>
        </article>
      </template>

    </div>
  </aside>
</template>

<style scoped>
.inspector { position: absolute; z-index: 20; inset: 0 0 0 auto; width: min(var(--inspector-width, 392px), calc(100% - 20px)); min-width: 0; overflow: hidden; border-left: 1px solid #cbd9ec; color: #233d5b; background: rgba(247, 250, 255, .96); box-shadow: -16px 0 42px rgba(31, 67, 119, .08); will-change: transform, opacity; }
.inspector__head { display: flex; height: 58px; align-items: center; justify-content: space-between; padding: 0 14px 0 17px; border-bottom: 1px solid #d8e3f1; }
.inspector__head div { display: grid; gap: 2px; }.inspector__head strong { font-size: 12px; }.inspector__head small { max-width: 240px; overflow: hidden; color: #8192a8; font: 9.5px/1.2 var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.inspector__head button { width: 27px; height: 27px; border: 0; border-radius: 7px; color: #6d829d; background: transparent; font-size: 18px; cursor: pointer; }.inspector__head button:hover { background: #e6eef9; }
.inspector__content { height: calc(100% - 138px); overflow-y: auto; padding: 12px 10px 12px 12px; }
.usage-card,.hypothesis-row { margin-bottom: 10px; border: 1px solid #d8e2ef; border-radius: 10px; background: #fff; }
.hypothesis-row { overflow: hidden; border-left: 3px solid #d8e2ef; }
.hypothesis-row.is-supported { border-left-color: #11866f; background: rgba(15, 125, 101, .025); }
.hypothesis-row.is-refuted { border-left-color: #c44747; background: rgba(196, 71, 71, .025); }
.hypothesis-row.is-inconclusive { border-left-color: #c48b00; background: rgba(196, 139, 0, .025); }
.hypothesis-row.is-running { border-left-color: #f5c842; background: rgba(245, 200, 66, .04); }
.hypothesis-row.is-supported .hypothesis-row__meta b { color: #11866f; }
.hypothesis-row.is-refuted .hypothesis-row__meta b { color: #c44747; }
.hypothesis-row.is-inconclusive .hypothesis-row__meta b { color: #c48b00; }
.hypothesis-row.is-running .hypothesis-row__meta b { color: #c48b00; }
.usage-card { padding: 11px; }
.usage-card span,.usage-card p { color: #71859e; font: 9px/1.45 var(--mono); }.usage-card strong { display: block; margin: 4px 0; color: #1756d1; font: 700 15px/1 var(--mono); }.usage-card p { margin: 0; }
.hypothesis-row__summary {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 11px;
  border: 0;
  color: #294564;
  background: transparent;
  font-size: 10.5px;
  font-weight: 700;
  cursor: pointer;
  text-align: left;
  transition: background .15s ease;
}
.hypothesis-row__summary:hover { background: #f5f8fd; }
.hypothesis-row__summary span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.hypothesis-row__meta { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.hypothesis-row__meta b { color: var(--accent-text, #1756d1); font: 800 11px/1 var(--mono, monospace); }
.hypothesis-row__chevron {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-right: 1.5px solid #94a8c2;
  border-bottom: 1.5px solid #94a8c2;
  transform: rotate(45deg);
  transition: transform .28s cubic-bezier(.22, 1, .36, 1);
}
.hypothesis-row.is-open .hypothesis-row__chevron { transform: rotate(-135deg); }
.hypothesis-row .confidence-card { margin: 0 10px 10px; box-shadow: none; }
.hypothesis-row .evidence-card { margin: 7px 10px; }
.hypothesis-row .verdict-card { margin: 7px 10px 10px; }
.confidence-card { padding: 13px; border: 1px solid #cbdcf2; border-radius: 12px; background: #fff; box-shadow: 0 7px 20px rgba(34, 70, 118, .06); }
.confidence-card__top { display: flex; align-items: baseline; justify-content: space-between; color: #71859e; font: 700 9px/1 var(--mono); text-transform: uppercase; }.confidence-card__top strong { color: #1756d1; font-size: 20px; }
.confidence-card__track { height: 7px; margin: 9px 0 11px; overflow: hidden; border-radius: 9px; background: #e2ebf7; }.confidence-card__track i { display: block; width: 100%; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #1756d1 0 55%, #f5c642); transform: scaleX(.5); transform-origin: left; }
.confidence-card p { margin: 0; color: #344f6f; font-size: 11px; line-height: 1.55; }.confidence-card__counts { display: flex; gap: 12px; margin-top: 10px; font: 700 9px/1 var(--mono); }.support { color: #11866f; }.oppose { color: #b77d00; }
.phase-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin-top: 10px; }.phase-strip span { padding: 5px 3px; border: 1px solid #d7e3f2; border-radius: 6px; color: #7d90a7; background: #f7faff; font: 700 7.5px/1 var(--mono); text-align: center; text-transform: uppercase; }.phase-strip span.active { border-color: #1756d1; color: #fff; background: #1756d1; box-shadow: 0 4px 12px rgba(23,86,209,.18); }
.evidence-list { display: grid; gap: 7px; margin-top: 10px; }.evidence-card { position: relative; display: grid; grid-template-columns: 22px 1fr 25px; gap: 8px; padding: 10px; border: 1px solid #d6e1ef; border-radius: 10px; background: #fff; }.evidence-card.is-support { border-left: 3px solid #11866f; }.evidence-card.is-oppose { border-left: 3px solid #e0a300; }
.evidence-card__sign { display: grid; width: 21px; height: 21px; place-items: center; border-radius: 6px; color: #fff; background: #11866f; font-weight: 900; }.is-oppose .evidence-card__sign { background: #d99b00; }.evidence-card div { min-width: 0; }.evidence-card strong { display: block; color: #294564; font-size: 10.5px; line-height: 1.4; }.evidence-card small { display: block; overflow: hidden; margin-top: 3px; color: #7188a3; font: 8.5px/1.3 var(--mono); text-overflow: ellipsis; white-space: nowrap; }.evidence-card p { max-height: 48px; overflow: hidden; margin: 7px 0 0; color: #617791; font: 9px/1.45 var(--mono); }.evidence-card b { color: #8799ad; font: 800 10px/1 var(--mono); }
.relation-list,.verdict-card { margin-top: 10px; padding: 11px; border: 1px solid #d8e2ef; border-radius: 10px; background: #fff; }.relation-list > strong { font-size: 10px; }.relation-list div { display: flex; align-items: center; gap: 6px; margin-top: 7px; font: 8.5px/1 var(--mono); }.relation-list i { flex: 1; height: 1px; color: #6658c7; background: #c9c3ef; text-align: center; }
.verdict-card { border-top: 3px solid #1756d1; }.verdict-card.is-supported { border-top-color: #11866f; }.verdict-card.is-refuted { border-top-color: #c44747; }.verdict-card small { color: #8798ac; font: 700 8px/1 var(--mono); text-transform: uppercase; }.verdict-card strong { display: block; margin: 4px 0; font-size: 16px; text-transform: capitalize; }.verdict-card p { margin: 0; color: #566d88; font-size: 10px; line-height: 1.5; }
.inspector__section-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: 2px 2px 10px; }.inspector__section-head strong { font-size: 12px; }.inspector__section-head span { color: #8294aa; font: 9px/1 var(--mono); }
.inspector__section-actions { display: flex; align-items: center; gap: 8px; }
.section-action { height: 24px; padding: 0 8px; border: 1px solid #bfd0ea; border-radius: 6px; color: #1756d1; background: #eef4ff; font: 700 9px/1 var(--mono); cursor: pointer; }
.section-action:disabled { opacity: .45; cursor: default; }
/* ---- tasks redesign ---- */
.tk-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 2px 10px;
  padding: 7px 9px;
  border: 1px solid #d8e2ef;
  border-radius: 8px;
  background: #fff;
}
.tk-progress-bar {
  flex: 1;
  height: 5px;
  overflow: hidden;
  border-radius: 9px;
  background: #e2ebf7;
}
.tk-progress-bar i {
  display: block;
  width: 100%;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #1756d1, #38bdf8);
  transition: width .5s cubic-bezier(.22, 1, .36, 1);
}
.tk-progress > span {
  flex-shrink: 0;
  color: #7188a3;
  font: 700 8.5px/1 var(--mono);
}

.tk-goal {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 10px;
  padding: 11px;
  border: 1px solid #ccdcf2;
  border-left: 3px solid #6658c7;
  border-radius: 9px;
  background: linear-gradient(135deg, rgba(102,88,199,.05), #fff);
}
.tk-goal svg {
  flex-shrink: 0;
  margin-top: 1px;
  color: #6658c7;
}
.tk-goal strong {
  display: block;
  color: #294564;
  font-size: 11px;
  line-height: 1.45;
}
.tk-goal small {
  display: block;
  margin-top: 4px;
  color: #7188a3;
  font: 8.5px/1.35 var(--mono);
}

/* ---- task block (multi-task folding) ---- */
.task-block {
  margin-bottom: 10px;
  border: 1px solid #d8e2ef;
  border-radius: 10px;
  background: #fff;
  overflow: hidden;
}
.task-block__summary {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 8px;
  padding: 11px;
  border: 0;
  color: #294564;
  background: transparent;
  font-size: 10.5px;
  font-weight: 700;
  cursor: pointer;
  text-align: left;
  transition: background .15s ease;
}
.task-block__summary:hover { background: #f5f8fd; }
.task-block__summary svg {
  flex-shrink: 0;
  color: #6658c7;
}
.task-block__summary span:first-of-type {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.task-block__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.task-block__meta small {
  color: #7188a3;
  font: 8.5px/1.35 var(--mono);
}
.task-block__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.task-block__progress {
  display: block;
  width: 52px;
  height: 4px;
  overflow: hidden;
  border-radius: 4px;
  background: #e2ebf7;
}
.task-block__progress i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #1756d1, #5b8def);
  transition: width .4s cubic-bezier(.22, 1, .36, 1);
}
.task-block__count {
  color: #1756d1 !important;
  font-weight: 700;
}
.task-block__type {
  padding: 2px 5px;
  border-radius: 4px;
  background: rgba(23, 86, 209, .08);
  color: #1756d1 !important;
  font: 700 8px/1.2 var(--mono);
  text-transform: uppercase;
}
.task-block__chevron {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-right: 1.5px solid #94a8c2;
  border-bottom: 1.5px solid #94a8c2;
  transform: rotate(45deg);
  transition: transform .28s cubic-bezier(.22, 1, .36, 1);
}
.task-block__body {
  padding: 0 11px 11px;
}

/* ---- group ---- */
.tk-group {
  margin-bottom: 9px;
}
.tk-group-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 5px;
  padding: 0 1px;
}
.tk-group-icon {
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  border-radius: 5px;
  flex-shrink: 0;
}
.tk-group-icon--goal       { color: #6658c7; background: rgba(102,88,199,.1); }
.tk-group-icon--acceptance { color: #11866f; background: rgba(17,134,111,.1); }
.tk-group-icon--behavior   { color: #3b5998; background: rgba(59,89,152,.1); }
.tk-group-icon--boundary   { color: #c48b00; background: rgba(196,139,0,.1); }
.tk-group-icon--constraint { color: #1756d1; background: rgba(23,86,209,.1); }
.tk-group-icon--unknown    { color: #7c8ba0; background: rgba(124,139,160,.1); }
.tk-group-label {
  color: #294564;
  font: 650 10px/1 var(--sans);
}
.tk-group-count {
  margin-left: auto;
  color: #94a8c2;
  font: 700 8px/1 var(--mono);
}

/* ---- items ---- */
.tk-items {
  display: grid;
  gap: 4px;
}

.tk-item {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto;
  gap: 7px;
  align-items: start;
  padding: 7px 8px;
  border: 1px solid #d9e4f1;
  border-radius: 7px;
  background: #fff;
}

/* kind-specific left border */
.tk-item--goal       { border-left: 3px solid #6658c7; }
.tk-item--acceptance { border-left: 3px solid #11866f; }
.tk-item--behavior   { border-left: 3px solid #3b5998; }
.tk-item--boundary   { border-left: 3px solid #c48b00; }
.tk-item--constraint { border-left: 3px solid #1756d1; }
.tk-item--unknown    { border-left: 3px solid #a0aec0; }

/* status backgrounds */
.tk-item--known,
.tk-item--added,
.tk-item--updated { background: #f6fdf9; border-color: #bfdec7; }
.tk-item--blocked { background: #fffbf0; border-color: #ead08a; }
.tk-item--deferred { background: #f8fafc; border-color: #d8e1ec; }

.tk-item-status {
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  border-radius: 5px;
  flex-shrink: 0;
}
.tk-item-status--check    { color: #11866f; background: rgba(17,134,111,.1); }
.tk-item-status--blocked  { color: #c48b00; background: rgba(196,139,0,.12); }
.tk-item-status--deferred { color: #7c8ba0; background: rgba(124,139,160,.1); }
.tk-item-status--unknown  { color: #7c8ba0; background: rgba(124,139,160,.1); }
.tk-item-status--pending  { color: #94a8c2; background: rgba(148,168,194,.1); }

.tk-item-body {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.tk-item-text {
  margin: 0;
  color: #37516f;
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.tk-item-reason {
  display: block;
  margin-top: 2px;
  padding: 5px 7px;
  border-radius: 5px;
  color: #5a7392;
  background: rgba(124, 139, 160, .08);
  font: 9px/1.5 var(--mono);
  overflow-wrap: anywhere;
}
.tk-item-answers {
  display: grid;
  gap: 3px;
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
}
.tk-item-answers li {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 6px;
  align-items: baseline;
  padding: 4px 7px;
  border: 1px solid #dfe8f3;
  border-radius: 5px;
  background: #fbfdff;
  font-size: 10px;
  line-height: 1.45;
}
.tk-item-answers b {
  color: #1756d1;
  font: 700 8px/1 var(--mono);
  text-transform: uppercase;
}
.tk-item-answers span {
  min-width: 0;
  color: #4a607d;
  overflow-wrap: anywhere;
}
.tk-item-trace {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 5px;
}
.tk-trace-step {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 6px;
  border: 1px solid #dce6f2;
  border-radius: 5px;
  color: #5a7392;
  background: #f5f8fd;
  font: 8.5px/1.4 var(--mono);
}
.tk-trace-arrow {
  color: #9fb1c8;
}
.tk-item-badge {
  padding: 2px 5px;
  border-radius: 4px;
  color: #7c8ba0;
  background: rgba(124, 139, 160, .1);
  font: 700 8px/1.2 var(--mono);
  text-transform: uppercase;
}
.tk-item-badge.is-check    { color: #11866f; background: rgba(17, 134, 111, .1); }
.tk-item-badge.is-blocked  { color: #a06c00; background: rgba(196, 139, 0, .12); }
.tk-item-badge.is-deferred { color: #7c8ba0; background: rgba(124, 139, 160, .1); }
.tk-item-badge.is-unknown  { color: #1756d1; background: rgba(23, 86, 209, .08); }
.tk-item-badge.is-pending  { color: #94a8c2; background: rgba(148, 168, 194, .1); }

.tk-item--known .tk-item-text,
.tk-item--added .tk-item-text,
.tk-item--updated .tk-item-text { color: #4a7c5c; }

.tk-item-reason {
  color: #5d7188;
  font-size: 8.5px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.tk-item-answers {
  display: grid;
  gap: 3px;
  margin: 2px 0 0;
  padding: 0;
  list-style: none;
}

.tk-item-answers li {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 5px;
  align-items: start;
  color: #36506d;
  font-size: 9px;
  line-height: 1.35;
}

.tk-item-answers b {
  padding: 1px 4px;
  border-radius: 3px;
  color: #1756d1;
  background: rgba(23,86,209,.08);
  font: 700 7.5px/1.3 var(--mono);
  text-transform: uppercase;
}

.tk-item-trace {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 3px;
  margin-top: 1px;
}

.tk-trace-step {
  padding: 1px 5px;
  border-radius: 3px;
  color: #1756d1;
  background: rgba(23,86,209,.06);
  font: 8px/1.35 var(--mono);
}

.tk-trace-arrow {
  color: #a0b5ce;
  font-size: 7px;
  margin: 0 1px;
}

.tk-item-badge {
  flex-shrink: 0;
  padding: 1px 5px;
  border-radius: 3px;
  color: #7188a3;
  background: rgba(113,136,163,.08);
  font: 700 7.5px/1.3 var(--mono);
  text-transform: uppercase;
  white-space: nowrap;
}
.tk-item--known .tk-item-badge,
.tk-item--added .tk-item-badge,
.tk-item--updated .tk-item-badge { color: #11866f; background: rgba(17,134,111,.1); }
.tk-item--blocked .tk-item-badge { color: #8a5b00; background: rgba(196,139,0,.12); }
.inspector-tool { display: grid; grid-template-columns: 28px 1fr auto; align-items: center; gap: 9px; margin-bottom: 6px; padding: 9px; border: 1px solid #d7e2ef; border-radius: 9px; background: #fff; }.inspector-tool > b { display: grid; width: 27px; height: 27px; place-items: center; border-radius: 7px; color: #1756d1; background: #e8f0ff; font: 800 10px/1 var(--mono); }.inspector-tool div { display: grid; gap: 3px; }.inspector-tool strong { font: 700 10px/1 var(--mono); }.inspector-tool small { color: #778ba4; font-size: 9px; line-height: 1.35; }.inspector-tool > span { color: #8da0b6; font: 800 9px/1 var(--mono); }
.mcp-row { margin-bottom: 7px; padding: 10px; border: 1px solid #d7e2ef; border-radius: 10px; background: #fff; }
.subagent-row {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  margin-bottom: 8px;
  padding: 12px;
  border: 1px solid #d7e2ef;
  border-radius: 10px;
  background: #fff;
  overflow: hidden;
  min-width: 0;
}

.subagent-row__dot {
  width: 8px; height: 8px; margin-top: 4px;
  border-radius: 50%; flex-shrink: 0;
  background: #94a8c2;
  transition: background var(--transition, 180ms), box-shadow var(--transition, 180ms);
}
.subagent-row.running .subagent-row__dot {
  background: #f5c842;
  box-shadow: 0 0 0 3px rgba(245,200,66,.25);
  animation: agent-dot-pulse 1.5s ease-in-out infinite;
}
.subagent-row.done .subagent-row__dot {
  background: #11866f;
  box-shadow: 0 0 0 3px rgba(15,125,101,.12);
}
.subagent-row.error .subagent-row__dot {
  background: #c44747;
  box-shadow: 0 0 0 3px rgba(196,71,71,.1);
}
.subagent-row.ready .subagent-row__dot {
  background: #1756d1;
  box-shadow: 0 0 0 3px rgba(23,86,209,.1);
}

.subagent-row__body {
  display: flex; flex-direction: column; gap: 4px;
  min-width: 0;
}

.subagent-row__head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
}

.subagent-row strong {
  font: 700 11px/1.3 var(--mono, monospace);
  color: var(--text-h, #102a5c);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.subagent-row__state {
  flex-shrink: 0;
  padding: 1px 6px; border-radius: 4px;
  font: 700 8px/1.3 var(--mono, monospace);
  text-transform: uppercase; letter-spacing: .06em;
}
.subagent-row.running .subagent-row__state { color: #5c4200; background: rgba(245,200,66,.2); }
.subagent-row.done .subagent-row__state { color: #007b59; background: rgba(0,168,120,.1); }
.subagent-row.done .subagent-row__state::before { content: '✓ '; }
.subagent-row.error .subagent-row__state { color: #bd145d; background: rgba(225,29,116,.08); }
.subagent-row.ready .subagent-row__state { color: #1748a3; background: rgba(23,86,209,.07); }

.subagent-row small {
  color: var(--text-muted, #71809c);
  font: 9px/1.35 var(--mono, monospace);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.subagent-row p {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: 7px;
  border: 1px solid rgba(23,86,209,.06);
  background: #f8fafd;
  color: var(--text, #3f5274);
  font: 9.5px/1.5 var(--mono, monospace);
  overflow-wrap: anywhere; word-break: break-word;
}

@keyframes agent-dot-pulse {
  0%, 100% { box-shadow: 0 0 0 3px rgba(245,200,66,.25); }
  50% { box-shadow: 0 0 0 6px rgba(245,200,66,.12); }
}
@media (prefers-reduced-motion: reduce) {
  .subagent-row.running .subagent-row__dot { animation: none; }
}
.mcp-row__main { display: grid; grid-template-columns: 12px minmax(0, 1fr) auto; align-items: center; gap: 8px; }
.mcp-row__main i { width: 9px; height: 9px; border-radius: 50%; background: #91a0b2; }
.mcp-row.running .mcp-row__main i { background: #11866f; box-shadow: 0 0 0 3px #dff8f1; }
.mcp-row.error .mcp-row__main i { background: #c44747; box-shadow: 0 0 0 3px #ffe5e5; }
.mcp-row__main span { display: grid; min-width: 0; gap: 2px; }
.mcp-row__main strong { color: #294564; font: 800 10.5px/1.2 var(--mono); }
.mcp-row__main small { overflow: hidden; color: #7188a3; font: 9px/1.35 var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.mcp-row__main b { color: #8799ad; font: 800 10px/1 var(--mono); }
.mcp-row__tools { display: none; flex-wrap: wrap; gap: 5px; margin-top: 9px; }
.mcp-row:hover .mcp-row__tools { display: flex; }
.workspace-note { margin: 12px 0 0; padding: 14px 12px; border: 1px dashed #cbd9ec; border-radius: 9px; color: #7188a3; background: #f8faff; font: 10px/1.45 var(--mono); text-align: center; }.mcp-row__tools span { padding: 4px 5px; border: 1px solid #d8e2ef; border-radius: 6px; color: #1756d1; background: #eef4ff; font: 8.5px/1 var(--mono); }
.evidence-list-enter-active,.evidence-list-leave-active { transition: opacity .25s, transform .3s cubic-bezier(.22,1,.36,1); }.evidence-list-enter-from,.evidence-list-leave-to { opacity: 0; transform: translateY(10px) scale(.98); }

/* visual refresh */
.inspector {
  width: min(var(--inspector-width, 392px), calc(100% - 20px));
  border-left-color: rgba(16, 42, 92, 0.16);
  color: var(--text);
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(246, 250, 255, 0.98)),
    var(--bg-raised);
  box-shadow: -18px 0 52px rgba(23, 72, 150, 0.14);
}

.inspector__resize {
  position: absolute;
  inset: 0 auto 0 -5px;
  z-index: 4;
  width: 10px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  cursor: col-resize;
  touch-action: none;
}

.inspector__resize::after {
  content: "";
  position: absolute;
  top: 12px;
  bottom: 12px;
  left: 4px;
  width: 2px;
  border-radius: 999px;
  background: transparent;
  transition: background var(--transition-fast), box-shadow var(--transition-fast);
}

.inspector__resize:hover::after,
.inspector__resize:focus-visible::after {
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-bg);
}

.inspector__head {
  position: sticky;
  top: 0;
  z-index: 2;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 36px;
  column-gap: 14px;
  height: 90px;
  align-items: center;
  padding: 16px 18px 16px 18px;
  border-bottom-color: var(--border);
  background:
    linear-gradient(135deg, var(--inspector-accent-soft), transparent 48%),
    rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(16px);
}

.inspector__head::before {
  display: none;
}

.inspector__head .inspector__title {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  min-width: 0;
  align-items: center;
  gap: 13px;
}

.inspector__mark {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  place-items: center;
  border: 1px solid var(--inspector-accent-soft);
  border-radius: var(--radius);
  color: #ffffff;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.18), transparent 42%),
    var(--inspector-accent);
  font: 800 12px/1 var(--mono);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.2),
    0 10px 24px color-mix(in srgb, var(--inspector-accent) 22%, transparent);
}

.inspector__head .inspector__title-copy {
  display: grid;
  gap: 7px;
  min-width: 0;
}

.inspector__head strong {
  color: var(--text-h);
  font: 620 18px/1.05 var(--heading);
  letter-spacing: -0.02em;
}

.inspector__head small {
  overflow: hidden;
  color: var(--text-muted);
  font: 10px/1.35 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector__close {
  width: 36px;
  height: 36px;
  margin-left: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  background: #ffffff;
}

.inspector__close:hover {
  border-color: var(--border);
  color: var(--text-h);
  background: var(--code-bg-hover);
}

.inspector__content {
  height: calc(100% - 90px);
  padding: 14px;
  background:
    linear-gradient(rgba(23, 86, 209, 0.035) 1px, transparent 1px),
    transparent;
  background-size: 100% 48px;
}

.usage-card,
.confidence-card,
.hypothesis-row,
.evidence-card,
.relation-list,
.verdict-card,
.mcp-row,
.subagent-row,
.task-block,
.inspector-tool,
.terminal-card {
  border-color: var(--border);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.78), 0 10px 28px rgba(23, 72, 150, 0.07);
}

.usage-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 5px 10px;
  padding: 13px;
}

.usage-card span {
  grid-column: 1 / 2;
}

.usage-card strong {
  grid-column: 2 / 3;
  margin: 0;
  align-self: center;
  color: var(--text-h);
}

.usage-card p {
  grid-column: 1 / -1;
}

.confidence-card,
.hypothesis-row,
.task-block {
  border-left-width: 1px;
}

.confidence-card__top {
  color: var(--text-muted);
  letter-spacing: 0;
  text-transform: none;
}

.confidence-card__track,
.tk-progress-bar {
  height: 6px;
  background: var(--code-bg-hover);
}

.inspector__section-head {
  margin: 0 2px 11px;
  padding: 0 1px;
}

.inspector__section-head strong {
  color: var(--text-h);
  font: 570 14px/1.2 var(--heading);
}

.inspector__section-head span,
.task-block__meta small,
.mcp-row__main small,
.subagent-row small,
.inspector-tool small {
  color: var(--text-muted);
}

.hypothesis-row__summary,
.task-block__summary,
.terminal-card__summary {
  min-height: 44px;
}

.evidence-card,
.tk-item,
.inspector-tool,
.mcp-row,
.subagent-row {
  transition: border-color var(--transition-fast), background var(--transition-fast), transform var(--transition-fast);
}

.evidence-card:hover,
.tk-item:hover,
.inspector-tool:hover,
.mcp-row:hover,
.subagent-row:hover {
  border-color: var(--accent-border);
  background: #ffffff;
  transform: translateY(-1px);
}

@media (max-width: 980px) { .inspector { height: 100%; } }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>
