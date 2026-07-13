<script setup>
import { ref, reactive, computed, provide, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { gsap } from 'gsap'
import ChatEvent from './chat/ChatEvent.vue'
import { useChatStream } from '../composables/useChatStream'
import FileReference from './FileReference.vue'
import FileMentionDropdown from './FileMentionDropdown.vue'
import InspectorPanel from './inspector/InspectorPanel.vue'

const props = defineProps({
  session: { type: Object, default: null },
  mcpServers: { type: Array, default: () => [] },
  workspaces: { type: Array, default: () => [] },
  activeWorkspace: { type: Object, default: null },
  workspaceError: { type: String, default: '' },
  sessions: { type: Array, default: () => [] },
})
const emit = defineEmits([
  'save-session-state',
  'add-workspace',
  'activate-workspace',
  'delete-workspace',
  'create-session',
  'open-session',
  'rename-session',
  'delete-session',
])

/* ── todos ── */
const inspectorTab = ref(null)
const todos = reactive([
  { id: 1, content: 'Implement agent chat backend', done: false },
  { id: 2, content: 'Add provider connection testing', done: true },
  { id: 3, content: 'Build sub-agent dispatch system', done: false },
  { id: 4, content: 'Create diff preview component', done: false },
])

/* ── file context ── */
const fileContext = reactive([])

const suggestions = [
  { label: 'Run the tool workflow', detail: 'See read and grep calls in action', prompt: 'Inspect the server tools and show me how they work' },
  { label: 'Explain a module', detail: 'Map structure, calls, and side effects', prompt: 'Explain how the main application module works' },
  { label: 'Refactor safely', detail: 'Reduce complexity without changing behavior', prompt: 'Review the current code and propose a focused refactor' },
]

const toolCatalog = ref([])
const toolNames = computed(() => toolCatalog.value.map(tool => tool.name).filter(Boolean))
provide('toolNames', toolNames)

/* ── chat state ── */
const input = ref('')
const chatRef = ref(null)
const msgList = ref(null)
const messages = reactive([])
const msgRefs = reactive({})
const isStreaming = ref(false)
const restoring = ref(false)
const textareaRef = ref(null)

/* ── mention state ── */
const mentionFiles = ref([])
const mentionFilesLoaded = ref(false)
const mentionActive = ref(false)
const mentionSearch = ref('')
const mentionStartPos = ref(-1)
const mentionDropdownTop = ref(0)
const mentionDropdownLeft = ref(0)

async function loadMentionFiles() {
  if (mentionFilesLoaded.value) return
  try {
    const res = await fetch('/api/files/list')
    if (!res.ok) throw new Error(`Failed to load file list (${res.status})`)
    const data = await res.json()
    mentionFiles.value = data.files || []
    mentionFilesLoaded.value = true
  } catch {
    mentionFiles.value = []
  }
}

function mentionInsert(path) {
  if (!textareaRef.value) return
  const ta = textareaRef.value
  const before = ta.value.substring(0, mentionStartPos.value)
  const after = ta.value.substring(ta.selectionStart)
  input.value = before + after
  mentionActive.value = false
  mentionSearch.value = ''
  mentionStartPos.value = -1
  addToFileContext(path)
  nextTick(() => {
    const pos = before.length
    ta.setSelectionRange(pos, pos)
    ta.focus()
  })
}

function addToFileContext(path) {
  if (fileContext.find(f => f.path === path)) return
  const ext = path.includes('.') ? path.split('.').pop().toLowerCase() : ''
  fileContext.push({ path, lang: ext || 'text' })
}

function onTextareaInput() {
  const ta = textareaRef.value
  if (!ta) return
  const cursor = ta.selectionStart
  const text = ta.value
  const atPos = text.lastIndexOf('@', cursor)
  if (atPos < 0 || (atPos > 0 && /\w/.test(text[atPos - 1]))) {
    mentionActive.value = false
    return
  }
  const between = text.substring(atPos + 1, cursor)
  if (/\s/.test(between)) {
    mentionActive.value = false
    return
  }
  mentionStartPos.value = atPos
  mentionSearch.value = between
  mentionActive.value = true
  const rect = ta.getBoundingClientRect()
  const ddHeight = 324
  const ddWidth = 300
  if (rect.bottom + ddHeight + 8 > window.innerHeight) {
    mentionDropdownTop.value = Math.max(4, rect.top - ddHeight - 4)
  } else {
    mentionDropdownTop.value = rect.bottom + 4
  }
  mentionDropdownLeft.value = Math.min(rect.left, window.innerWidth - ddWidth - 8)
  loadMentionFiles()
}

function onTextareaKeydown(e) {
  if (e.defaultPrevented) return
  if (mentionActive.value && (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === 'Tab' || e.key === 'Escape')) {
    return
  }
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
}

function closeMention() {
  mentionActive.value = false
  mentionSearch.value = ''
  mentionStartPos.value = -1
}
const emptyEvidenceRun = reactive({
  id: '',
  hypothesis: '',
  confidence: .5,
  status: 'idle',
  phase: 'idle',
  evidence: [],
  relations: [],
  verdict: null,
})
const evidenceRuns = reactive([])
const activeRunId = ref('')
const taskAnalyses = reactive([])
const subagentRuns = reactive([])
const availableSubagents = reactive([
  {
    id: 'available-mcp-installer',
    name: '@mcp-installer',
    task: 'Install MCP servers from docs, URLs, or config hints.',
    status: 'ready',
    result: 'Available from the MCP page and the subagent tool.',
  },
  {
    id: 'available-hypothesis-verifier',
    name: '@hypothesis-verifier',
    task: 'Verify code hypotheses by gathering and recording grounded evidence.',
    status: 'ready',
    result: 'Runs after task analysis and is available through the subagent tool.',
  },
])
const evidenceRun = computed(() => evidenceRuns.find(run => run.id === activeRunId.value) || emptyEvidenceRun)
const activeTaskAnalysis = computed(() => taskAnalyses[taskAnalyses.length - 1] || null)
const visibleSubagents = computed(() => {
  const byName = new Map()
  for (const agent of availableSubagents) byName.set(agent.name, agent)
  for (const agent of subagentRuns) if (!byName.has(agent.name)) byName.set(agent.name, agent)
  return [...byName.values()]
})
const sessionName = computed(() => props.session?.name || 'New session')
const sessionUsage = reactive({
  input_tokens: 0,
  output_tokens: 0,
  cached_tokens: 0,
  total_tokens: 0,
  cost: 0,
  currency: 'USD',
})
const agentStatus = reactive({
  state: 'idle',
  phase: '',
  provider: '',
  model: '',
  contextLength: null,
  contextUsed: 0,
})
const contextPercent = computed(() => {
  if (!agentStatus.contextLength) return 0
  return Math.min(100, Math.round((agentStatus.contextUsed / agentStatus.contextLength) * 100))
})
const agentRunning = computed(() => isStreaming.value)
const contextRatio = computed(() => `${contextPercent.value}%`)
let gsapCtx
let saveTimer

function setMsgRef(id, el) { if (el) msgRefs[id] = el }

function timeNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function toggleTodo(id) {
  const t = todos.find(t => t.id === id)
  if (t) t.done = !t.done
}

function toggleInspector(tab) {
  inspectorTab.value = inspectorTab.value === tab ? null : tab
}

function currentEvidenceRun() {
  return evidenceRuns.find(run => run.id === activeRunId.value)
}

function addUsage(delta = {}) {
  sessionUsage.input_tokens += delta.input_tokens || 0
  sessionUsage.output_tokens += delta.output_tokens || 0
  sessionUsage.cached_tokens += delta.cached_tokens || 0
  sessionUsage.total_tokens += delta.total_tokens || 0
  sessionUsage.cost = Number((sessionUsage.cost + (delta.cost || 0)).toFixed(6))
  sessionUsage.currency = delta.currency || sessionUsage.currency
}

function usageDefaults() {
  return {
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    total_tokens: 0,
    cost: 0,
    currency: 'USD',
  }
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString()
}

function plain(value) {
  return JSON.parse(JSON.stringify(value))
}

function snapshotState() {
  return {
    messages: plain(messages),
    evidenceRuns: plain(evidenceRuns),
    activeRunId: activeRunId.value,
    taskAnalyses: plain(taskAnalyses),
    subagentRuns: plain(subagentRuns),
    fileContext: plain(fileContext),
    usage: plain(sessionUsage),
  }
}

function scheduleSave() {
  if (!props.session?.id) return
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => emit('save-session-state', snapshotState()), 220)
}

