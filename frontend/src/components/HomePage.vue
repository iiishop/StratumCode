<script setup>
import { ref, reactive, onMounted, onUnmounted, nextTick } from 'vue'
import { gsap } from 'gsap'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import diff from 'highlight.js/lib/languages/diff'
import 'highlight.js/styles/github-dark.css'

hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('diff', diff)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('sh', bash)

/* ── agents & sessions ── */
const agents = [
  { id: 'build', label: 'Build', short: 'B', desc: 'Full access to code, shell, and files' },
  { id: 'plan', label: 'Plan', short: 'P', desc: 'Read-only analysis and design' },
  { id: 'explore', label: 'Explore', short: 'E', desc: 'Read-only codebase search' },
]
const activeAgent = ref(agents[0])
const showAgentMenu = ref(false)

/* ── todos ── */
const todosOpen = ref(false)
const todos = reactive([
  { id: 1, content: 'Implement agent chat backend', done: false },
  { id: 2, content: 'Add provider connection testing', done: true },
  { id: 3, content: 'Build sub-agent dispatch system', done: false },
  { id: 4, content: 'Create diff preview component', done: false },
])

/* ── file context ── */
const fileContext = reactive([
  { path: 'stratumcode/providers.py', lang: 'python' },
  { path: 'stratumcode/server.py',    lang: 'python' },
])

const suggestions = [
  { label: 'Trace a bug', detail: 'Follow the failure to its source', prompt: 'Find and fix a bug in the current branch' },
  { label: 'Explain a module', detail: 'Map structure, calls, and side effects', prompt: 'Explain how the main application module works' },
  { label: 'Refactor safely', detail: 'Reduce complexity without changing behavior', prompt: 'Review the current code and propose a focused refactor' },
]

/* ── chat state ── */
const input = ref('')
const chatRef = ref(null)
const msgList = ref(null)
const messages = reactive([])
const msgRefs = reactive({})
const isStreaming = ref(false)
let gsapCtx

function setMsgRef(id, el) { if (el) msgRefs[id] = el }

function timeNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function toggleTodo(id) {
  const t = todos.find(t => t.id === id)
  if (t) t.done = !t.done
}

function removeContextFile(p) {
  const i = fileContext.findIndex(f => f.path === p)
  if (i !== -1) fileContext.splice(i, 1)
}

function selectAgent(a) {
  activeAgent.value = a
  showAgentMenu.value = false
}

function send() {
  const text = input.value.trim()
  if (!text) return
  messages.push({ id: Date.now(), role: 'user', content: text, time: timeNow() })
  input.value = ''
  isStreaming.value = true
  nextTick(() => { scrollBottom(); animateLast() })
  setTimeout(simulateAgent, 400)
}

function simulateAgent() {
  const id = Date.now()
  const msg = reactive({
    id, role: 'assistant', content: '', time: timeNow(),
    thinking: { text: '', done: false },
    toolCalls: [],
    subDispatch: [],
    diff: null,
  })
  messages.push(msg)
  nextTick(() => scrollBottom())

  // thinking
  const thought = `[${activeAgent.value.label} agent] Analyzing request with file context: ${fileContext.map(f => f.path).join(', ')}. I need to understand the codebase structure, then dispatch sub-agents for parallel exploration, and finally propose code changes.`
  let ti = 0
  const t1 = setInterval(() => {
    if (ti < thought.length) { msg.thinking.text += thought[ti]; ti++; nextTick(() => scrollBottom()) }
    else { clearInterval(t1); msg.thinking.done = true; setTimeout(() => simulateSubDispatch(msg), 300) }
  }, 8)

  // sub-agent dispatch
  function simulateSubDispatch(msg) {
    msg.subDispatch.push(reactive({ id: 1, name: '@explore', task: 'Search codebase for auth-related files', status: 'running', result: '' }))
    msg.subDispatch.push(reactive({ id: 2, name: '@explore', task: 'Find all API route definitions', status: 'running', result: '' }))
    nextTick(() => scrollBottom())

    setTimeout(() => {
      msg.subDispatch[0].status = 'done'
      msg.subDispatch[0].result = 'Found 3 files: auth.py, middleware.py, tokens.py'
      nextTick(() => scrollBottom())
      setTimeout(() => {
        msg.subDispatch[1].status = 'done'
        msg.subDispatch[1].result = 'Found 6 routes: /api/providers, /api/chat, /api/config, ...'
        nextTick(() => scrollBottom())

        // diff preview
        setTimeout(() => {
          msg.diff = reactive({
            path: 'stratumcode/server.py',
            hunks: [
              { type: 'add', lines: ['+    self.send_response(200)', '+    self.send_header("Content-Type", "application/json")', '+    self.end_headers()'] },
              { type: 'keep', lines: ['     # existing route handling', '     if path == "/api/providers":'] },
              { type: 'add', lines: ['+         return self.handle_chat(data)', '+', '+     def handle_chat(self, data):'] },
            ],
            accepted: null,
          })
          nextTick(scrollBottom)
          streamFinalReply(msg)
        }, 600)
      }, 500)
    }, 600)
  }

  function streamFinalReply(msg) {
    const full = 'I analyzed the codebase and found the relevant files. Here is the proposed change to add chat support to the server.'
    let i = 0
    const timer = setInterval(() => {
      if (i < full.length) { msg.content += full[i]; i++; nextTick(scrollBottom) }
      else { clearInterval(timer); isStreaming.value = false }
    }, 10)
  }
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
}

