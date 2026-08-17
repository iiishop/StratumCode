<script setup>
import { ref, reactive, computed, provide, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { gsap } from 'gsap'
import ChatTimeline from './chat/ChatTimeline.vue'
import ChatComposer from './chat/ChatComposer.vue'
import FileReference from './FileReference.vue'
import { useChatStream } from '../composables/useChatStream'
import InspectorPanel from './inspector/InspectorPanel.vue'
import InspectorRail from './inspector/InspectorRail.vue'
import { extractInlineFileRefs, languageFromPath, tokenizeInlineFileRefs } from '../lib/fileRefs'

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
])

/* ── todos ── */
const inspectorTab = ref(null)
const inspectorOpen = ref(false)
const inspectorWidth = ref(392)
const inspectorRail = ref(null)
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
const isAtMessageBottom = ref(true)
const isAutoScrollingMessages = ref(false)
const currentChatState = ref('initializing')
const restoring = ref(false)
const copySessionStatus = ref('')
let copySessionTimer
const MESSAGE_BOTTOM_THRESHOLD = 28

function addToFileContext(path) {
  if (fileContext.find(f => f.path === path)) return
  fileContext.push({ path, lang: languageFromPath(path) })
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
  {
    id: 'available-skill-placer',
    name: '@skill-placer',
    task: 'Decide which skill target (global, state, or subagent) best fits a skill.',
    status: 'ready',
    result: 'Available from the Skills page and the subagent tool.',
  },
])
const evidenceRun = computed(() => evidenceRuns.find(run => run.id === activeRunId.value) || emptyEvidenceRun)
const activeTaskAnalysis = computed(() => taskAnalyses[taskAnalyses.length - 1] || null)
const visibleSubagents = computed(() => {
  const byName = new Map()
  for (const agent of availableSubagents) byName.set(agent.name, agent)
  for (const agent of subagentRuns) byName.set(agent.name, agent)
  return [...byName.values()]
})
const inspectorTabs = computed(() => [
  {
    id: 'evidence',
    label: 'Evidence',
    icon: '◎',
    color: '#1756d1',
    soft: '#e8f0ff',
    description: 'Hypotheses, supporting facts, and verdicts for this run.',
    count: evidenceRuns.length,
  },
  {
    id: 'terminal',
    label: 'Terminal',
    icon: '>_',
    color: '#12846f',
    soft: '#e3f5f0',
    description: 'Background commands launched by the workspace.',
  },
  {
    id: 'git',
    label: 'Git',
    icon: 'G',
    color: '#2f6edb',
    soft: '#e8f0ff',
    description: 'Local changes, remote refs, and commit graph.',
  },
  {
    id: 'mcp',
    label: 'MCP',
    icon: 'M',
    color: '#8f45d8',
    soft: '#f0e7fb',
    description: 'Connected MCP servers and exposed tools.',
    count: props.mcpServers.length,
  },
  {
    id: 'subagents',
    label: 'Agents',
    icon: '@',
    color: '#cf4d78',
    soft: '#fae6ee',
    description: 'Delegated work and agent results.',
    count: visibleSubagents.value.length,
  },
  {
    id: 'tasks',
    label: 'Tasks',
    icon: 'T',
    color: '#c57716',
    soft: '#f9eddc',
    description: 'Task contract, unknowns, and acceptance criteria.',
    count: todos.filter(t => !t.done).length,
  },
  {
    id: 'tools',
    label: 'Tools',
    icon: '#',
    color: '#536675',
    soft: '#e9eef2',
    description: 'Built-in tools available to the agent.',
    count: toolCatalog.value.length,
  },
])
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
let saveTimerSessionId = null

function setMsgRef(id, el) { if (el) msgRefs[id] = el }

function timeNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function toggleTodo(id) {
  const t = todos.find(t => t.id === id)
  if (t) t.done = !t.done
}

function toggleInspector(tab) {
  if (inspectorOpen.value && inspectorTab.value === tab) {
    closeInspector()
    return
  }
  openInspector(tab)
}

function openInspector(tab) {
  inspectorTab.value = tab
  inspectorOpen.value = true
}

function closeInspector() {
  inspectorOpen.value = false
}

function panelAfterLeave() {
  if (!inspectorOpen.value) inspectorTab.value = null
}

function panelTravel(el) {
  return el.getBoundingClientRect().width
}

function railElement() {
  return inspectorRail.value?.$el || inspectorRail.value
}