function clearState() {
  messages.splice(0, messages.length)
  evidenceRuns.splice(0, evidenceRuns.length)
  taskAnalyses.splice(0, taskAnalyses.length)
  subagentRuns.splice(0, subagentRuns.length)
  activeRunId.value = ''
  fileContext.splice(0, fileContext.length)
  Object.assign(sessionUsage, usageDefaults())
  Object.assign(agentStatus, { state: 'idle', phase: '', provider: '', model: '', contextLength: null, contextUsed: 0 })
}

async function restoreState(state = {}) {
  clearState()
  restoring.value = true
  await nextTick()

  const rawMessages = state.messages || []
  const CHUNK = 30

  for (let i = 0; i < rawMessages.length; i += CHUNK) {
    const batch = rawMessages.slice(i, i + CHUNK)
    for (const msg of batch) {
      messages.push(reactive({
        ...msg,
        events: (msg.events || []).map(event => ({ ...event, data: reactive(event.data || {}) })),
      }))
    }
    if (i + CHUNK < rawMessages.length) {
      await new Promise(resolve => requestAnimationFrame(resolve))
    }
  }

  evidenceRuns.splice(0, evidenceRuns.length, ...(state.evidenceRuns || []).map(run => reactive(run)))
  taskAnalyses.splice(0, taskAnalyses.length, ...(state.taskAnalyses || []).map(analysis => reactive(analysis)))
  subagentRuns.splice(0, subagentRuns.length, ...(state.subagentRuns || []).map(run => reactive(run)))
  activeRunId.value = state.activeRunId || ''
  fileContext.splice(0, fileContext.length, ...(state.fileContext || []))
  Object.assign(sessionUsage, usageDefaults(), state.usage || {})
  Object.assign(agentStatus, { state: 'idle', phase: '', provider: '', model: '', contextLength: null, contextUsed: 0 })

  restoring.value = false
  await nextTick()
  scrollBottom()
}

async function loadTools() {
  try {
    const response = await fetch('/api/tools')
    if (!response.ok) return
    const tools = await response.json()
    toolCatalog.value = Array.isArray(tools) ? tools : []
  } catch {
    toolCatalog.value = []
  }
}

function removeContextFile(p) {
  const i = fileContext.findIndex(f => f.path === p)
  if (i !== -1) fileContext.splice(i, 1)
}

function questionEventFor(answer) {
  for (const message of messages) {
    const event = message.events?.find(item => item.type === 'user_question' && item.data?.id === answer.question_id)
    if (event) return { message, event }
  }
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'assistant') return { message: messages[i], event: null }
  }
  return { message: null, event: null }
}

async function send(answer = null) {
  if (answer) {
    await continueAfterAnswer(answer)
    return
  }
  const text = input.value.trim()
  if (!text || isStreaming.value) return
  messages.push({ id: Date.now(), role: 'user', content: text, time: timeNow(), files: plain(fileContext) })
  const message = reactive({ id: Date.now() + 1, role: 'assistant', time: timeNow(), events: [] })
  messages.push(message)
  input.value = ''
  isStreaming.value = true
  Object.assign(agentStatus, { state: 'running', phase: 'starting', contextUsed: 0 })
  inspectorTab.value = 'evidence'
  nextTick(() => { scrollBottom(); animateLast() })
  try {
    const structuredAnswer = answer
    const taskAnalysis = structuredAnswer ? analysisForQuestion(structuredAnswer) : null
    const origin = structuredAnswer?.origin_message || taskAnalysis?.origin_message || text
    const request = {
      message: origin,
      context: fileContext.map(file => file.path),
      session_id: props.session?.id,
    }
    if (structuredAnswer) {
      request.answer = { ...structuredAnswer, origin_message: origin }
      if (taskAnalysis) request.analysis = plain(taskAnalysis)
    }
    await chatStream(message, request)
  } catch (error) {
    if (error.name !== 'AbortError') {
      message.events.push({
        id: `${message.id}-error`,
        type: 'output',
        data: reactive({ content: `Chat failed: ${error.message}`, streaming: false }),
      })
    }
  } finally {
    isStreaming.value = false
    agentStatus.state = 'idle'
    scheduleSave()
    nextTick(scrollBottom)
  }
}

async function continueAfterAnswer(answer) {
  if (!answer) return
  const { message, event } = questionEventFor(answer)
  if (!message || isStreaming.value) return
  if (event?.data) Object.assign(event.data, {
    answer_status: 'submitted',
    selected_option_id: answer.selected_option_id,
    selected_option_label: answer.selected_option_label,
    response: answer.response,
  })
  isStreaming.value = true
  Object.assign(agentStatus, { state: 'running', phase: 'continuing', contextUsed: 0 })
  inspectorTab.value = 'evidence'
  const continuation = reactive({ id: Date.now() + 1, role: 'assistant', time: timeNow(), events: [] })
  messages.push(continuation)
  nextTick(() => { scrollBottom(); animateLast() })
  try {
    const taskAnalysis = analysisForQuestion(answer)
    const origin = answer.origin_message || taskAnalysis?.origin_message || answer.question || ''
    const request = {
      message: origin,
      context: fileContext.map(file => file.path),
      session_id: props.session?.id,
      answer: { ...answer, origin_message: origin },
    }
    if (taskAnalysis) request.analysis = plain(taskAnalysis)
    await chatStream(continuation, request)
  } catch (error) {
    if (error.name !== 'AbortError') {
      continuation.events.push({
        id: `${continuation.id}-answer-error`,
        type: 'output',
        data: reactive({ content: `Chat failed: ${error.message}`, streaming: false }),
      })
    }
  } finally {
    isStreaming.value = false
    agentStatus.state = 'idle'
    scheduleSave()
    nextTick(scrollBottom)
  }
}

function answerQuestion(answer) {
  continueAfterAnswer(answer)
}

function applyTaskUpdate(update) {
  const analysis = analysisForId(update?.analysis_id) || activeTaskAnalysis.value
  if (!analysis || !Array.isArray(update?.items)) return []
  if (!Array.isArray(analysis.task_updates)) analysis.task_updates = []
  const changes = []
  for (const item of update.items) {
    const text = String(item?.text || '').trim()
    if (!text) continue
    const id = item.id || `${item.kind || 'unknown'}:${text}`
    const trace = Array.isArray(item.trace) ? item.trace : []
    const existing = analysis.task_updates.find(row => sameTaskItem(row, { ...item, id, text, trace }))
    const next = {
      id: existing?.id || id,
      kind: item.kind || 'unknown',
      text,
      status: item.status || 'updated',
      reason: item.reason || '',
      trace,
    }
    const before = existing ? { ...existing } : null
    const action = !existing
      ? 'add'
      : existing.status !== next.status
        ? 'status'
        : existing.text !== next.text || existing.kind !== next.kind || existing.reason !== next.reason
          ? 'update'
          : 'noop'
    if (existing) Object.assign(existing, next)
    else analysis.task_updates.push(next)
    changes.push({ action, before, item: next })
  }
  return changes.filter(change => change.action !== 'noop')
}

function normalizedTaskText(value) {
  return String(value || '')
    .replace(/[（(][^）)]*[）)]/g, '')
    .replace(/[^\p{L}\p{N}]+/gu, '')
    .toLowerCase()
}

function sameTaskItem(left, right) {
  if (!left || !right) return false
  if (sameTaskId(left.id, right.id)) return true
  const leftTrace = Array.isArray(left.trace) ? left.trace : []
  const rightTrace = Array.isArray(right.trace) ? right.trace : []
  if ([left.id, ...leftTrace].some(leftId => [right.id, ...rightTrace].some(rightId => sameTaskId(leftId, rightId)))) return true
  const a = normalizedTaskText(left.text)
  const b = normalizedTaskText(right.text)
  return Boolean(a && b && (a === b || a.includes(b) || b.includes(a)))
}

