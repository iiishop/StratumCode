<script setup>
import { ref, reactive, onMounted, onUnmounted, nextTick } from 'vue'
import { gsap } from 'gsap'

const input = ref('')
const msgList = ref(null)
const messages = reactive([])
const msgRefs = reactive({})
const isStreaming = ref(false)
let gsapCtx

function setMsgRef(id, el) { if (el) msgRefs[id] = el }

function send() {
  const text = input.value.trim()
  if (!text) return
  messages.push({ id: Date.now(), role: 'user', content: text })
  input.value = ''
  isStreaming.value = true
  nextTick(() => {
    scrollBottom()
    animateLast()
    setTimeout(streamReply, 400)
  })
}

function streamReply() {
  const id = Date.now()
  const msg = reactive({ id, role: 'assistant', content: '' })
  messages.push(msg)
  nextTick(() => scrollBottom())

  const full = 'This is a placeholder response. The backend is not connected yet. Once the agent backend is implemented, real responses will appear here. You can manage your LLM providers in the **Providers** tab and then connect them to this chat interface.\n\n```python\n# Example: future agent code\ndef chat(message, provider):\n    return provider.complete(message)\n```\n\nStay tuned!'
  let i = 0
  const timer = setInterval(() => {
    if (i < full.length) {
      msg.content += full[i]
      i++
      nextTick(() => scrollBottom())
    } else {
      clearInterval(timer)
      isStreaming.value = false
    }
  }, 12)
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function scrollBottom() {
  if (msgList.value) {
    msgList.value.scrollTop = msgList.value.scrollHeight
  }
}

function animateLast() {
  const ids = Object.keys(msgRefs)
  if (!ids.length) return
  const el = msgRefs[ids[ids.length - 1]]
  if (el) {
    gsap.fromTo(el, { autoAlpha: 0, y: 8, scale: 0.98 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.25, ease: 'power2.out' })
  }
}

function renderContent(text) {
  const parts = []
  const codeRegex = /```(\w*)\n([\s\S]*?)```/g
  let last = 0
  let match
  while ((match = codeRegex.exec(text)) !== null) {
    if (match.index > last) {
      parts.push(...renderInline(text.slice(last, match.index)))
    }
    parts.push({ type: 'code', lang: match[1] || '', content: match[2].trim() })
    last = match.index + match[0].length
  }
  if (last < text.length) {
    parts.push(...renderInline(text.slice(last)))
  }
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

onMounted(() => {
  gsapCtx = gsap.context(() => {}, undefined)
})

onUnmounted(() => {
  gsapCtx?.revert()
})
</script>

<template>
  <div class="chat">

    <!-- empty state -->
    <div v-if="!messages.length" class="chat__empty">
      <div class="chat__welcome">
        <h1 class="chat__title">StratumCode</h1>
        <p class="chat__subtitle">Your AI agent desktop. Connect a provider, then start a conversation.</p>
      </div>
      <div class="chat__hints">
        <button
          v-for="hint in ['Explain quantum computing in simple terms', 'Write a Python script to parse JSON files', 'Help me debug a segmentation fault']"
          :key="hint"
          class="chat__hint"
          @click="input = hint; send()"
        >{{ hint }}</button>
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
          <template v-for="(part, pi) in renderContent(m.content)" :key="pi">
            <code v-if="part.type === 'code'" class="chat__code">{{ part.content }}</code>
            <strong v-else-if="part.type === 'bold'">{{ part.content }}</strong>
            <template v-else>{{ part.content }}</template>
          </template>
          <span v-if="m.role === 'assistant' && m === messages[messages.length - 1] && isStreaming" class="chat__cursor">|</span>
        </div>
      </div>
    </div>

    <!-- input -->
    <div class="chat__input-bar">
      <textarea
        v-model="input"
        class="chat__input"
        placeholder="Type a message…"
        rows="1"
        @keydown="onKeydown"
        :disabled="isStreaming"
        ref="inputEl"
      ></textarea>
      <button class="chat__send" @click="send" :disabled="!input.trim() || isStreaming">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
        </svg>
      </button>
    </div>

  </div>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  height: 100svh;
  max-width: 720px;
  margin: 0 auto;
  padding: 0 32px;
}

/* ---- empty ---- */
.chat__empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 32px;
  padding-bottom: 60px;
}

.chat__welcome { text-align: center; }

.chat__title {
  font-size: 28px; font-weight: 700; color: var(--text-h);
  margin: 0 0 6px; letter-spacing: -0.02em;
}

.chat__subtitle {
  font-size: 14px; color: var(--text-muted); margin: 0;
  max-width: 360px; line-height: 1.55;
}

.chat__hints {
  display: flex; flex-direction: column; gap: 8px;
  width: 100%; max-width: 440px;
}

.chat__hint {
  width: 100%; padding: 12px 16px;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-raised); color: var(--text);
  font-size: 13px; font-family: var(--sans); text-align: left; cursor: pointer;
  transition: border-color 0.12s, background 0.12s;
}
.chat__hint:hover { border-color: var(--accent-border); background: var(--accent-bg); }

/* ---- messages ---- */
.chat__msgs {
  flex: 1;
  overflow-y: auto;
  padding: 24px 0 16px;
  display: flex; flex-direction: column; gap: 20px;
}

.chat__msg { display: flex; }

.chat__msg--user {
  justify-content: flex-end;
}

.chat__bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: var(--radius);
  font-size: 13px; line-height: 1.6; color: var(--text-h);
  white-space: pre-wrap; word-break: break-word;
}

.chat__msg--user .chat__bubble {
  background: var(--accent-bg);
  border: 1px solid var(--accent-border);
}

.chat__msg--assistant .chat__bubble {
  background: var(--bg-raised);
  border: 1px solid var(--border);
}

/* ---- code ---- */
.chat__code {
  display: block;
  margin: 8px 0;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--code-bg);
  font-family: var(--mono); font-size: 12px; line-height: 1.5;
  white-space: pre-wrap; word-break: break-word;
  color: var(--text-h);
  border: 1px solid var(--border);
}

/* ---- cursor ---- */
.chat__cursor {
  display: inline;
  color: var(--accent);
  animation: blink 1s step-end infinite;
}
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

/* ---- input bar ---- */
.chat__input-bar {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px 0 16px;
  border-top: 1px solid var(--border);
  background: var(--bg);
  position: sticky;
  bottom: 0;
}

.chat__input {
  flex: 1;
  min-height: 36px; max-height: 120px;
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
</style>