function scrollBottom() { if (msgList.value) msgList.value.scrollTop = msgList.value.scrollHeight }

function animateLast() {
  const ids = Object.keys(msgRefs)
  if (!ids.length) return
  const el = msgRefs[ids[ids.length - 1]]
  if (el) gsap.fromTo(el, { autoAlpha: 0, y: 8, scale: 0.98 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.25, ease: 'power2.out' })
}

const languageAliases = {
  py: 'python',
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  sh: 'bash',
}

function highlightCode(code, languageOrPath = '') {
  const name = languageOrPath.toLowerCase().split('.').pop()
  const language = languageAliases[name] || name
  return hljs.getLanguage(language)
    ? hljs.highlight(code, { language, ignoreIllegals: true }).value
    : code.replace(/[&<>]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[char])
}

function diffLineType(hunk, line) {
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'remove'
  return ['add', 'remove'].includes(hunk.type) ? hunk.type : 'keep'
}

function diffLinePrefix(hunk, line) {
  if (/^[ +\-]/.test(line)) return line[0]
  return { add: '+', remove: '-', keep: ' ' }[diffLineType(hunk, line)]
}

function diffLineContent(line) {
  return /^[ +\-]/.test(line) ? line.slice(1) : line
}

function diffCount(diff, type) {
  return diff.hunks.reduce(
    (count, hunk) => count + hunk.lines.filter(line => diffLineType(hunk, line) === type).length,
    0,
  )
}

function renderContent(text) {
  const parts = []
  const codeRegex = /```(\w*)\n([\s\S]*?)```/g
  let last = 0, match
  while ((match = codeRegex.exec(text)) !== null) {
    if (match.index > last) parts.push(...renderInline(text.slice(last, match.index)))
    parts.push({ type: 'code', lang: match[1] || 'plaintext', content: match[2].trim() })
    last = match.index + match[0].length
  }
  if (last < text.length) parts.push(...renderInline(text.slice(last)))
  return parts
}

function renderInline(text) {
  const parts = []
  const boldRegex = /\*\*(.+?)\*\*/g
  let last = 0, match
  while ((match = boldRegex.exec(text)) !== null) {
    if (match.index > last) parts.push({ type: 'text', content: text.slice(last, match.index) })
    parts.push({ type: 'bold', content: match[1] })
    last = match.index + match[0].length
  }
  if (last < text.length) parts.push({ type: 'text', content: text.slice(last) })
  return parts
}

function toggleThinking(msg) { msg._thinkOpen = !msg._thinkOpen }
function toggleToolCall(tc) { tc._open = !tc._open }
function toggleSubTask(st) { st._open = !st._open }