function sameTaskId(left, right) {
  left = String(left || '')
  right = String(right || '')
  if (!left || !right) return false
  if (left === right) return true
  if (left.includes(':') && right.includes(':')) return false
  return taskIdTail(left) === taskIdTail(right)
}

function taskIdTail(value) {
  return String(value || '').split(':').pop()
}

function analysisForId(id) {
  return id ? taskAnalyses.find(analysis => analysis.id === id) : null
}

function analysisForQuestion(question) {
  return analysisForId(question?.analysis_id) || activeTaskAnalysis.value
}

/* ── animation helpers ──────────────────────────────────── */

function animSmoothScroll() {
  if (!msgList.value) return
  gsap.to(msgList.value, { scrollTop: msgList.value.scrollHeight, duration: 0.25, ease: 'power2.out' })
}

function scrollBottom() { if (msgList.value) msgList.value.scrollTop = msgList.value.scrollHeight }

function upsertSubagent(data) {
  const existing = [...availableSubagents, ...subagentRuns].find(agent =>
    (data.id && agent.id === data.id) || (data.name && agent.name === data.name)
  )
  if (existing) {
    Object.assign(existing, data)
    return
  }
  subagentRuns.push(reactive({ ...data }))
}

function onAgentPacket(packet, type, data) {
  if (packet.op === 'start' && type === 'hypothesis') {
    const run = reactive({
      id: packet.id,
      hypothesis: data.text,
      confidence: data.confidence,
      status: data.status,
      phase: 'support',
      evidence: [],
      relations: [],
      verdict: null,
      open: true,
    })
    evidenceRuns.push(run)
    activeRunId.value = run.id
  } else if (packet.op === 'start' && type === 'task_analysis') {
    data.id ||= packet.id
    data.origin_message ||= messages[messages.length - 2]?.content || ''
    taskAnalyses.push(data)
    inspectorTab.value = 'tasks'
  } else if (packet.op === 'start' && type === 'task_update') {
    data.changes = applyTaskUpdate(data)
    inspectorTab.value = 'tasks'
  } else if (packet.op === 'start' && type === 'user_question') {
    const analysis = analysisForId(data.analysis_id) || activeTaskAnalysis.value
    data.analysis_id ||= analysis?.id || ''
    data.origin_message ||= analysis?.origin_message || ''
    data.answer_status ||= 'waiting'
  } else if (packet.op === 'start' && type === 'evidence') {
    const run = currentEvidenceRun()
    if (!run) return
    run.evidence.push({ ...data })
    run.confidence = data.confidence
  } else if (packet.op === 'start' && type === 'evidence_relation') {
    const run = currentEvidenceRun()
    if (!run) return
    run.relations.push({ ...data })
    run.confidence = data.confidence
  } else if (packet.op === 'start' && type === 'verdict') {
    const run = currentEvidenceRun()
    if (!run) return
    run.verdict = { ...data }
    run.status = data.verdict
    run.confidence = data.confidence
  } else if (packet.op === 'start' && type === 'usage') {
    addUsage(data.delta)
    agentStatus.contextUsed = data.delta?.input_tokens || agentStatus.contextUsed
  } else if (packet.op === 'start' && type === 'subagent') {
    upsertSubagent(data)
  } else if (packet.op === 'update' && type === 'subagent') {
    upsertSubagent(data)
  } else if (packet.op === 'update' && type === 'stage' && data?.phase) {
    const run = currentEvidenceRun()
    if (run) run.phase = data.phase
    agentStatus.phase = data.phase
  } else if (packet.op === 'start' && type === 'stage') {
    Object.assign(agentStatus, {
      state: data.state || 'running',
      phase: data.phase || '',
      provider: data.provider || '',
      model: data.model || '',
      contextLength: data.context_length || null,
    })
  } else if (type === 'hypothesis' && data) {
    const run = currentEvidenceRun()
    if (!run) return
    run.confidence = data.confidence
    run.status = data.status
  } else if (packet.op === 'done' && packet.run) {
    const run = currentEvidenceRun()
    if (run) run.status = packet.run.state
  }
}

const { stream: chatStream, abort: abortChat } = useChatStream(animSmoothScroll, onAgentPacket)

function animateLast() {
  const ids = Object.keys(msgRefs)
  if (!ids.length) return
  const el = msgRefs[ids[ids.length - 1]]
  if (el) gsap.fromTo(el, { autoAlpha: 0, y: 8, scale: 0.98 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.25, ease: 'power2.out' })
}

onMounted(() => {
  loadTools()
  gsapCtx = gsap.context(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    gsap.fromTo(
      ['.chat__welcome-mark', '.chat__title', '.chat__subtitle'],
      { y: 10, autoAlpha: 0 },
      { y: 0, autoAlpha: 1, duration: 0.42, stagger: 0.055, ease: 'power2.out' },
    )
    gsap.fromTo(
      '.chat__hint',
      { x: -8, autoAlpha: 0 },
      { x: 0, autoAlpha: 1, duration: 0.34, stagger: 0.045, ease: 'power2.out', delay: 0.12 },
    )
    gsap.fromTo('.chat__composer', { y: 12, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.46, ease: 'power2.out', delay: 0.16 })
  }, chatRef.value)
})
onUnmounted(() => { abortChat(); gsapCtx?.revert(); clearTimeout(saveTimer) })

watch(() => props.session?.id, (id) => {
  if (!id) {
    clearState()
    return
  }
  restoreState(props.session?.state || {})
}, { immediate: true })

watch(() => props.activeWorkspace?.id, () => {
  mentionFiles.value = []
  mentionFilesLoaded.value = false
})
</script>

