<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { gsap } from 'gsap'
import FileReference from '../FileReference.vue'
import FileMentionDropdown from '../FileMentionDropdown.vue'

const input = defineModel({ type: String, default: '' })

const rootRef = ref(null)
const props = defineProps({
  isStreaming: { type: Boolean, default: false },
  agentRunning: { type: Boolean, default: false },
  agentStatus: { type: Object, default: () => ({}) },
  contextRatio: { type: String, default: '0%' },
  sessionUsage: { type: Object, default: () => ({}) },
  fileContext: { type: Array, default: () => [] },
  copySessionStatus: { type: String, default: '' },
  activeWorkspaceId: { type: [Number, String], default: null },
  activeQuestion: { type: Object, default: null },
})

const emit = defineEmits(['send', 'stop', 'copy-session', 'remove-file', 'add-file', 'answer'])

const textareaRef = ref(null)

/* ── clearify question state ── */
const isAsking = computed(() => !!props.activeQuestion)
const questionOptions = computed(() => (props.activeQuestion?.options || []).filter(o => o && o.id))
const canSubmitAnswer = computed(() => {
  if (!isAsking.value) return true
  return input.value.trim().length > 0
})

function buildAnswerPayload(extra) {
  const q = props.activeQuestion || {}
  return {
    question_id: q.id,
    analysis_id: q.analysis_id || '',
    unknown_id: q.unknown_id || '',
    origin_message: q.origin_message || '',
    question: q.question || '',
    send: true,
    ...extra,
  }
}

function submitOption(option) {
  if (!isAsking.value) return
  emit('answer', buildAnswerPayload({
    selected_option_id: option.id,
    selected_option_label: option.label,
    response: option.value || option.label,
  }))
}

function submitCustomAnswer() {
  const text = input.value.trim()
  if (!isAsking.value || !text) return
  emit('answer', buildAnswerPayload({ response: text, custom: true }))
  input.value = ''
}

function onComposerAction() {
  if (isAsking.value) {
    submitCustomAnswer()
    return
  }
  emit('send')
}

/* ── mention state ── */
const mentionFiles = ref([])
const mentionFilesLoaded = ref(false)
const mentionActive = ref(false)
const mentionSearch = ref('')
const mentionStartPos = ref(-1)
const mentionTrigger = ref('@')
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
  const inline = mentionTrigger.value === '#'
  const inserted = inline ? `#${path}` : ''
  input.value = before + inserted + after
  mentionActive.value = false
  mentionSearch.value = ''
  mentionStartPos.value = -1
  if (!inline) emit('add-file', path)
  nextTick(() => {
    const pos = before.length + inserted.length
    ta.setSelectionRange(pos, pos)
    ta.focus()
  })
}

function onTextareaInput() {
  const ta = textareaRef.value
  if (!ta) return
  const cursor = ta.selectionStart
  const text = ta.value
  const atPos = text.lastIndexOf('@', cursor)
  const hashPos = text.lastIndexOf('#', cursor)
  const triggerPos = Math.max(atPos, hashPos)
  if (triggerPos < 0 || (triggerPos > 0 && /\w/.test(text[triggerPos - 1]))) {
    mentionActive.value = false
    return
  }
  const between = text.substring(triggerPos + 1, cursor)
  if (/\s/.test(between)) {
    mentionActive.value = false
    return
  }
  mentionStartPos.value = triggerPos
  mentionTrigger.value = text[triggerPos]
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
  // IME 组合中（输入法选词/上屏）的 Enter 不是发送意图
  if (e.isComposing || e.keyCode === 229) return
  if (mentionActive.value && (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === 'Tab' || e.key === 'Escape')) {
    return
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    onComposerAction()
  }
}

function closeMention() {
  mentionActive.value = false
  mentionSearch.value = ''
  mentionStartPos.value = -1
}

watch(() => props.activeWorkspaceId, () => {
  mentionFiles.value = []
  mentionFilesLoaded.value = false
})

function formatNumber(value) {
  return typeof value === 'number' ? value.toLocaleString() : (value ?? 0)
}

/* ── clearify panel transition ── */
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

function onAskEnter(el, done) {
  if (reducedMotion()) { done(); return }
  const finalH = el.scrollHeight
  gsap.fromTo(el,
    { height: 0, autoAlpha: 0 },
    {
      height: finalH + 15,
      autoAlpha: 1,
      duration: 0.3,
      ease: 'power3.out',
      onComplete: () => {
        gsap.to(el, {
          height: finalH,
          duration: 0.2,
          ease: 'power2.out',
          clearProps: 'height',
          onComplete: () => {
            spawnAskParticles(el)
            done()
          },
        })
      },
    },
  )
  gsap.fromTo(el.querySelectorAll('.chat__ask-option, .chat__ask-hint'),
    { autoAlpha: 0, y: 4 },
    { autoAlpha: 1, y: 0, duration: 0.22, stagger: 0.04, ease: 'power2.out', delay: 0.1 },
  )
}