function railDockX(width) {
  return window.matchMedia('(max-width: 980px)').matches ? 48 - width : -width
}

function panelEnter(el, done) {
  const rail = railElement()
  const travel = panelTravel(el)
  const dockX = railDockX(travel)
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    if (rail) gsap.set(rail, { x: dockX })
    done()
    return
  }
  gsap.killTweensOf([el, rail].filter(Boolean))
  const timeline = gsap.timeline({
    defaults: { duration: 0.36, ease: 'power3.out' },
    onComplete: () => {
      gsap.set(el, { clearProps: 'transform,opacity,visibility' })
      done()
    },
  })
  timeline.fromTo(el, { x: travel, autoAlpha: 0 }, { x: 0, autoAlpha: 1 }, 0)
  if (rail) timeline.to(rail, { x: dockX }, 0)
}

function panelLeave(el, done) {
  const rail = railElement()
  const travel = panelTravel(el)
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    if (rail) gsap.set(rail, { x: 0 })
    done()
    return
  }
  gsap.killTweensOf([el, rail].filter(Boolean))
  const timeline = gsap.timeline({
    defaults: { duration: 0.32, ease: 'power2.inOut' },
    onComplete: done,
  })
  timeline.to(el, { x: travel, autoAlpha: 0 }, 0)
  if (rail) timeline.to(rail, { x: 0 }, 0)
}

watch(inspectorWidth, (width) => {
  if (!inspectorOpen.value) return
  const rail = railElement()
  if (rail) gsap.set(rail, { x: railDockX(width) })
})

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

function plain(value) {
  return JSON.parse(JSON.stringify(value))
}

function parseJsonField(value) {
  if (typeof value === 'string') {
    try { return JSON.parse(value) } catch { return value }
  }
  return value
}

function summarizeArgs(args) {
  if (!args || typeof args !== 'object') return {}
  const keys = ['operation', 'symbol', 'path', 'name', 'action', 'pattern', 'limit', 'line', 'character', 'target_unknown_ids', 'question', 'reason', 'status']
  const out = {}
  for (const k of keys) {
    if (args[k] !== undefined && args[k] !== null) {
      out[k] = typeof args[k] === 'object' ? JSON.stringify(args[k]).slice(0, 120) : String(args[k]).slice(0, 120)
    }
  }
  return out
}