<template>
  <div ref="chatRef" class="chat">

    <div class="chat__topbar">
      <div class="chat__session">
        <span class="chat__session-mark">&gt;_</span>
        <div>
          <strong>{{ sessionName }}</strong>
          <small>{{ agentRunning ? 'Agent running' : 'Agent idle' }}</small>
        </div>
      </div>

      <div class="chat__topbar-right">
        <button class="chat__topbtn" :class="{ 'is-on': inspectorTab === 'evidence' }" @click="toggleInspector('evidence')" title="Evidence">
          <span aria-hidden="true">◎</span>
          <span class="chat__topbtn-label">Evidence</span>
          <span class="chat__topbtn-badge">{{ evidenceRuns.length }}</span>
        </button>
        <button class="chat__topbtn chat__topbtn--tools" :class="{ 'is-on': inspectorTab === 'tools' }" @click="toggleInspector('tools')" title="Built-in tools">
          <span class="chat__tool-grid" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
          <span class="chat__topbtn-label">Tools</span>
          <span class="chat__topbtn-badge chat__topbtn-badge--yellow">{{ toolCatalog.length }}</span>
        </button>
        <button class="chat__topbtn" :class="{ 'is-on': inspectorTab === 'mcp' }" @click="toggleInspector('mcp')" title="MCP">
          <span aria-hidden="true">M</span>
          <span class="chat__topbtn-label">MCP</span>
          <span class="chat__topbtn-badge">{{ mcpServers.length }}</span>
        </button>
        <button class="chat__topbtn" :class="{ 'is-on': inspectorTab === 'subagents' }" @click="toggleInspector('subagents')" title="Subagents">
          <span aria-hidden="true">@</span>
          <span class="chat__topbtn-label">Agents</span>
          <span class="chat__topbtn-badge">{{ visibleSubagents.length }}</span>
        </button>
        <button class="chat__topbtn" :class="{ 'is-on': inspectorTab === 'tasks' }" @click="toggleInspector('tasks')" title="Tasks">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
          <span class="chat__topbtn-label">Tasks</span>
          <span class="chat__topbtn-badge">{{ todos.filter(t => !t.done).length }}</span>
        </button>
        <button class="chat__topbtn" :class="{ 'is-on': inspectorTab === 'workspace' }" @click="toggleInspector('workspace')" title="Workspace">
          <span aria-hidden="true">⌂</span>
          <span class="chat__topbtn-label">Workspace</span>
        </button>
      </div>
    </div>

    <div class="chat__body" :class="{ 'chat__body--has-panel': inspectorTab }">

      <!-- ============= message area ============= -->
      <div class="chat__main">

        <div v-if="restoring" class="chat__restoring">
          <span class="chat__restoring-spinner"></span>
          <p>Loading session...</p>
        </div>

        <div v-else-if="!messages.length" class="chat__empty">
          <div class="chat__welcome">
            <span class="chat__welcome-mark">&gt;_</span>
            <h1 class="chat__title">What should we change?</h1>
            <p class="chat__subtitle">Describe the task. StratumCode will trace the code, make the change, and show the diff.</p>
          </div>
          <div class="chat__hints">
            <button
              v-for="hint in suggestions"
              :key="hint.label"
              class="chat__hint"
              @click="input = hint.prompt; send()"
            >
              <span>
                <strong>{{ hint.label }}</strong>
                <small>{{ hint.detail }}</small>
              </span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m9 18 6-6-6-6"/></svg>
            </button>
          </div>
        </div>

        <!-- messages -->
        <div v-else ref="msgList" class="chat__msgs">
          <div
            v-for="m in messages" :key="m.id"
            :ref="(el) => setMsgRef(m.id, el)"
            class="chat__msg"
            :class="'chat__msg--' + m.role"
          >
            <div class="chat__bubble">
              <div class="chat__time">{{ m.time }}</div>

              <div v-if="m.role === 'user'" class="chat__content">
                <div v-if="m.files?.length" class="chat__msg-files">
                  <FileReference
                    v-for="f in m.files"
                    :key="f.path"
                    :path="f.path"
                    :language="f.lang"
                  />
                </div>
                {{ m.content }}
              </div>
              <TransitionGroup v-else name="timeline-event" tag="div" class="chat__timeline">
                <ChatEvent
                  v-for="event in m.events"
                  :key="event.id"
                  :event="event"
                  :events="m.events"
                  @answer="answerQuestion"
                />
              </TransitionGroup>
            </div>
          </div>
        </div>

      </div>

      <!-- ============= todo panel ============= -->
      <Transition name="panel">
        <InspectorPanel
          v-if="inspectorTab"
          :tab="inspectorTab"
          :run="evidenceRun"
          :runs="evidenceRuns"
          :task-analysis="activeTaskAnalysis"
          :usage="sessionUsage"
          :todos="todos"
          :tools="toolCatalog"
          :mcp-servers="mcpServers"
          :subagents="visibleSubagents"
          :workspaces="workspaces"
          :active-workspace="activeWorkspace"
          :workspace-error="workspaceError"
          :sessions="sessions"
          :active-session="session"
          @update:tab="inspectorTab = $event"
          @toggle-todo="toggleTodo"
          @add-workspace="emit('add-workspace', $event)"
          @activate-workspace="emit('activate-workspace', $event)"
          @delete-workspace="emit('delete-workspace', $event)"
          @create-session="emit('create-session')"
          @open-session="emit('open-session', $event)"
          @rename-session="emit('rename-session', $event)"
          @delete-session="emit('delete-session', $event)"
          @close="inspectorTab = null"
        />
      </Transition>

    </div>

    <div class="chat__foot">
      <div class="chat__composer">
        <div class="chat__runbar" :class="{ 'is-running': agentRunning }">
          <div class="chat__runstate">
            <i></i>
            <span>{{ agentRunning ? 'Running' : 'Idle' }}</span>
            <small>{{ agentStatus.phase || 'ready' }}</small>
          </div>
          <div class="chat__meter" :style="{ '--context': contextRatio }">
            <span>
              <b>{{ formatNumber(agentStatus.contextUsed) }}</b>
              <small>/ {{ agentStatus.contextLength ? formatNumber(agentStatus.contextLength) : 'unknown' }}</small>
            </span>
            <em><i></i></em>
          </div>
          <div class="chat__usage-strip">
            <span><small>Tokens</small><b>{{ formatNumber(sessionUsage.total_tokens) }}</b></span>
            <span><small>Cache</small><b>{{ formatNumber(sessionUsage.cached_tokens) }}</b></span>
            <span><small>Cost</small><b>{{ sessionUsage.currency }} {{ sessionUsage.cost.toFixed(6) }}</b></span>
          </div>
          <div v-if="agentStatus.model" class="chat__model">{{ agentStatus.provider }} / {{ agentStatus.model }}</div>
        </div>
        <div v-if="fileContext.length" class="chat__files">
          <span class="chat__files-label">Context</span>
          <FileReference
            v-for="f in fileContext"
            :key="f.path"
            :path="f.path"
            :language="f.lang"
            removable
            @remove="removeContextFile(f.path)"
          />
        </div>
        <div class="chat__input-row">
          <textarea
            ref="textareaRef"
            v-model="input"
            class="chat__input"
            placeholder="Ask StratumCode to inspect or change the project"
            rows="1"
            @keydown="onTextareaKeydown"
            @input="onTextareaInput"
            :disabled="isStreaming"
          ></textarea>
          <button class="chat__send" type="button" @click="send" :disabled="!input.trim() || isStreaming" aria-label="Send message">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
              <path d="M12 19V5M6 11l6-6 6 6"/>
            </svg>
          </button>
        </div>

        <div class="chat__composer-meta">
          <span>Enter to send</span>
        </div>

        <FileMentionDropdown
          :files="mentionFiles"
          :search-text="mentionSearch"
          :visible="mentionActive"
          :top="mentionDropdownTop"
          :left="mentionDropdownLeft"
          @select="mentionInsert"
          @close="closeMention"
        />
      </div>
    </div>

  </div>
</template>

<style scoped>
.chat__timeline {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.timeline-event-enter-active {
  transition: opacity .26s ease, transform .26s cubic-bezier(.22, 1, .36, 1);
}

.timeline-event-enter-from {
  opacity: 0;
  transform: translateY(10px) scale(.985);
}

.timeline-event-move {
  transition: transform .26s cubic-bezier(.22, 1, .36, 1);
}

@media (prefers-reduced-motion: reduce) {
  .timeline-event-enter-active,
  .timeline-event-move {
    transition-duration: .01ms;
  }
}

.chat {
  display: flex; flex-direction: column;
  height: 100svh; overflow: hidden;
}

/* ---- top bar ---- */
.chat__topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 32px; border-bottom: 1px solid var(--border);
  background: var(--bg); flex-shrink: 0;
}
.chat__topbar-right { display: flex; align-items: center; gap: 6px; }

/* ---- topbar buttons ---- */
.chat__topbtn {
  position: relative;
  display: flex; align-items: center; justify-content: center;
  width: 30px; height: 30px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--bg-raised); color: var(--text-muted); cursor: pointer;
  transition: border-color 0.12s, color 0.12s;
}
.chat__topbtn:hover { border-color: var(--accent-border); color: var(--text-h); }
.chat__topbtn.is-on { border-color: var(--accent-border); background: var(--accent-bg); color: var(--accent-text); }
.chat__topbtn[title="Workspace"] { display: none; }
.chat__topbtn-badge {
  position: absolute; top: -4px; right: -4px;
  min-width: 14px; height: 14px; padding: 0 3px;
  border-radius: 7px; background: var(--accent); color: #fff;
  font-size: 9px; font-weight: 700; font-family: var(--mono);
  display: flex; align-items: center; justify-content: center; line-height: 1;
}

/* ---- body ---- */
.chat__body {
  position: relative;
  z-index: 2;
  display: flex;
  flex: 1;
  overflow: visible;
}
.chat__main { flex: 1; display: flex; flex-direction: column; overflow: hidden; max-width: 820px; margin: 0 auto; width: 100%; padding: 0 32px; }

/* ---- empty ---- */
.chat__empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 32px; }

/* ---- restoring ---- */
.chat__restoring {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  color: var(--text-muted);
}
.chat__restoring p {
  margin: 0;
  font: 12px/1 var(--sans);
}
.chat__restoring-spinner {
  width: 28px;
  height: 28px;
  border: 2.5px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: restore-spin .7s linear infinite;
}
@keyframes restore-spin { to { transform: rotate(360deg); } }