onMounted(() => {
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
onUnmounted(() => { gsapCtx?.revert() })
</script>

<template>
  <div ref="chatRef" class="chat">

    <div class="chat__topbar">
      <div class="chat__session">
        <span class="chat__session-mark">&gt;_</span>
        <div>
          <strong>New session</strong>
          <small>No changes yet</small>
        </div>
      </div>

      <div class="chat__topbar-right">
        <div class="agent-switch">
          <button class="agent-switch__trigger" type="button" @click="showAgentMenu = !showAgentMenu">
            <span class="agent-switch__icon">{{ activeAgent.short }}</span>
            <span class="agent-switch__name">{{ activeAgent.label }}</span>
            <span class="agent-switch__mode">{{ activeAgent.id === 'build' ? 'edit' : 'read' }}</span>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m7 9 5 5 5-5"/></svg>
          </button>

          <Transition name="drop">
            <div v-if="showAgentMenu" class="agent-menu">
              <button
                v-for="a in agents"
                :key="a.id"
                class="agent-menu__item"
                :class="{ 'is-on': activeAgent.id === a.id }"
                type="button"
                @click.stop="selectAgent(a)"
              >
                <span class="agent-menu__key">{{ a.short }}</span>
                <span class="agent-menu__body">
                  <span class="agent-menu__name">{{ a.label }}</span>
                  <span class="agent-menu__desc">{{ a.desc }}</span>
                </span>
                <svg v-if="activeAgent.id === a.id" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m5 12 4 4L19 6"/></svg>
              </button>
            </div>
          </Transition>
        </div>

        <button class="chat__topbtn" :class="{ 'is-on': todosOpen }" @click="todosOpen = !todosOpen" title="Tasks">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
          <span class="chat__topbtn-label">Tasks</span>
          <span class="chat__topbtn-badge">{{ todos.filter(t => !t.done).length }}</span>
        </button>
      </div>
    </div>

    <div class="chat__body" :class="{ 'chat__body--has-todos': todosOpen }">

      <!-- ============= message area ============= -->
      <div class="chat__main">

        <div v-if="!messages.length" class="chat__empty">
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

              <!-- thinking -->
              <div v-if="m.thinking" class="think" :class="{ 'think--done': m.thinking.done }" @click="toggleThinking(m)">
                <div class="think__bar">
                  <span class="think__icon">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2a3 3 0 0 0-3 3v1a6 6 0 0 0-5 5H3a3 3 0 0 0 0 6h1a6 6 0 0 0 5 5v1a3 3 0 0 0 6 0v-1a6 6 0 0 0 5-5h1a3 3 0 0 0 0-6h-1a6 6 0 0 0-5-5V5a3 3 0 0 0-3-3z"/></svg>
                  </span>
                  <span class="think__label">{{ m.thinking.done ? 'Thought for a few seconds' : 'Thinking…' }}</span>
                  <span v-if="!m.thinking.done" class="think__dots"><i></i><i></i><i></i></span>
                  <svg class="think__chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
                </div>
                <Transition name="think-slide">
                  <div v-if="m._thinkOpen !== false" class="think__body">{{ m.thinking.text }}</div>
                </Transition>
              </div>

              <!-- sub-agent dispatch -->
              <div v-for="st in m.subDispatch" :key="st.id" class="sub" :class="{ 'sub--done': st.status === 'done' }">
                <div class="sub__bar" @click="toggleSubTask(st)">
                  <span class="sub__icon">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                  </span>
                  <span class="sub__agent">{{ st.name }}</span>
                  <span class="sub__task">{{ st.task }}</span>
                  <span v-if="st.status === 'running'" class="sub__spinner"></span>
                  <span v-else class="sub__check">
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--ok)" stroke-width="2.5" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>
                  </span>
                  <svg class="sub__chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
                </div>
                <Transition name="sub-slide">
                  <div v-if="st._open" class="sub__body">{{ st.result || 'Loading…' }}</div>
                </Transition>
              </div>

              <!-- tool calls -->
              <div v-for="tc in m.toolCalls" :key="tc.id" class="tc" :class="{ 'tc--done': tc.status === 'done' }">
                <div class="tc__bar" @click="toggleToolCall(tc)">
                  <span class="tc__icon">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
                  </span>
                  <span class="tc__name">{{ tc.name }}</span>
                  <span v-if="tc.status === 'running'" class="tc__spinner"></span>
                  <span v-else class="tc__check"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--ok)" stroke-width="2.5" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg></span>
                  <svg class="tc__chevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
                </div>
                <Transition name="tc-slide">
                  <div v-if="tc._open" class="tc__body">
                    <div class="tc__section"><div class="tc__section-label">Input</div><pre class="tc__pre">{{ tc.input }}</pre></div>
                    <div v-if="tc.output" class="tc__section"><div class="tc__section-label">Output</div><pre class="tc__pre">{{ tc.output }}</pre></div>
                  </div>
                </Transition>
              </div>

              <!-- diff preview -->
              <div v-if="m.diff" class="diff">
                <div class="diff__head">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                  <span class="diff__path">{{ m.diff.path }}</span>
                  <span class="diff__tag diff__tag--add">+{{ diffCount(m.diff, 'add') }}</span>
                  <span class="diff__tag diff__tag--remove">−{{ diffCount(m.diff, 'remove') }}</span>
                </div>
                <pre class="diff__code"><code><template v-for="(hunk, hi) in m.diff.hunks" :key="hi"><span
                    v-for="(line, li) in hunk.lines"
                    :key="hi + '-' + li"
                    class="diff__line"
                    :class="'diff__line--' + diffLineType(hunk, line)"
                  ><span class="diff__marker">{{ diffLinePrefix(hunk, line) }}</span><span v-html="highlightCode(diffLineContent(line), m.diff.path)"></span></span></template></code></pre>
                <div class="diff__actions" v-if="m.diff.accepted === null">
                  <button class="diff__accept" @click="m.diff.accepted = true">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>
                    Accept
                  </button>
                  <button class="diff__reject" @click="m.diff.accepted = false">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    Reject
                  </button>
                </div>
                <div v-else class="diff__status" :class="m.diff.accepted ? 'diff__status--ok' : 'diff__status--err'">
                  {{ m.diff.accepted ? 'Accepted' : 'Rejected' }}
                </div>
              </div>

              <!-- message text -->
              <div v-if="m.content" class="chat__content">
                <template v-for="(part, pi) in renderContent(m.content)" :key="pi">
                  <pre v-if="part.type === 'code'" class="chat__code-block"><code :class="'language-' + part.lang" v-html="highlightCode(part.content, part.lang)"></code></pre>
                  <strong v-else-if="part.type === 'bold'">{{ part.content }}</strong>
                  <template v-else>{{ part.content }}</template>
                </template>
                <span v-if="m.role === 'assistant' && m === messages[messages.length - 1] && isStreaming" class="chat__cursor">|</span>
              </div>
            </div>
          </div>
        </div>

      </div>

      <!-- ============= todo panel ============= -->
      <Transition name="panel">
        <div v-if="todosOpen" class="chat__todos">
          <div class="chat__todos-head">
            <span class="chat__todos-title">Tasks</span>
            <span class="chat__todos-count">{{ todos.filter(t => !t.done).length }} remaining</span>
          </div>
          <div class="chat__todos-list">
            <div v-for="t in todos" :key="t.id" class="chat__todo" :class="{ 'is-done': t.done }" @click="toggleTodo(t.id)">
              <span class="chat__todo-check">
                <svg v-if="t.done" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--ok)" stroke-width="3" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>
              </span>
              <span class="chat__todo-text">{{ t.content }}</span>
            </div>
          </div>
        </div>
      </Transition>

    </div>

    <div class="chat__foot">
      <div class="chat__composer">
        <div v-if="fileContext.length" class="chat__files">
          <span class="chat__files-label">Context</span>
          <span v-for="f in fileContext" :key="f.path" class="chat__file-chip">
            <span class="chat__file-ext">{{ f.lang.slice(0, 2) }}</span>
            {{ f.path }}
            <button class="chat__file-chip-x" type="button" @click="removeContextFile(f.path)" :aria-label="'Remove ' + f.path">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 6 12 12M18 6 6 18"/></svg>
            </button>
          </span>
        </div>

        <div class="chat__input-row">
          <textarea
            v-model="input"
            class="chat__input"
            placeholder="Ask StratumCode to inspect or change the project"
            rows="1"
            @keydown="onKeydown"
            :disabled="isStreaming"
          ></textarea>
          <button class="chat__send" type="button" @click="send" :disabled="!input.trim() || isStreaming" aria-label="Send message">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
              <path d="M12 19V5M6 11l6-6 6 6"/>
            </svg>
          </button>
        </div>

        <div class="chat__composer-meta">
          <span>{{ activeAgent.label }} agent</span>
          <span>Enter to send</span>
        </div>
      </div>
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