// AI 友好的扁平轨迹视图：摊平 messages/events，统一工具字段，解析 input 为对象。
// 供外部分析（如 AI 审查 investigation 行为）直接读取，避免深嵌套/类型混乱。
function buildTrace(messages) {
  const trace = []
  for (const msg of messages) {
    if (!msg || typeof msg !== 'object') continue
    const role = msg.role || ''
    const time = msg.time || ''
    if (role === 'user' || role === 'system') {
      const content = typeof msg.content === 'string' ? msg.content : ''
      if (content.trim()) {
        trace.push({ t: time, role, stage: '', tool: '', status: '', args: { content: content.slice(0, 200) }, error: '' })
      }
      continue
    }
    const events = Array.isArray(msg.events) ? msg.events : []
    for (const ev of events) {
      if (!ev || typeof ev !== 'object') continue
      const type = ev.type || ''
      const d = ev.data && typeof ev.data === 'object' ? ev.data : {}
      const name = d.name || ''
      const status = d.status || ''
      const parsed = parseJsonField(d.input)
      const args = parsed && typeof parsed === 'object' ? parsed : { raw: String(parsed ?? '').slice(0, 200) }
      const out = parseJsonField(d.output)
      let error = ''
      if (out && typeof out === 'object' && out.error) {
        error = typeof out.error === 'string' ? out.error : JSON.stringify(out.error).slice(0, 250)
      } else if (typeof out === 'string') {
        error = ''
      }
      const t = ev.createdAt
        ? String(ev.createdAt).length > 12 ? new Date(Number(ev.createdAt)).toISOString().slice(11, 19) : String(ev.createdAt)
        : time
      trace.push({
        t,
        role,
        stage: type === 'stage' ? (d.phase || '') : '',
        tool: name || (type === 'stage' || type === 'skill' ? '' : type),
        status,
        args: summarizeArgs(args),
        error: error.slice(0, 250),
      })
    }
  }
  return trace
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

function sessionExport() {
  return {
    exported_at: new Date().toISOString(),
    url: window.location.href,
    session: sessionExportMetadata(),
    activeWorkspace: props.activeWorkspace ? plain(props.activeWorkspace) : null,
    draft: input.value,
    agentStatus: plain(agentStatus),
    state: snapshotState(),
    trace: buildTrace(messages),
  }
}

function sessionExportMetadata() {
  if (!props.session) return null
  const { state_json, usage_json, state, usage, ...session } = plain(props.session)
  return session
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

async function copyCurrentSession() {
  clearTimeout(copySessionTimer)
  try {
    await writeClipboard(JSON.stringify(sessionExport(), null, 2))
    copySessionStatus.value = 'Session copied'
  } catch (error) {
    copySessionStatus.value = `Copy failed: ${error?.message || 'clipboard unavailable'}`
  } finally {
    copySessionTimer = setTimeout(() => { copySessionStatus.value = '' }, 2200)
  }
}

function scheduleSave() {
  const sessionId = props.session?.id
  if (!sessionId) return
  clearTimeout(saveTimer)
  saveTimerSessionId = sessionId
  saveTimer = setTimeout(() => {
    saveTimer = null
    const targetSessionId = saveTimerSessionId
    saveTimerSessionId = null
    saveSessionState(targetSessionId)
  }, 220)
}

function saveSessionState(sessionId = props.session?.id) {
  if (!sessionId) return
  emit('save-session-state', {
    session_id: sessionId,
    state: snapshotState(),
  })
}

function flushPendingSave(sessionId = saveTimerSessionId || props.session?.id) {
  if (!saveTimer) return
  clearTimeout(saveTimer)
  saveTimer = null
  const targetSessionId = sessionId || saveTimerSessionId
  saveTimerSessionId = null
  saveSessionState(targetSessionId)
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
  isAtMessageBottom.value = true
  isAutoScrollingMessages.value = false
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

function contextPathsFor(text) {
  const paths = [
    ...extractInlineFileRefs(text).map(file => file.path),
    ...fileContext.map(file => file.path),
  ]
  return [...new Set(paths)]
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

const activeQuestion = computed(() => {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i]
    const event = message.events?.find(item => item.type === 'user_question' && item.data?.answer_status !== 'submitted')
    if (event) return event.data
  }
  return null
})

async function send(answer = null) {
  if (answer) {
    await continueAfterAnswer(answer)
    return
  }
  const text = input.value.trim()
  if (!text || isStreaming.value) return
  messages.push({
    id: Date.now(),
    role: 'user',
    content: text,
    time: timeNow(),
    files: plain(fileContext),
    inlineFiles: extractInlineFileRefs(text),
  })
  const message = reactive({ id: Date.now() + 1, role: 'assistant', time: timeNow(), events: [] })
  messages.push(message)
  input.value = ''
  isStreaming.value = true
  currentChatState.value = 'initializing'
  Object.assign(agentStatus, { state: 'running', phase: 'starting', contextUsed: 0 })
  openInspector('evidence')
  nextTick(() => { scrollBottom(); animateLast() })
  let completedNormally = false
  try {
    const request = {
      message: text,
      context: contextPathsFor(text),
      session_id: props.session?.id,
    }
    await chatStream(message, request)
    completedNormally = true
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
    if (completedNormally && props.session?.id) {
      void generateSessionTitle(props.session.id, text, message)
    }
    nextTick(scrollForNewContent)
  }
}

async function generateSessionTitle(sessionId, userText, assistantMsg) {
  try {
    const output = assistantMsg.events
      .filter(e => e.type === 'output')
      .map(e => e.data?.content || '')
      .join('\\n')
    if (!output) return
    const response = await fetch('/api/sessions/generate-title', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: sessionId, user_message: userText, ai_response: output }),
    })
    const data = await response.json().catch(() => ({}))
    const title = data?.title
    if (!title) return
    // 无条件写回 props.sessions 对应项，确保异步期间切换会话后标题仍能更新会话列表
    const item = props.sessions?.find(s => s.id === sessionId)
    if (item) item.name = title
    // 若仍指向同一会话，同步更新 props.session.name 以触发 sessionName 重算与界面刷新
    // 根因说明：props.session 即 App.vue 中 activeSession（sessionStore.active.value）的同一响应式对象引用，
    // 因此对 props.session.name 赋值即等价于对 sessionStore.active.value.name 赋值。
    if (props.session?.id === sessionId) {
      props.session.name = title
      // 显式更新 sessionName 计算属性依赖的响应式字段 sessionStore.active.value.name，
      // 确保标题生成完成后界面立即刷新（props.session 即 sessionStore.active.value 的同一对象）。
      sessionStore.active.value.name = title
    }
  } catch {
    // Title generation is non-critical
  } finally {
    // 请求完成后（无论成功失败）清除标题生成中标志，结束加载动画
    if (target) target.titleGenerating = false
    if (props.session?.id === sessionId) props.session.titleGenerating = false
  }
}