.chat__welcome { text-align: center; }
.chat__title { font-size: 28px; font-weight: 700; color: var(--text-h); margin: 0 0 6px; letter-spacing: -0.02em; }
.chat__subtitle { font-size: 14px; color: var(--text-muted); margin: 0; max-width: 360px; line-height: 1.55; }
.chat__hints { display: flex; flex-direction: column; gap: 8px; width: 100%; max-width: 440px; }
.chat__hint {
  width: 100%; padding: 12px 16px;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-raised); color: var(--text);
  font-size: 13px; font-family: var(--sans); text-align: left; cursor: pointer;
  transition: border-color 0.12s, background 0.12s;
}
.chat__hint:hover { border-color: var(--accent-border); background: var(--accent-bg); }

/* ---- messages ---- */
.chat__msgs { flex: 1; overflow-y: auto; padding: 24px 8px 16px 0; margin-right: 6px; display: flex; flex-direction: column; gap: 16px; }
.chat__msg { display: flex; }
.chat__msg--user { justify-content: flex-end; }

.chat__bubble {
  max-width: 82%;
  padding: 10px 14px;
  border-radius: var(--radius);
  font-size: 13px; line-height: 1.6; color: var(--text-h);
  white-space: pre-wrap; word-break: break-word;
}
.chat__msg--user .chat__bubble { background: var(--accent-bg); border: 1px solid var(--accent-border); }
.chat__msg--assistant .chat__bubble { background: var(--bg-raised); border: 1px solid var(--border); }
.chat__content { margin-top: 4px; }
.chat__msg-files { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }

/* ---- time ---- */
.chat__time { font-size: 10px; color: var(--text-muted); font-family: var(--mono); margin-bottom: 4px; }

/* ---- thinking ---- */
.think { margin-bottom: 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); overflow: hidden; background: var(--code-bg); cursor: pointer; }
.think--done { border-color: var(--warn); }
.think__bar { display: flex; align-items: center; gap: 7px; padding: 6px 10px; font-size: 11px; color: var(--text-muted); user-select: none; }
.think--done .think__bar { color: var(--warn); }
.think__icon { display: flex; color: var(--warn); }
.think__label { font-weight: 500; flex: 1; }
.think__dots { display: flex; gap: 3px; align-items: center; }
.think__dots i { width: 4px; height: 4px; border-radius: 50%; background: var(--text-muted); animation: dot-bounce 1.2s infinite; }
.think__dots i:nth-child(2) { animation-delay: 0.2s; }
.think__dots i:nth-child(3) { animation-delay: 0.4s; }
@keyframes dot-bounce { 0%, 60%, 100% { opacity: 0.3; transform: translateY(0); } 30% { opacity: 1; transform: translateY(-3px); } }
.think__chevron { flex-shrink: 0; opacity: 0.5; transition: transform 0.15s; }
.think__body { padding: 8px 10px; border-top: 1px solid var(--border); font-size: 11px; color: var(--text); line-height: 1.55; font-style: italic; }
.think-slide-enter-active { transition: all 0.18s ease; overflow: hidden; }
.think-slide-leave-active { transition: all 0.12s ease; overflow: hidden; }
.think-slide-enter-from, .think-slide-leave-to { opacity: 0; max-height: 0; }
.think-slide-enter-to, .think-slide-leave-from { max-height: 200px; }

/* ---- sub-agent dispatch ---- */
.sub { margin-bottom: 6px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--bg); overflow: hidden; }
.sub--done { border-color: var(--ok-border); }
.sub__bar { display: flex; align-items: center; gap: 6px; padding: 6px 10px; cursor: pointer; user-select: none; transition: background 0.1s; }
.sub__bar:hover { background: var(--code-bg); }
.sub__icon { display: flex; color: var(--accent); }
.sub__agent { font-size: 11px; font-weight: 600; font-family: var(--mono); color: var(--accent-text); flex-shrink: 0; }
.sub__task { font-size: 11px; color: var(--text-muted); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sub__spinner { width: 12px; height: 12px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: tc-spin 0.6s linear infinite; flex-shrink: 0; }
@keyframes tc-spin { to { transform: rotate(360deg); } }
.sub__check { display: flex; flex-shrink: 0; }
.sub__chevron { flex-shrink: 0; opacity: 0.4; transition: transform 0.15s; }
.sub__body { padding: 8px 10px; border-top: 1px solid var(--border); font-size: 11px; font-family: var(--mono); color: var(--text); line-height: 1.45; }
.sub-slide-enter-active { transition: all 0.18s ease; overflow: hidden; }
.sub-slide-leave-active { transition: all 0.12s ease; overflow: hidden; }
.sub-slide-enter-from, .sub-slide-leave-to { opacity: 0; max-height: 0; }
.sub-slide-enter-to, .sub-slide-leave-from { max-height: 200px; }

/* ---- tool calls ---- */
.tc { margin-bottom: 6px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--bg); overflow: hidden; }
.tc--done { border-color: var(--ok-border); }
.tc__bar { display: flex; align-items: center; gap: 8px; padding: 6px 10px; font-size: 11px; font-family: var(--mono); cursor: pointer; user-select: none; color: var(--text-muted); transition: background 0.1s; }
.tc__bar:hover { background: var(--code-bg); }
.tc__icon { display: flex; color: var(--accent); }
.tc__name { font-weight: 500; color: var(--text-h); flex: 1; }
.tc__spinner { width: 12px; height: 12px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: tc-spin 0.6s linear infinite; }
.tc__check { display: flex; }
.tc__chevron { flex-shrink: 0; opacity: 0.4; transition: transform 0.15s; }
.tc__body { border-top: 1px solid var(--border); padding: 8px 10px; display: flex; flex-direction: column; gap: 8px; }
.tc__section-label { font-size: 10px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 3px; }
.tc__pre { margin: 0; padding: 8px 10px; background: var(--code-bg); border-radius: 4px; font-family: var(--mono); font-size: 11px; line-height: 1.45; color: var(--text-h); white-space: pre-wrap; word-break: break-word; max-height: 160px; overflow-y: auto; }
.tc-slide-enter-active { transition: all 0.18s ease; overflow: hidden; }
.tc-slide-leave-active { transition: all 0.12s ease; overflow: hidden; }
.tc-slide-enter-from, .tc-slide-leave-to { opacity: 0; max-height: 0; }
.tc-slide-enter-to, .tc-slide-leave-from { max-height: 300px; }

/* ---- diff ---- */
.diff { margin: 8px 0; border: 1px solid var(--border); border-radius: var(--radius-sm); overflow: hidden; }
.diff__head { display: flex; align-items: center; gap: 8px; padding: 6px 10px; background: var(--code-bg); font-size: 11px; font-family: var(--mono); color: var(--text-muted); }
.diff__path { font-weight: 500; color: var(--text-h); flex: 1; }
.diff__tag { font-size: 10px; padding: 1px 5px; border-radius: 3px; font-weight: 600; }
.diff__tag--add { background: var(--ok-bg); color: var(--ok); }
.diff__tag--remove { background: var(--err-bg); color: var(--err); }
.diff__code { margin: 0; font-size: 12px; line-height: 1.6; }
.diff__code code { display: block; padding: 8px 0; overflow-x: auto; font-family: var(--mono); background: var(--code-bg); }
.diff__line { display: block; min-width: max-content; padding: 0 10px; white-space: pre; }
.diff__marker { display: inline-block; width: 1.5ch; user-select: none; }
.diff__line--add  { color: var(--ok);  background: var(--ok-bg);  }
.diff__line--keep { color: var(--text-muted); }
.diff__line--remove { color: var(--err); background: var(--err-bg); }
.diff__actions { display: flex; gap: 6px; padding: 8px 10px; border-top: 1px solid var(--border); background: var(--bg); }
.diff__accept, .diff__reject {
  display: inline-flex; align-items: center; gap: 4px;
  height: 26px; padding: 0 10px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  font-size: 11px; font-weight: 500; font-family: var(--sans); cursor: pointer;
  transition: border-color 0.1s, background 0.1s, color 0.1s;
}
.diff__accept { color: var(--ok); background: var(--ok-bg); border-color: var(--ok-border); }
.diff__accept:hover { background: rgba(5,150,105,0.15); }
.diff__reject { color: var(--err); background: var(--err-bg); border-color: var(--err-border); }
.diff__reject:hover { background: rgba(220,38,38,0.12); }
.diff__status { padding: 6px 10px; text-align: center; font-size: 11px; font-weight: 600; }
.diff__status--ok { color: var(--ok); background: var(--ok-bg); }
.diff__status--err { color: var(--err); background: var(--err-bg); }