/* ---- agent switcher ---- */
.agent-switch {
  position: relative;
  display: flex; align-items: center; gap: 7px;
  padding: 4px 10px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  cursor: pointer; user-select: none;
  font-size: 12px; font-weight: 600; color: var(--text-h);
  transition: border-color 0.12s, background 0.12s;
}
.agent-switch:hover { border-color: var(--accent-border); background: var(--accent-bg); }
.agent-switch__dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.agent-switch__name { font-size: 12px; }
.agent-switch__mode {
  font-size: 10px; font-weight: 500; color: var(--text-muted);
  padding: 1px 6px; border-radius: 3px; background: var(--code-bg);
  text-transform: uppercase; letter-spacing: 0.04em;
}

.agent-menu {
  position: absolute; top: calc(100% + 6px); left: 0;
  min-width: 260px;
  padding: 4px;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-raised); box-shadow: var(--shadow-md);
  z-index: 20;
}
.agent-menu__item {
  display: flex; align-items: center; gap: 10px; padding: 8px 10px;
  border-radius: var(--radius-sm); cursor: pointer;
  transition: background 0.1s;
}
.agent-menu__item:hover { background: var(--accent-bg); }
.agent-menu__item.is-on { background: var(--accent-bg); }
.agent-menu__dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.agent-menu__body { flex: 1; display: flex; flex-direction: column; gap: 1px; }
.agent-menu__name { font-size: 13px; font-weight: 600; color: var(--text-h); }
.agent-menu__desc { font-size: 11px; color: var(--text-muted); }