function onAskLeave(el, done) {
  if (reducedMotion()) { done(); return }
  gsap.to(el,
    {
      height: 0,
      autoAlpha: 0,
      duration: 0.2,
      ease: 'power2.in',
      onComplete: done,
    },
  )
}

function spawnAskParticles(el) {
  if (reducedMotion()) return
  const w = el.clientWidth
  const h = el.clientHeight
  const cx = w / 2
  const baseY = h * 0.92
  const colors = ['#4f8af7', '#f5c842', '#ffffff']
  for (let i = 0; i < 8; i += 1) {
    const p = document.createElement('span')
    p.className = 'chat__ask-particle'
    const size = 2.5 + Math.random() * 3
    p.style.width = `${size}px`
    p.style.height = `${size}px`
    p.style.background = colors[i % colors.length]
    p.style.left = `${cx + (Math.random() - 0.5) * 160}px`
    p.style.top = `${baseY}px`
    el.appendChild(p)
    gsap.to(p, {
      y: -(18 + Math.random() * 32),
      x: (Math.random() - 0.5) * 30,
      autoAlpha: 0,
      scale: 0.4,
      duration: 0.55 + Math.random() * 0.35,
      ease: 'power1.out',
      onComplete: () => p.remove(),
    })
  }
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  if (rootRef.value) {
    gsap.fromTo(rootRef.value, { y: 12, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.46, ease: 'power2.out', delay: 0.16 })
  }
})
</script>

<template>
  <div ref="rootRef" class="chat__composer">
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
        @remove="emit('remove-file', f.path)"
      />
    </div>
    <Transition :css="false" @enter="onAskEnter" @leave="onAskLeave">
      <div v-if="isAsking" class="chat__ask">
        <div class="chat__ask-head">
          <span class="chat__ask-badge">Question</span>
          <span class="chat__ask-question">{{ props.activeQuestion?.question || 'Agent needs your input' }}</span>
          <button
            v-if="props.activeQuestion?.reason"
            class="chat__ask-reason"
            type="button"
            title="Why this is asked"
          >{{ props.activeQuestion.reason }}</button>
        </div>
        <div v-if="questionOptions.length" class="chat__ask-options">
          <button
            v-for="option in questionOptions"
            :key="option.id"
            class="chat__ask-option"
            :class="{ 'is-recommended': option.recommended }"
            type="button"
            @click="submitOption(option)"
          >
            <span class="chat__ask-option-label">{{ option.label }}</span>
            <span class="chat__ask-option-side">
              <span v-if="option.recommended" class="chat__ask-option-rec">recommended</span>
              <svg class="chat__ask-option-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
            </span>
          </button>
        </div>
        <div v-else class="chat__ask-hint">Type your answer above and press Enter.</div>
      </div>
    </Transition>
    <div class="chat__input-row">
      <textarea
        ref="textareaRef"
        v-model="input"
        class="chat__input"
        :placeholder="isAsking ? 'Type your answer…' : 'Ask StratumCode to inspect or change the project'"
        rows="1"
        @keydown="onTextareaKeydown"
        @input="onTextareaInput"
        :disabled="isStreaming && !isAsking"
      ></textarea>
      <button
        class="chat__copy-session"
        type="button"
        @click="emit('copy-session')"
        aria-label="Copy full session"
        title="Copy full session"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round">
          <path d="M8 8h10v12H8z"/>
          <path d="M6 16H4V4h12v2"/>
        </svg>
      </button>
      <button
        class="chat__send"
        :class="{ 'is-stop': isStreaming }"
        type="button"
        @click="isStreaming ? emit('stop') : onComposerAction()"
        :disabled="!isStreaming && !canSubmitAnswer"
        :aria-label="isStreaming ? 'Stop run' : (isAsking ? 'Submit answer' : 'Send message')"
        :title="isStreaming ? 'Stop run' : (isAsking ? 'Submit answer' : 'Send message')"
      >
        <svg v-if="isStreaming" width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <rect x="6" y="6" width="12" height="12" rx="2"/>
        </svg>
        <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" aria-hidden="true">
          <path d="M12 19V5M6 11l6-6 6 6"/>
        </svg>
      </button>
    </div>

    <div class="chat__composer-meta">
      <span>{{ isAsking ? 'Enter to submit answer' : 'Enter to send' }}</span>
      <span v-if="copySessionStatus">{{ copySessionStatus }}</span>
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
</template>

<style scoped>
.chat__composer {
  position: relative;
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
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  flex-shrink: 0;
  padding: 7px 11px 0;
}