function stopChat() {
  if (!isStreaming.value) return
  abortChat()
  const message = [...messages].reverse().find(item => item.role === 'assistant')
  message?.events?.push({
    id: `${message.id}-stopped`,
    type: 'state_transition',
    data: reactive({
      from_state: currentChatState.value || 'initializing',
      to_state: 'completed',
      reason: 'Stopped by user.',
    }),
    createdAt: Date.now(),
  })
  isStreaming.value = false
  Object.assign(agentStatus, { state: 'idle', phase: 'completed' })
  scheduleSave()
  nextTick(scrollForNewContent)
}

async function continueAfterAnswer(answer) {
  if (!answer) return
  const { message, event } = questionEventFor(answer)
  // 所有 user_question（模型 clearify 工具 / 系统 fallback / design / validation）都能回答，
  // 不要求 clearify_tool 标记——系统 fallback 提问没有该字段，之前导致回答被静默丢弃。
  if (!message || !event?.data?.id) return
  if (event?.data) Object.assign(event.data, {
    answer_status: 'submitted',
    selected_option_id: answer.selected_option_id,
    selected_option_label: answer.selected_option_label,
    response: answer.response,
  })
  const response = await fetch('/api/chat/answer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(answer),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `Answer submit failed (${response.status})`)
  // Immediately persist answer_status='submitted' — no debounce window.
  // Vue 3 emit is fire-and-forget; the parent saveActiveSessionState awaits the backend.
  const sessionId = props.session?.id
  if (sessionId) saveSessionState(sessionId)
}

async function answerQuestion(answer) {
  try {
    await continueAfterAnswer(answer)
  } catch (error) {
    const { message, event } = questionEventFor(answer)
    // Roll back optimistic UI update so the question reverts to pending.
    if (event?.data) {
      event.data.answer_status = 'pending'
      delete event.data.selected_option_id
      delete event.data.selected_option_label
      delete event.data.response
    }
    message?.events?.push({
      id: `${message.id}-answer-submit-error`,
      type: 'output',
      data: reactive({ content: `Answer submit failed: ${error.message}`, streaming: false }),
    })
  }
}

function userContentParts(message) {
  return message.inlineFiles?.length ? tokenizeInlineFileRefs(message.content) : [{ type: 'text', text: message.content }]
}
/* ── user bubble actions ── */
const copiedMsgId = ref(null)
let copyMsgTimer
const userBubbleActions = [
  { id: 'copy', label: '复制', icon: '⧉' },
]

async function copyUserMessage(m) {
  const text = userContentParts(m)
    .filter(part => part.type === 'text')
    .map(part => part.text)
    .join('')
  await writeClipboard(text)
  copiedMsgId.value = m.id
  clearTimeout(copyMsgTimer)
  copyMsgTimer = setTimeout(() => { copiedMsgId.value = null }, 2200)
}

function applyTaskUpdate(update) {
  const analysis = analysisForId(update?.analysis_id) || activeTaskAnalysis.value
  if (!analysis || !Array.isArray(update?.items)) return []
  analysis.task_updates = update.items
  return Array.isArray(update.changes) ? update.changes : update.items.map(item => ({ action: 'update', item }))
}

function analysisForId(id) {
  return id ? taskAnalyses.find(analysis => analysis.id === id) : null
}

/* ── animation helpers ──────────────────────────────────── */

function isMessageListAtBottom() {
  const el = msgList.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight <= MESSAGE_BOTTOM_THRESHOLD
}

function updateMessageScrollState() {
  if (isAutoScrollingMessages.value) return
  isAtMessageBottom.value = isMessageListAtBottom()
}

function handleMessageScrollIntent() {
  if (!msgList.value) return
  gsap.killTweensOf(msgList.value)
  isAutoScrollingMessages.value = false
  updateMessageScrollState()
}

function scrollForNewContent() {
  if (!isAtMessageBottom.value) return
  animSmoothScroll()
}

function animSmoothScroll() {
  if (!msgList.value) return
  isAutoScrollingMessages.value = true
  gsap.to(msgList.value, {
    scrollTop: msgList.value.scrollHeight,
    duration: 0.25,
    ease: 'power2.out',
    onComplete: () => {
      isAutoScrollingMessages.value = false
      isAtMessageBottom.value = true
    },
    onInterrupt: () => {
      isAutoScrollingMessages.value = false
      updateMessageScrollState()
    },
  })
}

function scrollBottom() {
  if (!msgList.value) return
  gsap.killTweensOf(msgList.value)
  isAutoScrollingMessages.value = false
  msgList.value.scrollTop = msgList.value.scrollHeight
  isAtMessageBottom.value = true
}

function upsertSubagent(data) {
  const existing = subagentRuns.find(agent =>
    (data.id && agent.id === data.id) || (!data.id && data.name && agent.name === data.name)
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
    data.open = true
    taskAnalyses.push(data)
    openInspector('tasks')
  } else if (packet.op === 'start' && type === 'task_update') {
    data.changes = applyTaskUpdate(data)
    openInspector('tasks')
  } else if (packet.op === 'start' && type === 'user_question') {
    const analysis = analysisForId(data.analysis_id) || activeTaskAnalysis.value
    data.analysis_id ||= analysis?.id || ''
    data.origin_message ||= analysis?.origin_message || ''
    data.answer_status ||= 'pending'
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
    upsertSubagent({ id: packet.id, ...data })
  } else if (packet.op === 'update' && type === 'subagent') {
    upsertSubagent({ id: packet.id, ...data })
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
  } else if (packet.op === 'start' && type === 'state_transition') {
    currentChatState.value = data.to_state || currentChatState.value
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

const { stream: chatStream, abort: abortChat } = useChatStream(scrollForNewContent, onAgentPacket)

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
  }, chatRef.value)
})
onUnmounted(() => {
  abortChat()
  gsapCtx?.revert()
  // Flush pending save before unmount so answer_status='submitted' is persisted.
  flushPendingSave()
  clearTimeout(copySessionTimer)
  clearTimeout(copyMsgTimer)
})