.drop-enter-active { transition: all 0.15s ease; }
.drop-leave-active { transition: all 0.1s ease; }
.drop-enter-from, .drop-leave-to { opacity: 0; transform: translateY(-4px); }

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
.chat__body { display: flex; flex: 1; overflow: hidden; }
.chat__main { flex: 1; display: flex; flex-direction: column; overflow: hidden; max-width: 820px; margin: 0 auto; width: 100%; padding: 0 32px; }

/* ---- empty ---- */
.chat__empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 32px; }
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

.panel-enter-active { transition: all 0.2s ease; }
.panel-leave-active { transition: all 0.15s ease; }
.panel-enter-from, .panel-leave-to { width: 0; opacity: 0; }
.panel-enter-to, .panel-leave-from { width: 240px; opacity: 1; }

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
.chat__foot { flex-shrink: 0; border-top: 1px solid var(--border); background: var(--bg); }
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
  min-height: 0;
  background: transparent;
}

.chat__topbar {
  min-height: 54px;
  padding: 0 18px;
  border-bottom-color: var(--border);
  background: rgba(21, 21, 20, 0.82);
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

.agent-switch {
  position: relative;
  padding: 0;
  border: 0;
  background: transparent;
}

.agent-switch__trigger {
  display: flex;
  height: 32px;
  align-items: center;
  gap: 7px;
  padding: 0 9px 0 5px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  background: var(--bg-raised);
  cursor: pointer;
}

.agent-switch__trigger:hover {
  border-color: var(--border-strong);
  background: var(--bg-overlay);
}

.agent-switch__icon,
.agent-menu__key {
  display: grid;
  place-items: center;
  border-radius: 6px;
  color: #fff2ed;
  background: var(--accent);
  font: 650 9px/1 var(--mono);
}

.agent-switch__icon {
  width: 22px;
  height: 22px;
}

.agent-switch__name {
  color: var(--text-h);
  font-size: 11px;
}

.agent-switch__mode {
  padding: 0;
  color: var(--text-muted);
  background: transparent;
  font: 9px/1 var(--mono);
  text-transform: lowercase;
}

.agent-menu {
  top: calc(100% + 8px);
  right: 0;
  left: auto;
  min-width: 286px;
  padding: 6px;
  border-color: var(--border-strong);
  border-radius: var(--radius);
  background: var(--bg-overlay);
  box-shadow: var(--shadow-md);
}

.agent-menu__item {
  width: 100%;
  gap: 10px;
  padding: 9px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text);
  background: transparent;
  text-align: left;
}