/* ---- todo panel ---- */
.chat__todos {
  width: 240px; flex-shrink: 0;
  border-left: 1px solid var(--border); background: var(--bg-raised);
  display: flex; flex-direction: column; overflow: hidden;
}
.chat__todos-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; border-bottom: 1px solid var(--border); }
.chat__todos-title { font-size: 12px; font-weight: 600; color: var(--text-h); }
.chat__todos-count { font-size: 10px; color: var(--text-muted); font-family: var(--mono); }
.chat__todos-list { flex: 1; overflow-y: auto; padding: 6px; display: flex; flex-direction: column; gap: 2px; }
.chat__todo {
  display: flex; align-items: center; gap: 8px; padding: 7px 8px;
  border-radius: var(--radius-sm); cursor: pointer; transition: background 0.1s;
}
.chat__todo:hover { background: var(--accent-bg); }
.chat__todo.is-done .chat__todo-text { text-decoration: line-through; color: var(--text-muted); }
.chat__todo-check {
  flex-shrink: 0; width: 16px; height: 16px;
  border: 1.5px solid var(--border); border-radius: 3px;
  display: flex; align-items: center; justify-content: center;
}
.chat__todo.is-done .chat__todo-check { border-color: var(--ok-border); background: var(--ok-bg); }
.chat__todo-text { font-size: 12px; color: var(--text-h); line-height: 1.4; }

.panel-enter-active,
.panel-leave-active { transition: opacity 180ms ease, transform 220ms cubic-bezier(0.16, 1, 0.3, 1); }
.panel-enter-from,
.panel-leave-to { opacity: 0; transform: translateX(14px); }

/* ---- file context ---- */
.chat__files { display: flex; align-items: center; gap: 6px; padding: 6px 32px; flex-wrap: wrap; flex-shrink: 0; }
.chat__files-label { font-size: 10px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-right: 2px; }
.chat__file-chip {
  display: inline-flex; align-items: center; gap: 5px;
  height: 24px; padding: 0 8px;
  border: 1px solid var(--border); border-radius: 12px;
  font-size: 11px; font-family: var(--mono); color: var(--text-h); background: var(--bg-raised);
}
.chat__file-chip-dot { width: 6px; height: 6px; border-radius: 50%; }
.chat__file-chip-dot--python { background: #3572a5; }
.chat__file-chip-dot--javascript { background: #f0db4f; }
.chat__file-chip-dot--typescript { background: #3178c6; }
.chat__file-chip-x {
  display: flex; align-items: center; justify-content: center;
  width: 14px; height: 14px; padding: 0;
  border: none; border-radius: 50%; background: transparent; color: var(--text-muted); cursor: pointer;
}
.chat__file-chip-x:hover { background: var(--err-bg); color: var(--err); }

/* ---- foot ---- */
.chat__foot {
  position: relative;
  z-index: 1;
  flex-shrink: 0;
  border-top: 1px solid var(--border);
  background: var(--bg);
}
.chat__input-row { display: flex; align-items: flex-end; gap: 8px; padding: 8px 32px 14px; }
.chat__input {
  flex: 1; min-height: 36px; max-height: 120px;
  padding: 8px 12px;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-raised); color: var(--text-h);
  font: 13px/1.5 var(--sans); resize: none; outline: none;
  transition: border-color 0.12s;
}
.chat__input:focus { border-color: var(--accent-border); box-shadow: 0 0 0 3px var(--accent-bg); }
.chat__input::placeholder { color: var(--text-muted); }
.chat__input:disabled { opacity: 0.5; }

.chat__send {
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  width: 36px; height: 36px;
  border: none; border-radius: var(--radius);
  background: var(--accent); color: #fff; cursor: pointer;
  transition: background 0.12s, transform 0.1s;
}
.chat__send:hover { background: var(--accent-text); }
.chat__send:active { transform: scale(0.95); }
.chat__send:disabled { opacity: 0.35; cursor: default; transform: none; }

/* ---- code ---- */
.chat__code-block { margin: 8px 0; border-radius: var(--radius-sm); overflow: hidden; font-size: 12px; line-height: 1.55; }
.chat__code-block code { display: block; padding: 12px 14px; overflow-x: auto; font-family: var(--mono); background: var(--code-bg) !important; }
.chat__cursor { display: inline; color: var(--accent); animation: blink 1s step-end infinite; }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

/* workspace redesign */
.chat {
  height: 100%;
  flex: 1;
  min-height: 0;
  background: transparent;
}

.chat__topbar {
  min-height: 54px;
  padding: 0 18px;
  border-bottom-color: var(--border);
  background: rgba(255, 255, 255, 0.9);
}

.chat__session {
  display: flex;
  align-items: center;
  gap: 10px;
}

.chat__session-mark,
.chat__welcome-mark {
  color: var(--accent-text);
  font-family: var(--mono);
}

.chat__session-mark {
  font-size: 11px;
}

.chat__session > div {
  display: flex;
  flex-direction: column;
}

.chat__session strong {
  color: var(--text-h);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.25;
}

.chat__session small {
  color: var(--text-muted);
  font: 9px/1.3 var(--mono);
}

.chat__topbar-right {
  gap: 7px;
}

.chat__topbtn {
  width: auto;
  height: 32px;
  gap: 7px;
  padding: 0 9px;
  border-color: var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-raised);
}

.chat__topbtn:hover {
  border-color: var(--border-strong);
  color: var(--text-h);
}

.chat__topbtn.is-on {
  border-color: var(--accent-border);
  color: var(--accent-text);
  background: var(--accent-bg);
}

.chat__topbtn-label {
  font-size: 10px;
}

.chat__topbtn-badge {
  position: static;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 5px;
  color: var(--text-h);
  background: var(--code-bg);
}

.chat__body {
  position: relative;
  z-index: 2;
  min-height: 0;
  overflow: visible;
}

.chat__main {
  max-width: none;
  margin: 0;
  padding: 0;
}

@media (min-width: 981px) {
  .chat__body--has-panel .chat__main {
    margin-right: min(360px, calc(100% - 24px));
  }
}

.chat__empty {
  align-items: flex-start;
  justify-content: center;
  gap: 30px;
  width: min(760px, calc(100% - 64px));
  margin: 0 auto;
  padding: 36px 0 80px;
}

.chat__welcome {
  max-width: 590px;
  text-align: left;
}

.chat__welcome-mark {
  display: inline-block;
  margin-bottom: 18px;
  padding: 6px 8px;
  border: 1px solid var(--accent-border);
  border-radius: 7px;
  background: var(--accent-bg);
  font-size: 11px;
}

.chat__title {
  margin: 0;
  color: var(--text-h);
  font: 560 clamp(30px, 4.2vw, 46px)/1.02 var(--heading);
  letter-spacing: -0.045em;
}

.chat__subtitle {
  max-width: 520px;
  margin: 14px 0 0;
  color: #5f7193;
  font-size: 13px;
  line-height: 1.6;
}

.chat__hints {
  width: min(620px, 100%);
  max-width: none;
  gap: 0;
  border-top: 1px solid var(--border);
}

.chat__hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 58px;
  padding: 10px 2px;
  border: 0;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  color: var(--text);
  background: transparent;
}

.chat__hint:hover {
  padding-inline: 10px;
  border-color: var(--border-strong);
  color: var(--accent-text);
  background: var(--accent-bg);
}