watch(() => props.session?.id, (id, oldId) => {
  if (!id) {
    clearState()
    return
  }
  // Flush any pending save for the previous session before restoreState overwrites memory.
  flushPendingSave(oldId)
  restoreState(props.session?.state || {})
}, { immediate: true })
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

    </div>

    <div class="chat__body" :class="{ 'chat__body--has-panel': inspectorOpen }" :style="{ '--inspector-width': `${inspectorWidth}px` }">
      <InspectorRail
        ref="inspectorRail"
        :tabs="inspectorTabs"
        :active-tab="inspectorTab"
        @select="toggleInspector"
      />

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
        <div
          v-else
          ref="msgList"
          class="chat__msgs"
          @scroll="updateMessageScrollState"
          @wheel.passive="handleMessageScrollIntent"
          @touchstart.passive="handleMessageScrollIntent"
          @mousedown="handleMessageScrollIntent"
        >
          <div
            v-for="m in messages" :key="m.id"
            :ref="(el) => setMsgRef(m.id, el)"
            class="chat__msg"
            :class="'chat__msg--' + m.role"
          >
            <div class="chat__bubble">
              <div class="chat__time">{{ m.time }}</div>

              <div v-if="m.role === 'user'" class="chat__bubble-actions">
                <button
                  v-for="act in userBubbleActions"
                  :key="act.id"
                  class="chat__bubble-action"
                  :class="{ 'is-copied': act.id === 'copy' && copiedMsgId === m.id }"
                  :title="act.label"
                  @click="copyUserMessage(m)"
                >
                  <span v-if="act.id === 'copy' && copiedMsgId === m.id">✓</span>
                  <span v-else>{{ act.icon }}</span>
                </button>
              </div>

              <div v-if="m.role === 'user'" class="chat__content">
                <div v-if="m.files?.length" class="chat__msg-files">
                  <FileReference
                    v-for="f in m.files"
                    :key="f.path"
                    :path="f.path"
                    :language="f.lang"
                  />
                </div>
                <template v-for="(part, i) in userContentParts(m)" :key="i">
                  <FileReference
                    v-if="part.type === 'file'"
                    :path="part.path"
                    :language="part.lang"
                  />
                  <span v-else>{{ part.text }}</span>
                </template>
              </div>
              <ChatTimeline
                v-else
                :events="m.events"
                :running="isStreaming && m === messages[messages.length - 1]"
                @answer="answerQuestion"
              />
            </div>
          </div>
        </div>

        <Transition name="jump-latest">
          <button
            v-if="messages.length && !isAtMessageBottom"
            type="button"
            class="chat__jump-latest"
            @click="scrollBottom"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 5v14"/>
              <path d="m19 12-7 7-7-7"/>
            </svg>
            <span>Jump to latest</span>
          </button>
        </Transition>

      </div>

      <!-- ============= todo panel ============= -->
      <Transition :css="false" @enter="panelEnter" @leave="panelLeave" @after-leave="panelAfterLeave">
        <InspectorPanel
          v-if="inspectorOpen"
          :tab="inspectorTab"
          :run="evidenceRun"
          :runs="evidenceRuns"
          :task-analyses="taskAnalyses"
          :usage="sessionUsage"
          :todos="todos"
          :tools="toolCatalog"
          :mcp-servers="mcpServers"
          :subagents="visibleSubagents"
          :width="inspectorWidth"
          :tabs="inspectorTabs"
          :workspace-key="activeWorkspace?.id || activeWorkspace?.path || ''"
          @resize="inspectorWidth = $event"
          @toggle-todo="toggleTodo"
          @close="closeInspector"
        />
      </Transition>

    </div>

    <div class="chat__foot">
      <ChatComposer
        v-model="input"
        :is-streaming="isStreaming"
        :agent-running="agentRunning"
        :agent-status="agentStatus"
        :context-ratio="contextRatio"
        :session-usage="sessionUsage"
        :file-context="fileContext"
        :copy-session-status="copySessionStatus"
        :active-workspace-id="props.activeWorkspace?.id"
        :active-question="activeQuestion"
        @send="send"
        @stop="stopChat"
        @copy-session="copyCurrentSession"
        @remove-file="removeContextFile"
        @add-file="addToFileContext"
        @answer="answerQuestion"
      />
    </div>

  </div>