.chat__files-label {
  margin-right: 3px;
  color: var(--text-muted);
  font: 9px/1 var(--mono);
  letter-spacing: 0;
  text-transform: none;
}

.chat__file-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
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
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 9px 10px 5px 14px;
}

.chat__input {
  flex: 1;
  min-height: 42px;
  max-height: 120px;
  padding: 9px 0;
  border: 0;
  border-radius: 0;
  color: var(--text-h);
  background: transparent;
  font-size: var(--font-body);
  resize: none;
  outline: none;
  transition: border-color 0.12s;
}

.chat__input:focus {
  border: 0;
  box-shadow: none;
}

.chat__input::placeholder {
  color: #91a0ba;
}

.chat__input:disabled {
  opacity: 0.5;
}

.chat__send {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  align-self: flex-end;
  border: 1px solid var(--accent);
  border-radius: 9px;
  color: #ffffff;
  background: var(--accent);
  cursor: pointer;
  transition: background 0.12s, transform 0.1s;
}

.chat__copy-session {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  align-self: flex-end;
  border: 1px solid var(--border);
  border-radius: 9px;
  color: var(--text-muted);
  background: #f8fbff;
  cursor: pointer;
  transition: color .12s ease, border-color .12s ease, background .12s ease, transform .1s ease;
}

.chat__copy-session:hover {
  color: var(--accent-text);
  border-color: var(--accent-border);
  background: var(--accent-bg);
}

.chat__copy-session:active {
  transform: scale(.95);
}

.chat__send:hover {
  background: var(--accent-hover);
}

.chat__send:active {
  transform: scale(.95);
}

.chat__send.is-stop {
  border-color: var(--err);
  background: var(--err);
}

.chat__send.is-stop:hover {
  background: #b91c1c;
}

.chat__send:disabled {
  opacity: 0.35;
  cursor: default;
  transform: none;
}

.chat__composer-meta {
  display: flex;
  justify-content: space-between;
  padding: 0 13px 9px;
  color: var(--text-muted);
  font: 8.5px/1 var(--mono);
}

/* ---- clearify question panel ---- */
.chat__ask {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0 11px;
  padding: 12px 14px 13px;
  border: 1px solid var(--border);
  border-left: 2px solid var(--accent);
  border-radius: var(--radius-sm);
  background: #fff;
  box-shadow: 0 1px 2px rgba(23, 72, 150, 0.04);
  overflow: hidden;
}

.chat__ask-particle {
  position: absolute;
  border-radius: 50%;
  pointer-events: none;
  will-change: transform, opacity;
}

.chat__ask-head {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.chat__ask-badge {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: 99px;
  color: var(--accent-text);
  background: var(--accent-bg);
  font: 700 8px/1.5 var(--mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.chat__ask-badge::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
}

.chat__ask-question {
  min-width: 0;
  color: var(--text-h);
  font: 600 13px/1.5 var(--sans);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.chat__ask-reason {
  align-self: flex-start;
  max-width: 100%;
  padding: 0;
  border: 0;
  color: var(--text-muted);
  background: transparent;
  font: 10px/1.5 var(--sans);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: help;
}

.chat__ask-options {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.chat__ask-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: 9px;
  color: var(--text);
  background: #fff;
  font: 600 11.5px/1.35 var(--sans);
  text-align: left;
  cursor: pointer;
  transition: border-color .14s, color .14s, background .14s, transform .1s, box-shadow .14s;
}

.chat__ask-option:hover {
  border-color: var(--accent-border);
  color: var(--accent-text);
  background: var(--accent-bg);
  box-shadow: 0 2px 8px rgba(23, 86, 209, 0.08);
}

.chat__ask-option:active {
  transform: scale(.985);
}

.chat__ask-option.is-recommended {
  border-color: var(--accent-border);
  background: var(--accent-bg);
}

.chat__ask-option-side {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.chat__ask-option-rec {
  padding: 2px 7px;
  border-radius: 99px;
  color: var(--accent-text);
  background: var(--accent-bg);
  border: 1px solid var(--accent-border);
  font: 700 7.5px/1.4 var(--mono);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.chat__ask-option-arrow {
  color: var(--text-muted);
  opacity: 0;
  transform: translateX(-4px);
  transition: opacity .14s, transform .14s, color .14s;
}

.chat__ask-option:hover .chat__ask-option-arrow {
  opacity: 1;
  transform: translateX(0);
  color: var(--accent-text);
}

.chat__ask-hint {
  color: var(--text-muted);
  font: 10px/1.4 var(--sans);
}

@keyframes status-pulse { 50% { transform: scale(1.45); opacity: .58; } }
@keyframes meter-shimmer {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}
</style>