.agent-menu__item:hover {
  border-color: var(--border);
  background: var(--code-bg-hover);
}

.agent-menu__item.is-on {
  border-color: var(--accent-border);
  background: var(--accent-bg);
}

.agent-menu__key {
  width: 26px;
  height: 26px;
}

.agent-menu__name {
  color: var(--text-h);
  font-size: 11px;
}

.agent-menu__desc {
  color: var(--text-muted);
  font-size: 9.5px;
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
  min-height: 0;
}

.chat__main {
  max-width: none;
  margin: 0;
  padding: 0;
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
  color: #8d8783;
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
  width: min(900px, calc(100% - 48px));
  margin: 0 auto;
  padding: 30px 4px 22px;
  gap: 24px;
}

.chat__bubble {
  max-width: min(760px, 88%);
  padding: 0;
  border-radius: 0;
  color: #d5cfca;
  font-size: 12.5px;
  line-height: 1.65;
}

.chat__msg--user .chat__bubble {
  padding: 11px 14px;
  border: 1px solid var(--accent-border);
  border-radius: var(--radius);
  color: #f4eae6;
  background: var(--accent-bg);
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
  color: #5e5956;
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
  background: #191817;
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
  background: #181716;
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
  background: #121211;
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
  color: #b8b1ac;
  font-size: 10.5px;
}

.chat__foot {
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
  background: #191817;
  box-shadow: 0 18px 44px rgba(5, 4, 3, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.025);
  transition: border-color var(--transition), box-shadow var(--transition);
}

.chat__composer:focus-within {
  border-color: var(--accent-border);
  box-shadow: 0 18px 44px rgba(5, 4, 3, 0.3), 0 0 0 3px var(--accent-bg);
}

.chat__files {
  padding: 9px 11px 0;
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
  color: #aaa39e;
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
  font-size: 12px;
}

.chat__input:focus {
  border: 0;
  box-shadow: none;
}

.chat__input::placeholder {
  color: #625d59;
}

.chat__send {
  width: 34px;
  height: 34px;
  align-self: flex-end;
  border: 1px solid var(--accent);
  border-radius: 9px;
  color: #fff5f1;
  background: var(--accent);
}

.chat__send:hover {
  background: var(--accent-hover);
}

.chat__composer-meta {
  display: flex;
  justify-content: space-between;
  padding: 0 13px 9px;
  color: #5f5a56;
  font: 8.5px/1 var(--mono);
}

.chat__code-block {
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.chat__code-block code {
  background: var(--code-bg) !important;
}

@media (max-width: 780px) {
  .chat__topbtn-label,
  .agent-switch__mode,
  .chat__session small {
    display: none;
  }

  .chat__empty {
    width: calc(100% - 36px);
    padding-bottom: 40px;
  }

  .chat__msgs {
    width: calc(100% - 28px);
  }

  .chat__bubble {
    max-width: 94%;
  }

  .chat__todos {
    position: absolute;
    inset: 0 0 0 auto;
    z-index: 10;
    box-shadow: -18px 0 40px rgba(5, 4, 3, 0.4);
  }

  .chat__foot {
    padding: 10px 12px 12px;
  }
}

@media (max-width: 520px) {
  .chat__topbar { padding-inline: 12px; }
  .chat__session > div { display: none; }
  .agent-switch__name { display: none; }
  .chat__title { font-size: 30px; }
  .chat__hint small { display: none; }
  .chat__file-chip { max-width: 190px; overflow: hidden; text-overflow: ellipsis; }
}
</style>