</template>

<style scoped>
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
.chat__main { position: relative; flex: 1; display: flex; flex-direction: column; overflow: hidden; max-width: 820px; margin: 0 auto; width: 100%; padding: 0 32px; }

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
.chat__jump-latest {
  position: absolute;
  right: 42px;
  bottom: 18px;
  z-index: 5;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 11px;
  border: 1px solid var(--accent-border);
  border-radius: var(--radius-sm);
  background: var(--bg-raised);
  color: var(--accent-text);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
  font: 600 11px/1 var(--sans);
  cursor: pointer;
}
.chat__jump-latest:hover { background: var(--accent-bg); }
.chat__jump-latest svg { flex-shrink: 0; }
.jump-latest-enter-active,
.jump-latest-leave-active {
  transition: opacity 0.14s ease, transform 0.14s ease;
}
.jump-latest-enter-from,
.jump-latest-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
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
  --inspector-rail-width: 58px;
}

.chat__main {
  max-width: none;
  margin: 0;
  padding: 0;
  transition: margin-right 260ms cubic-bezier(0.16, 1, 0.3, 1);
}

@media (min-width: 981px) {
  .chat__body--has-panel .chat__main {
    margin-right: calc(min(var(--inspector-width, 392px), calc(100% - 20px)) + var(--inspector-rail-width));
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
.chat__msg--user .chat__bubble-actions {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 4px;
  padding: 3px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  box-shadow: 0 2px 8px rgba(23, 86, 209, 0.12);
  opacity: 0;
  visibility: hidden;
  transition: opacity .18s ease, visibility .18s ease;
}

.chat__msg--user .chat__bubble:hover .chat__bubble-actions {
  opacity: 1;
  visibility: visible;
}

.chat__bubble-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  transition: background .15s ease, color .15s ease;
}

.chat__bubble-action:hover {
  background: rgba(23, 86, 209, 0.1);
  color: var(--accent);
}

.chat__bubble-action.is-copied {
  color: #2e9e5b;
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
  flex-shrink: 0;
  padding: 12px 24px 18px;
  border-top: 0;
  background: linear-gradient(transparent, var(--bg) 28%);
}

.chat__code-block {
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.chat__code-block code {
  background: var(--code-bg) !important;
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
}
</style>