.chat__hint > span {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.chat__hint strong {
  color: var(--text-h);
  font-size: 11px;
  font-weight: 600;
}

.chat__hint small {
  color: var(--text-muted);
  font-size: 10px;
}

.chat__msgs {
  width: 100%;
  margin: 0;
  padding: 30px 0 22px;
  gap: 24px;
  scrollbar-gutter: stable;
  scrollbar-color: rgba(95, 113, 147, .42) transparent;
  scrollbar-width: thin;
}

.chat__msgs::-webkit-scrollbar {
  width: 12px;
}

.chat__msgs::-webkit-scrollbar-track {
  background: transparent;
}

.chat__msgs::-webkit-scrollbar-thumb {
  border: 4px solid transparent;
  border-radius: 999px;
  background: rgba(95, 113, 147, .38);
  background-clip: padding-box;
}

.chat__msgs::-webkit-scrollbar-thumb:hover {
  background: rgba(23, 86, 209, .52);
  background-clip: padding-box;
}

.chat__msg {
  width: min(900px, calc(100% - 48px));
  margin-inline: auto;
  padding-inline: 4px;
}

.chat__bubble {
  max-width: min(760px, 88%);
  padding: 0;
  border-radius: 0;
  color: var(--text);
  font-size: var(--font-body);
  line-height: 1.65;
}

.chat__msg--user .chat__bubble {
  padding: 12px 16px;
  border: 1px solid color-mix(in srgb, var(--accent) 24%, #cfddf5);
  border-radius: 16px 16px 5px 16px;
  color: var(--text-h);
  background: linear-gradient(135deg, rgba(23,86,209,.08), rgba(23,86,209,.035));
  box-shadow: 0 2px 10px rgba(23,86,209,.07);
  font-size: 14px;
  line-height: 1.6;
  position: relative;
  user-select: text;
  cursor: text;
}

.chat__msg--user .chat__time {
  margin-top: 3px;
  margin-bottom: 0;
  text-align: right;
  color: color-mix(in srgb, var(--accent) 30%, var(--text-muted));
  font-size: 8.5px;
  letter-spacing: .02em;
}

.chat__msg--assistant .chat__bubble {
  width: 100%;
  max-width: 780px;
  padding-left: 16px;
  border: 0;
  border-left: 2px solid var(--border-strong);
  background: transparent;
}

.chat__time {
  margin-bottom: 6px;
  color: var(--text-muted);
  font-size: 9px;
}

.think,
.sub,
.tc {
  border-color: var(--border);
  border-radius: var(--radius-sm);
  background: var(--code-bg);
}

.think--done,
.sub--done,
.tc--done {
  border-color: var(--border-strong);
}

.think--done .think__bar,
.think__icon,
.sub__icon,
.tc__icon,
.sub__agent {
  color: var(--accent-text);
}

.think__body,
.sub__body,
.tc__body {
  border-top-color: var(--border);
  color: var(--text);
}

.diff {
  margin: 12px 0;
  border-color: var(--border-strong);
  border-radius: var(--radius);
  background: var(--code-bg);
}

.diff__head {
  min-height: 38px;
  padding: 0 12px;
  border-bottom: 1px solid var(--border);
  background: #eef4ff;
}

.diff__tag {
  border-radius: 5px;
  font-family: var(--mono);
}

.diff__code code {
  background: var(--code-bg);
}

.diff__line {
  padding-inline: 12px;
}

.diff__actions {
  padding: 9px 12px;
  border-top-color: var(--border);
  background: #ffffff;
}

.diff__accept,
.diff__reject {
  height: 28px;
  border-radius: 7px;
  background: transparent;
}

.chat__todos {
  width: 280px;
  border-left-color: var(--border);
  background: #ffffff;
}

.chat__todos-head {
  min-height: 52px;
  padding: 0 15px;
  border-bottom-color: var(--border);
}

.chat__todos-title {
  color: var(--text-h);
  font: 600 11px/1 var(--sans);
}

.chat__todos-count {
  color: var(--text-muted);
  font-size: 9px;
}

.chat__todos-list {
  gap: 3px;
  padding: 10px 8px;
}

.chat__todo {
  padding: 8px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
}

.chat__todo:hover {
  border-color: var(--border);
  background: var(--code-bg-hover);
}

.chat__todo-check {
  width: 15px;
  height: 15px;
  border-color: var(--border-strong);
  border-radius: 4px;
}

.chat__todo-text {
  color: var(--text);
  font-size: 10.5px;
}

.chat__foot {
  position: relative;
  z-index: 1;
  padding: 12px 24px 18px;
  border-top: 0;
  background: linear-gradient(transparent, var(--bg) 28%);
}

.chat__composer {
  width: min(880px, 100%);
  margin: 0 auto;
  overflow: hidden;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  background: #ffffff;
  box-shadow: 0 18px 44px rgba(23, 72, 150, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.7);
  transition: border-color var(--transition), box-shadow var(--transition);
}

.chat__composer:focus-within {
  border-color: var(--accent-border);
  box-shadow: 0 18px 44px rgba(23, 72, 150, 0.16), 0 0 0 3px var(--accent-bg);
}

.chat__runbar {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 16px;
  overflow-x: auto;
  padding: 11px 16px 10px;
  color: var(--text-muted);
  font: 10px/1.25 var(--mono);
  scrollbar-width: none;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(180deg, #fbfdff, #f4f8fe);
  transition: background var(--transition);
}

.chat__runbar::-webkit-scrollbar { display: none; }

.chat__runbar.is-running {
  background: linear-gradient(180deg, #fffefa, #fef9e8);
}

.chat__runstate {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-shrink: 0;
}

.chat__runstate i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #94a8c2;
  box-shadow: 0 0 0 3px rgba(148, 168, 194, 0.22);
  flex-shrink: 0;
  transition: background var(--transition), box-shadow var(--transition);
}

.chat__runstate span {
  color: var(--text-h);
  font-weight: 700;
  font-size: 10px;
  letter-spacing: -0.01em;
}

.chat__runstate small {
  color: var(--text-muted);
  font-size: 9px;
  max-width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat__runbar.is-running .chat__runstate i {
  background: var(--yellow);
  box-shadow: 0 0 0 3px rgba(245, 200, 66, 0.3), 0 0 12px rgba(245, 200, 66, 0.65);
  animation: status-pulse 1.2s ease-in-out infinite;
}

.chat__runbar.is-running .chat__runstate span {
  color: #5c4200;
}

.chat__meter {
  --context: 0%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 160px;
}

.chat__meter span {
  display: flex;
  align-items: baseline;
  gap: 6px;
  white-space: nowrap;
}

.chat__meter b {
  color: var(--accent-text);
  font-weight: 800;
  font-size: 11px;
}

.chat__meter small {
  color: #8192aa;
  font-size: 9px;
}

.chat__meter em {
  height: 7px;
  overflow: hidden;
  border-radius: 99px;
  background: #e1eafa;
  box-shadow: inset 0 1px 3px rgba(23, 72, 150, 0.07);
}

.chat__meter em i {
  display: block;
  width: var(--context);
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #1756d1, #4f8af7);
  transition: width .36s cubic-bezier(.22,1,.36,1);
  position: relative;
}

.chat__meter em i::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
  opacity: 0;
}

.chat__runbar.is-running .chat__meter em i {
  background: linear-gradient(90deg, #1756d1, #4f8af7, #f5c842);
}

.chat__runbar.is-running .chat__meter em i::after {
  opacity: 1;
  animation: meter-shimmer 2.4s ease-in-out infinite;
}

.chat__usage-strip {
  display: flex;
  gap: 14px;
  flex-shrink: 0;
}

.chat__usage-strip span {
  display: flex;
  flex-direction: column;
  gap: 1px;
  position: relative;
}

.chat__usage-strip span + span::before {
  content: '';
  position: absolute;
  left: -7px;
  top: 3px;
  bottom: 2px;
  width: 1px;
  background: #dce5f3;
}

.chat__usage-strip span:last-child { border-right: 0; }
.chat__usage-strip small {
  color: #8292a8;
  font-size: 7.5px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.chat__usage-strip b { color: var(--text-h); font-weight: 700; font-size: 10px; }

.chat__model {
  flex-shrink: 0;
  max-width: min(200px, 28vw);
  overflow: hidden;
  padding: 3px 8px;
  border-radius: 5px;
  color: var(--text-muted);
  background: rgba(23, 86, 209, 0.05);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-left: auto;
}

.chat__files {
  padding: 7px 11px 0;
  gap: 6px;
}

.chat__files-label {
  margin-right: 3px;
  color: var(--text-muted);
  font: 9px/1 var(--mono);
  letter-spacing: 0;
  text-transform: none;
}

.chat__file-chip {
  height: 23px;
  padding: 0 6px;
  border-color: var(--border);
  border-radius: 6px;
  color: var(--text);
  background: var(--code-bg);
  font-size: 9px;
}

.chat__file-ext {
  color: var(--accent-text);
  font-size: 8px;
}

.chat__file-chip-x:hover {
  color: var(--err);
  background: transparent;
}

.chat__input-row {
  gap: 10px;
  padding: 9px 10px 5px 14px;
}

.chat__input {
  min-height: 42px;
  padding: 9px 0;
  border: 0;
  border-radius: 0;
  color: var(--text-h);
  background: transparent;
  font-size: var(--font-body);
}

.chat__input:focus {
  border: 0;
  box-shadow: none;
}

.chat__input::placeholder {
  color: #91a0ba;
}

.chat__send {
  width: 34px;
  height: 34px;
  align-self: flex-end;
  border: 1px solid var(--accent);
  border-radius: 9px;
  color: #ffffff;
  background: var(--accent);
}

.chat__send:hover {
  background: var(--accent-hover);
}

.chat__composer-meta {
  display: flex;
  justify-content: space-between;
  padding: 0 13px 9px;
  color: var(--text-muted);
  font: 8.5px/1 var(--mono);
}

.chat__code-block {
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.chat__code-block code {
  background: var(--code-bg) !important;
}

@keyframes status-pulse { 50% { transform: scale(1.45); opacity: .58; } }

@keyframes meter-shimmer {
  0%, 100% { opacity: 0; transform: translateX(-100%); }
  60% { opacity: 1; transform: translateX(100%); }
}

.chat__tool-grid {
  display: grid;
  grid-template-columns: repeat(2, 4px);
  gap: 2px;
}

.chat__tool-grid i {
  width: 4px;
  height: 4px;
  border-radius: 1px;
  background: currentColor;
}

.chat__topbtn--tools.is-on {
  color: #0f3f9e;
  border-color: var(--yellow-border);
  background: var(--yellow-bg);
}

.chat__topbtn-badge--yellow {
  color: #103b91;
  background: var(--yellow);
}

.tc {
  overflow: hidden;
  border: 1px solid var(--border);
  border-left-width: 3px;
  border-radius: var(--radius);
  background: #ffffff;
  box-shadow: var(--shadow-sm);
}

.tc--blue { border-left-color: var(--accent); }
.tc--yellow { border-left-color: var(--yellow); }
.tc--red { border-left-color: var(--red); }

.tc__bar {
  min-height: 52px;
  padding: 7px 10px;
  color: var(--text);
  background: #ffffff;
}

.tc__bar:hover { background: #f8faff; }

.tc__icon,
.tool-card__icon {
  display: grid;
  place-items: center;
  border-radius: 7px;
  font: 700 10px/1 var(--mono);
}

.tc__icon {
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
}

.tc--blue .tc__icon,
.tool-card--blue .tool-card__icon {
  color: #ffffff;
  background: var(--accent);
}

.tc--yellow .tc__icon,
.tool-card--yellow .tool-card__icon {
  color: #103b91;
  background: var(--yellow);
}

.tc--red .tc__icon,
.tool-card--red .tool-card__icon {
  color: #ffffff;
  background: var(--red);
}

.tc__title {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.tc__title strong {
  color: var(--text-h);
  font: 650 11px/1.25 var(--mono);
}

.tc__title small {
  overflow: hidden;
  color: var(--text-muted);
  font: 9px/1.35 var(--sans);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tc__state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 7px;
  border-radius: 6px;
  font: 650 8px/1 var(--mono);
}

.tc__state--running {
  color: #735300;
  background: var(--yellow-bg);
}

.tc__state--done {
  color: var(--accent-text);
  background: var(--accent-bg);
}

.tc__spinner {
  width: 9px;
  height: 9px;
  border-color: rgba(115, 83, 0, 0.22);
  border-top-color: #9b6a00;
}

.tc__body {
  gap: 10px;
  padding: 10px 12px;
  border-top-color: var(--border);
  background: #f8faff;
}

.tc__pre {
  border: 1px solid var(--border);
  color: #20365e;
  background: #ffffff;
}

.chat__tools {
  display: flex;
  width: 330px;
  flex: 0 0 330px;
  flex-direction: column;
  overflow: hidden;
  border-left: 1px solid var(--border);
  background: #ffffff;
}

.chat__tools-head {
  display: flex;
  min-height: 70px;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(135deg, #edf4ff, #fff9d8);
}

.chat__tools-head > div,
.chat__tools-title,
.chat__tools-subtitle {
  display: flex;
  flex-direction: column;
}

.chat__tools-title {
  color: var(--text-h);
  font-size: 12px;
  font-weight: 700;
}

.chat__tools-subtitle {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 9.5px;
}

.chat__tools-total {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  border: 1px solid var(--yellow-border);
  border-radius: 8px;
  color: #103b91;
  background: var(--yellow);
  font: 700 11px/1 var(--mono);
}

.chat__tools-list {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 7px;
  overflow-y: auto;
  padding: 12px;
}

.tool-card {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
  min-height: 62px;
  padding: 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: #ffffff;
}

.tool-card--blue:hover { border-color: var(--accent-border); background: #f5f8ff; }
.tool-card--yellow:hover { border-color: var(--yellow-border); background: #fffbed; }
.tool-card--red:hover { border-color: var(--red-border); background: var(--red-bg); }

.tool-card__icon {
  width: 30px;
  height: 30px;
}

.tool-card__copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.tool-card__copy strong {
  color: var(--text-h);
  font: 650 10.5px/1.3 var(--mono);
}

.tool-card__copy small {
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 9px;
  line-height: 1.35;
}

.tool-card__params {
  padding: 3px 5px;
  border-radius: 5px;
  color: #5f7193;
  background: var(--bg-overlay);
  font: 8px/1 var(--mono);
  white-space: nowrap;
}

.chat__tools-foot {
  padding: 11px 14px;
  border-top: 1px solid var(--border);
  color: #725500;
  background: var(--yellow-bg);
  font-size: 9.5px;
  line-height: 1.4;
}

.panel-enter-active,
.panel-leave-active {
  transition: opacity 180ms ease, transform 220ms cubic-bezier(0.16, 1, 0.3, 1);
}

.panel-enter-from,
.panel-leave-to {
  opacity: 0;
  transform: translateX(14px);
}

@media (max-width: 780px) {
  .chat__topbtn-label,
  .chat__session small {
    display: none;
  }

  .chat__empty {
    width: calc(100% - 36px);
    padding-bottom: 40px;
  }

  .chat__msgs {
    width: 100%;
  }

  .chat__msg {
    width: calc(100% - 28px);
    padding-inline: 0;
  }

  .chat__bubble {
    max-width: 94%;
  }

  .chat__todos,
  .chat__tools {
    position: absolute;
    inset: 0 0 0 auto;
    z-index: 10;
    box-shadow: -18px 0 40px rgba(23, 72, 150, 0.18);
  }

  .chat__tools {
    width: min(330px, calc(100% - 30px));
    flex-basis: auto;
  }

  .chat__foot {
    padding: 10px 12px 12px;
  }
}

@media (max-width: 520px) {
  .chat__topbar { padding-inline: 12px; }
  .chat__session > div { display: none; }
  .chat__title { font-size: 30px; }
  .chat__hint small { display: none; }
  .chat__file-chip { max-width: 190px; overflow: hidden; text-overflow: ellipsis; }
}
</style>
