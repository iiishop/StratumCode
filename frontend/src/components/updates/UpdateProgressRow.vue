<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import gsap from 'gsap'
import { parseBlock } from '../../lib/markdown'

const props = defineProps({
  title: { type: String, required: true },
  currentLabel: { type: String, required: true },
  targetLabel: { type: String, required: true },
  detail: { type: String, default: '' },
  available: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  progress: { type: Number, default: 0 },
  state: { type: String, default: 'idle' },
  message: { type: String, default: '' },
  releaseName: { type: String, default: '' },
  releaseBody: { type: String, default: '' },
  releaseUrl: { type: String, default: '' },
  releaseDate: { type: String, default: '' },
  commitsBehind: { type: Number, default: 0 },
  branchName: { type: String, default: '' },
})

const emit = defineEmits(['start', 'restart'])
const running = computed(() => props.state === 'running')
const done = computed(() => props.state === 'done')
const failed = computed(() => props.state === 'error')
const notesExpanded = ref(false)
const parsedNotes = computed(() => props.releaseBody ? parseBlock(props.releaseBody) : '')
const accentTrack = computed(() => props.title.toLowerCase().includes('dev') ? 'dev' : 'stable')

const relativeTime = computed(() => {
  if (!props.releaseDate) return ''
  const diff = Date.now() - new Date(props.releaseDate).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  return `${months}mo ago`
})

const statusText = computed(() => {
  if (done.value) return 'Updated'
  if (running.value) return 'Updating'
  return props.available ? 'Update available' : 'Current'
})

// 折叠时显示的摘要：去掉 markdown 标记取前 90 字符
const previewText = computed(() => {
  if (!props.releaseBody) return ''
  const text = props.releaseBody
    .split('\n')
    .map(line => line
      .replace(/^#{1,6}\s*/, '')
      .replace(/^[-*+]\s*/, '')
      .replace(/[*_`~]/g, '')
      .trim())
    .filter(Boolean)
    .join(' ')
    .trim()
  return text.length > 90 ? `${text.slice(0, 90)}…` : text
})

// -- animation refs --------------------------------------------------
const rowEl = ref(null)
const arrowBtn = ref(null)
const progressBar = ref(null)
const versionCurrent = ref(null)
const versionTarget = ref(null)
const fireworkContainer = ref(null)
const edgeShine = ref(null)
const restartBtn = ref(null)

let progressTl = null
let spinOutTl = null
let prevProgress = 0
let completionFired = false

// -- progress indicator position (CSS custom property) ---------------
const barStyle = computed(() => {
  const pct = Math.min(100, Math.max(0, props.progress))
  return { '--bar-pct': `${pct}%` }
})

const pctText = computed(() => `${Math.round(Math.min(100, Math.max(0, props.progress)))}%`)

// -- start animation when state becomes running ----------------------
watch(() => props.state, async (s, prev) => {
  if (s === 'running') {
    completionFired = false
    await nextTick()
    if (!progressBar.value) return

    // Kill any stale animation
    progressTl?.kill()
    spinOutTl?.kill()

    const bar = progressBar.value

    // 进度条在版本行下方展开：0 → 10px + 淡入，箭头按钮留在原位
    progressTl = gsap.timeline({ defaults: { ease: 'power2.out' } })
    progressTl
      .set(bar, { height: '10px', width: '100%' })
      .to(bar, { opacity: 1, duration: 0.2 }, 0.05)

    // Start tracking progress
    prevProgress = 0
    requestAnimationFrame(trackProgress)
    return
  }
  // 更新过快时 progress 还没到 100、state 已经 done（本地更新秒级完成），
  // trackProgress 不会触发完成动画——这里强制补发。
  if (s === 'done' && prev === 'running' && !completionFired) {
    await nextTick()
    onProgressComplete()
  }
})

// -- track progress updates ------------------------------------------
function trackProgress() {
  if (props.state !== 'running') return
  const pct = Math.min(100, Math.max(0, props.progress))
  if (pct < 100) {
    requestAnimationFrame(trackProgress)
  } else {
    // Progress complete → firework
    onProgressComplete()
  }
}

// -- completion animation --------------------------------------------
async function onProgressComplete() {
  if (completionFired) return
  completionFired = true
  await nextTick()
  if (!progressBar.value || !fireworkContainer.value || !edgeShine.value) return

  const bar = progressBar.value
  const fireworks = fireworkContainer.value
  const shine = edgeShine.value

  // Edge shine slides around progress bar
  spinOutTl = gsap.timeline()
  spinOutTl
    .fromTo(shine, { opacity: 1, left: 0, top: 0 }, {
      duration: 0.6,
      ease: 'none',
      keyframes: {
        '0%': { left: 0, top: 0 },
        '25%': { left: 'calc(100% - 8px)', top: 0 },
        '50%': { left: 'calc(100% - 8px)', top: 'calc(100% - 2px)' },
        '75%': { left: 0, top: 'calc(100% - 2px)' },
        '100%': { left: 0, top: 0 },
      },
    }, 0)
    .to(shine, { opacity: 0, duration: 0.15 }, 0.6)
  // Firework burst at the progress bar end
  spawnFireworks(fireworks)
}

// -- firework particles ----------------------------------------------
function spawnFireworks(container) {
  const count = 16
  const colors = ['#3b82f6', '#ef4444', '#f59e0b', '#10b981', '#ffffff', '#93c5fd']
  for (let i = 0; i < count; i++) {
    const particle = document.createElement('span')
    const angle = (i / count) * Math.PI * 2 + (Math.random() - 0.5) * 0.6
    const distance = 24 + Math.random() * 32
    const color = colors[Math.floor(Math.random() * colors.length)]
    particle.style.setProperty('--angle', `${angle}rad`)
    particle.style.setProperty('--dist', `${distance}px`)
    particle.style.setProperty('--color', color)
    particle.style.setProperty('--delay', `${Math.random() * 0.08}s`)
    particle.className = 'firework-particle'
    container.appendChild(particle)
    // Auto-remove after animation
    setTimeout(() => particle.remove(), 600)
  }
}

// -- cleanup ---------------------------------------------------------
onBeforeUnmount(() => {
  progressTl?.kill()
  spinOutTl?.kill()
})
</script>

<template>
  <article
    ref="rowEl"
    class="update-row"
    :class="[`track--${accentTrack}`, { 'is-running': running, 'is-done': done, 'is-error': failed, 'is-disabled': disabled }]"
  >
    <!-- 头部：渠道名 + 状态 -->
    <div class="update-row__head">
      <div class="update-row__channel">
        <span class="update-row__dot"></span>
        <h3>{{ title }}</h3>
      </div>
      <span class="update-row__badge" :class="{ 'is-alert': available && !failed && !running, 'is-running': running, 'is-done': done, 'is-error': failed }">
        {{ statusText }}
      </span>
    </div>

    <!-- 版本对比 + 进度动画舞台 -->
    <div class="update-row__stage">
      <div
        ref="progressBar"
        class="update-row__progress-bar"
        :class="{ 'is-visible': running || done }"
        :style="barStyle"
      >
        <span class="update-row__progress-head"></span>
        <span v-if="running" class="update-row__progress-pct">{{ pctText }}</span>
        <span ref="edgeShine" class="update-row__edge-shine"></span>
        <span ref="fireworkContainer" class="update-row__fireworks"></span>
      </div>

      <div class="update-row__version-row">
        <span ref="versionCurrent" class="update-row__current-ver">{{ currentLabel }}</span>
        <span v-show="!running && !done" class="update-row__arrow-sym">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
        </span>
        <span
          ref="versionTarget"
          class="update-row__target-ver"
          :style="{ '--reveal': `${Math.min(100, Math.max(0, props.progress))}%` }"
        >{{ targetLabel }}</span>

        <button
          ref="arrowBtn"
          class="update-row__arrow"
          type="button"
          :disabled="!available || disabled || running || done"
          aria-label="Start update"
          @click="emit('start')"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        </button>

        <button
          v-if="done"
          ref="restartBtn"
          class="update-row__restart"
          type="button"
          @click="emit('restart')"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/>
          </svg>
          Restart
        </button>
      </div>

      <!-- 完成徽章 -->
      <Transition name="done-pop">
        <div v-if="done" class="update-row__done">
          <span class="update-row__done-check">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
          </span>
          <span class="update-row__done-label">Updated to</span>
          <span class="update-row__done-ver">{{ targetLabel }}</span>
        </div>
      </Transition>
    </div>

    <!-- 元信息 -->
    <div v-if="releaseName || relativeTime || (accentTrack === 'dev' && commitsBehind)" class="update-row__meta">
      <a v-if="releaseUrl" class="update-row__gh-link" :href="releaseUrl" target="_blank" rel="noopener" title="View on GitHub">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" />
        </svg>
      </a>
      <span v-if="releaseName" class="update-row__release-name">{{ releaseName }}</span>
      <span v-if="relativeTime" class="update-row__release-date">{{ relativeTime }}</span>
      <span v-if="accentTrack === 'dev' && commitsBehind" class="update-row__behind">{{ commitsBehind }} commits behind {{ branchName || 'main' }}</span>
    </div>

    <p class="update-row__detail">{{ message || detail }}</p>

    <div v-if="releaseBody" class="update-row__notes" :class="{ 'is-open': notesExpanded }">
      <button class="update-row__notes-toggle" type="button" @click="notesExpanded = !notesExpanded">
        <span class="update-row__notes-toggle-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="8" y1="13" x2="16" y2="13" /><line x1="8" y1="17" x2="13" y2="17" />
          </svg>
        </span>
        <span class="update-row__notes-toggle-text">
          <span class="update-row__notes-toggle-label">Release notes</span>
          <span v-if="!notesExpanded && previewText" class="update-row__notes-preview">{{ previewText }}</span>
        </span>
        <span class="update-row__notes-toggle-btn">
          {{ notesExpanded ? 'Hide' : 'Show' }}
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" :class="{ 'is-open': notesExpanded }">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </span>
      </button>
      <div class="update-row__notes-body-wrap" :class="{ 'is-open': notesExpanded }">
        <div class="update-row__notes-body" v-html="parsedNotes"></div>
      </div>
    </div>
  </article>
</template>

<style scoped>
/* --- 区块卡片 --- */
.update-row {
  position: relative;
  display: grid;
  gap: 12px;
  padding: 16px 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background:
    radial-gradient(140% 120% at 100% 0%, rgba(59, 130, 246, 0.05), transparent 60%),
    linear-gradient(180deg, #ffffff, #fafbfd);
  box-shadow: 0 1px 3px rgba(16, 42, 92, 0.05);
  transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
}

.update-row:hover {
  transform: translateY(-1px);
  border-color: var(--accent-border);
  box-shadow: 0 8px 24px rgba(16, 42, 92, 0.09);
}

.update-row::before {
  content: "";
  position: absolute;
  left: 0;
  top: 14px;
  bottom: 14px;
  width: 3px;
  border-radius: 3px;
  background: var(--border-strong);
}

.track--stable::before { background: linear-gradient(180deg, #3b82f6, #60a5fa); }
.track--dev::before { background: linear-gradient(180deg, #10b981, #34d399); }

.update-row.is-disabled { opacity: 0.55; }
.update-row.is-error { border-color: var(--err-border); }
.update-row.is-error .update-row__detail { color: var(--err); }

/* done 行绿色氛围 */
.update-row.is-done {
  border-color: rgba(16, 185, 129, 0.35);
  background:
    radial-gradient(140% 120% at 100% 0%, rgba(16, 185, 129, 0.07), transparent 60%),
    linear-gradient(180deg, #ffffff, #f5fbf8);
  box-shadow: 0 1px 3px rgba(16, 185, 129, 0.08);
}

.update-row.is-done .update-row__detail {
  color: #047857;
}

/* --- 头部 --- */
.update-row__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.update-row__channel {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.update-row__dot {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--border-strong);
}

.track--stable .update-row__dot { background: #3b82f6; }
.track--dev .update-row__dot { background: #10b981; }

.update-row.is-running .update-row__dot {
  animation: dot-pulse 1.1s ease-in-out infinite;
}

@keyframes dot-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.35); }
  50% { box-shadow: 0 0 0 5px rgba(59, 130, 246, 0); }
}
.track--dev.is-running .update-row__dot {
  animation-name: dot-pulse-green;
}
@keyframes dot-pulse-green {
  0%, 100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.35); }
  50% { box-shadow: 0 0 0 5px rgba(16, 185, 129, 0); }
}

.update-row__channel h3 {
  margin: 0;
  color: var(--text-h);
  font: 600 13.5px/1.2 var(--heading);
  letter-spacing: -0.01em;
}

.update-row__badge {
  flex-shrink: 0;
  padding: 3px 10px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-muted);
  font: 600 9.5px/1 var(--mono);
  letter-spacing: 0.03em;
  background: var(--code-bg);
  transition: color 160ms, border-color 160ms, background 160ms;
}

.update-row__badge.is-alert {
  border-color: rgba(245, 200, 66, 0.65);
  color: #8a6d14;
  background: linear-gradient(180deg, rgba(245, 200, 66, 0.22), rgba(245, 200, 66, 0.12));
}

.update-row__badge.is-running {
  border-color: rgba(59, 130, 246, 0.4);
  color: #1d4ed8;
  background: rgba(59, 130, 246, 0.1);
}

.track--dev .update-row__badge.is-running {
  border-color: rgba(16, 185, 129, 0.4);
  color: #047857;
  background: rgba(16, 185, 129, 0.1);
}

.update-row__badge.is-done {
  border-color: rgba(16, 185, 129, 0.4);
  color: #047857;
  background: rgba(16, 185, 129, 0.1);
}

.update-row__badge.is-error {
  border-color: rgba(239, 68, 68, 0.4);
  color: #b91c1c;
  background: rgba(239, 68, 68, 0.08);
}

/* --- 版本对比舞台 --- */
.update-row__stage {
  position: relative;
  display: grid;
  gap: 8px;
  min-height: 44px;
}

.update-row__version-row {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 0 4px;
  transition: opacity 0.18s ease;
}

.update-row__current-ver {
  padding: 3px 10px;
  border-radius: 999px;
  font: 12px/1 var(--mono);
  color: var(--text-muted);
  background: var(--code-bg);
  border: 1px solid var(--border);
  white-space: nowrap;
  transition: opacity 0.26s ease, transform 0.26s ease;
}

.update-row__arrow-sym {
  display: grid;
  place-items: center;
  color: var(--border-strong);
  flex-shrink: 0;
}

.update-row__target-ver {
  padding: 3px 10px;
  border-radius: 999px;
  font: 12px/1 var(--mono);
  font-weight: 700;
  color: #ffffff;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.22);
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  clip-path: inset(0 calc(100% - var(--reveal, 0%)) 0 0);
  transition: clip-path 0.26s cubic-bezier(0.16, 1, 0.3, 1);
}

.track--dev .update-row__target-ver {
  background: linear-gradient(135deg, #10b981, #059669);
  box-shadow: 0 2px 8px rgba(16, 185, 129, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.22);
}

.update-row.is-running .update-row__target-ver {
  opacity: 1;
}

/* done 后版本对比让位给对勾徽章 */
.update-row.is-done .update-row__target-ver,
.update-row.is-done .update-row__arrow-sym,
.update-row.is-done .update-row__current-ver,
.update-row.is-done .update-row__arrow {
  opacity: 0;
  pointer-events: none;
}

/* --- 进度条 --- */
.update-row__progress-bar {
  position: relative;
  left: 0;
  z-index: 1;
  height: 0;
  pointer-events: none;
  opacity: 0;
  border: 1px solid rgba(59, 130, 246, 0.4);
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(59, 130, 246, 0.12), rgba(59, 130, 246, 0.05) 70%);
  overflow: hidden;
  transition: opacity 0.18s ease;
}

.track--dev .update-row__progress-bar {
  border-color: rgba(16, 185, 129, 0.4);
  background: linear-gradient(90deg, rgba(16, 185, 129, 0.12), rgba(16, 185, 129, 0.05) 70%);
}

.update-row__progress-bar::after {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: var(--bar-pct, 0%);
  border-radius: 999px;
  background: linear-gradient(90deg,
    #3b82f6,
    #60a5fa 55%,
    #93c5fd 100%
  );
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.35),
    0 0 10px rgba(59, 130, 246, 0.5);
  transition: width 0.26s cubic-bezier(0.16, 1, 0.3, 1);
}

.track--dev .update-row__progress-bar::after {
  background: linear-gradient(90deg,
    #10b981,
    #34d399 55%,
    #6ee7b7 100%
  );
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.35),
    0 0 10px rgba(16, 185, 129, 0.5);
}

/* 进度条端头发光点 */
.update-row__progress-head {
  position: absolute;
  z-index: 2;
  left: var(--bar-pct, 0%);
  top: 50%;
  transform: translate(-50%, -50%);
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #ffffff;
  box-shadow: 0 0 8px 2px rgba(255, 255, 255, 0.8), 0 0 16px 4px rgba(59, 130, 246, 0.5);
  transition: left 0.26s cubic-bezier(0.16, 1, 0.3, 1);
  pointer-events: none;
}

.track--dev .update-row__progress-head {
  box-shadow: 0 0 8px 2px rgba(255, 255, 255, 0.8), 0 0 16px 4px rgba(16, 185, 129, 0.5);
}

/* done 时端头光点让位给对勾徽章 */
.is-done .update-row__progress-head {
  display: none;
}

/* 百分比数字 */
.update-row__progress-pct {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 3;
  font: 700 9px/1 var(--mono);
  color: #ffffff;
  text-shadow: 0 1px 2px rgba(16, 42, 92, 0.4);
  pointer-events: none;
}

/* running 时斜纹流动 */
.is-running .update-row__progress-bar::after {
  background-image:
    linear-gradient(90deg,
      #3b82f6,
      #60a5fa 55%,
      #93c5fd 100%),
    repeating-linear-gradient(45deg, rgba(255, 255, 255, 0.22) 0 6px, transparent 6px 12px);
  background-size: 100% 100%, 200% 100%;
  animation: bar-stripes 1.1s linear infinite;
}

.track--dev.is-running .update-row__progress-bar::after {
  background-image:
    linear-gradient(90deg,
      #10b981,
      #34d399 55%,
      #6ee7b7 100%),
    repeating-linear-gradient(45deg, rgba(255, 255, 255, 0.22) 0 6px, transparent 6px 12px);
  background-size: 100% 100%, 200% 100%;
  animation: bar-stripes 1.1s linear infinite;
}

@keyframes bar-stripes {
  to { background-position: 0 0, 200% 0; }
}

.is-done .update-row__progress-bar.is-visible {
  opacity: 1;
  position: relative;
  height: 0;
  margin-bottom: 0;
}

/* done 时进度条满格绿 + 光脉冲 */
.is-done .update-row__progress-bar.is-visible::after {
  width: 100%;
  background: linear-gradient(90deg, #10b981, #34d399);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 0 12px rgba(16, 185, 129, 0.6);
  animation: done-glow 1.4s ease-out;
}

@keyframes done-glow {
  0% { box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 0 0 0 rgba(16, 185, 129, 0.55); }
  100% { box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 0 0 14px rgba(16, 185, 129, 0); }
}

/* --- 完成徽章 --- */
.update-row__done {
  position: absolute;
  left: 4px;
  top: 16px;
  transform: translateY(-50%);
  z-index: 6;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 5px 12px 5px 7px;
  border: 1px solid rgba(16, 185, 129, 0.4);
  border-radius: 999px;
  background: linear-gradient(180deg, #ffffff, rgba(16, 185, 129, 0.08));
  box-shadow: 0 2px 8px rgba(16, 185, 129, 0.18), inset 0 1px 0 rgba(255, 255, 255, 0.9);
}

.update-row__done-check {
  display: grid;
  width: 18px;
  height: 18px;
  place-items: center;
  border-radius: 50%;
  color: #ffffff;
  background: linear-gradient(135deg, #10b981, #059669);
  box-shadow: 0 1px 4px rgba(16, 185, 129, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.3);
}

.update-row__done-label {
  font: 600 10px/1 var(--sans);
  color: var(--text-muted);
}

.update-row__done-ver {
  font: 700 11.5px/1 var(--mono);
  color: #047857;
}

.done-pop-enter-active {
  transition: transform 0.34s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.22s ease;
}

.done-pop-enter-from {
  transform: translateY(-50%) scale(0.7);
  opacity: 0;
}

/* --- edge shine / fireworks --- */
.update-row__edge-shine {
  position: absolute;
  z-index: 3;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 0 8px 2px rgba(255, 255, 255, 0.7);
  opacity: 0;
  pointer-events: none;
}

.update-row__fireworks {
  position: absolute;
  right: 0;
  top: 50%;
  z-index: 5;
  pointer-events: none;
}

:global(.firework-particle) {
  position: absolute;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--color);
  left: 0;
  top: 0;
  animation: firework-burst 0.5s cubic-bezier(0.25, 0.46, 0.45, 0.94) both;
  animation-delay: var(--delay);
}

@keyframes firework-burst {
  0% {
    opacity: 1;
    transform: translate(0, 0) scale(1);
  }
  100% {
    opacity: 0;
    transform: translate(
      calc(cos(var(--angle)) * var(--dist)),
      calc(sin(var(--angle)) * var(--dist))
    ) scale(0.2);
  }
}

/* --- 主操作按钮（箭头） --- */
.update-row__arrow {
  position: relative;
  z-index: 4;
  display: grid;
  width: 32px;
  height: 32px;
  padding: 0;
  place-items: center;
  border: 1px solid rgba(59, 130, 246, 0.55);
  border-radius: 50%;
  color: #ffffff;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 2px 8px rgba(59, 130, 246, 0.3);
  cursor: pointer;
  flex-shrink: 0;
  margin-left: auto;
  transition: transform 140ms ease, box-shadow 140ms ease;
}

.track--dev .update-row__arrow {
  background: linear-gradient(135deg, #10b981, #059669);
  border-color: rgba(16, 185, 129, 0.55);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 2px 8px rgba(16, 185, 129, 0.3);
}

.update-row__arrow:not(:disabled):hover {
  transform: scale(1.1);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 4px 14px rgba(59, 130, 246, 0.42);
}

.track--dev .update-row__arrow:not(:disabled):hover {
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 4px 14px rgba(16, 185, 129, 0.42);
}

.update-row__arrow:disabled {
  cursor: default;
  opacity: 0.38;
}

/* running 时箭头拖尾光 */
.is-running .update-row__arrow::after {
  content: "";
  position: absolute;
  right: 100%;
  top: 50%;
  transform: translateY(-50%);
  width: 40px;
  height: 10px;
  border-radius: 999px;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.45));
  filter: blur(4px);
  pointer-events: none;
}

/* --- restart 按钮 --- */
.update-row__restart {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 16px;
  border: 1px solid #10b981;
  border-radius: 999px;
  color: #ffffff;
  background: linear-gradient(135deg, #10b981, #059669);
  font: 600 11px/1 var(--mono);
  cursor: pointer;
  flex-shrink: 0;
  margin-left: auto;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 2px 8px rgba(16, 185, 129, 0.32);
  transition: transform 150ms cubic-bezier(0.16, 1, 0.3, 1), box-shadow 150ms ease, filter 150ms ease;
}

.update-row__restart:hover {
  transform: translateY(-1px);
  filter: brightness(1.06);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.28), 0 5px 16px rgba(16, 185, 129, 0.42);
}

.update-row__restart:active {
  transform: translateY(0) scale(0.96);
  filter: brightness(0.94);
  box-shadow: inset 0 2px 4px rgba(16, 42, 92, 0.25), 0 1px 3px rgba(16, 185, 129, 0.2);
}

.update-row__restart svg {
  flex-shrink: 0;
}

/* --- 元信息 --- */
.update-row__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
}

.update-row__gh-link {
  flex-shrink: 0;
  display: grid;
  width: 26px;
  height: 26px;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  transition: color 120ms, border-color 120ms, background 120ms;
}

.update-row__gh-link:hover {
  color: #3b82f6;
  border-color: rgba(59, 130, 246, 0.3);
  background: rgba(59, 130, 246, 0.06);
}

.update-row__release-name { font: 500 12px/1.3 var(--heading); color: var(--text-h); }
.update-row__release-date {
  padding: 2px 7px;
  border-radius: 999px;
  font: 9.5px/1 var(--mono);
  color: var(--text-muted);
  background: var(--code-bg);
  border: 1px solid var(--border);
}
.update-row__behind {
  font: 10px/1 var(--mono);
  color: var(--text-muted);
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(245, 200, 66, 0.12);
  border: 1px solid rgba(245, 200, 66, 0.35);
}

/* --- 详情 --- */
.update-row__detail {
  margin: 0;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.55;
  min-height: 17px;
}

/* --- release notes --- */
.update-row__notes { margin-top: 2px; }

.update-row__notes-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-muted);
  background: linear-gradient(180deg, #ffffff, var(--code-bg));
  font: 600 10px/1 var(--mono);
  cursor: pointer;
  text-align: left;
  transition: border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
}

.update-row__notes-toggle:hover {
  border-color: var(--accent-border);
  background: linear-gradient(180deg, #ffffff, var(--accent-bg));
  box-shadow: 0 2px 8px rgba(23, 86, 209, 0.08);
}

.update-row.is-open .update-row__notes-toggle {
  border-color: var(--accent-border);
  border-bottom-left-radius: 0;
  border-bottom-right-radius: 0;
}

.update-row__notes-toggle-icon {
  display: grid;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text-muted);
  background: #ffffff;
  transition: color 160ms ease, border-color 160ms ease;
}

.update-row__notes-toggle:hover .update-row__notes-toggle-icon {
  color: var(--accent);
  border-color: var(--accent-border);
}

.update-row__notes-toggle-text {
  display: grid;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.update-row__notes-toggle-label {
  font: 600 10.5px/1.2 var(--sans);
  color: var(--text-h);
  letter-spacing: 0.01em;
}

.update-row__notes-preview {
  overflow: hidden;
  font: 10.5px/1.45 var(--sans);
  color: var(--text-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.update-row__notes-toggle-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding: 4px 9px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-muted);
  background: #ffffff;
  font: 600 9.5px/1 var(--mono);
  transition: color 160ms ease, border-color 160ms ease, background 160ms ease;
}

.update-row__notes-toggle:hover .update-row__notes-toggle-btn {
  color: var(--accent);
  border-color: var(--accent-border);
  background: var(--accent-bg);
}

.update-row__notes-toggle-btn svg { transition: transform 220ms cubic-bezier(0.16, 1, 0.3, 1); }
.update-row__notes-toggle-btn svg.is-open { transform: rotate(180deg); }

/* 展开高度动画：grid-template-rows 0fr → 1fr */
.update-row__notes-body-wrap {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 0.32s cubic-bezier(0.16, 1, 0.3, 1);
}

.update-row__notes-body-wrap.is-open {
  grid-template-rows: 1fr;
}

.update-row__notes-body-wrap > .update-row__notes-body {
  overflow: hidden; /* 折叠/动画期间隐藏溢出 */
  min-height: 0;
}

/* 展开后允许内部滚动（覆盖折叠时的 hidden） */
.update-row__notes-body-wrap.is-open > .update-row__notes-body {
  overflow-y: auto;
}

.update-row__notes-body::-webkit-scrollbar {
  width: 8px;
}

.update-row__notes-body::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: var(--border-strong);
  border: 2px solid transparent;
  background-clip: content-box;
}

.update-row__notes-body::-webkit-scrollbar-thumb:hover {
  background: var(--text-muted);
  border: 2px solid transparent;
  background-clip: content-box;
}

.update-row__notes-body::-webkit-scrollbar-track {
  background: transparent;
}

.update-row__notes-body {
  margin: 0;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-top: 0;
  border-radius: 0 0 var(--radius) var(--radius);
  background:
    radial-gradient(140% 100% at 100% 0%, rgba(59, 130, 246, 0.05), transparent 55%),
    rgba(248, 250, 253, 0.75);
  max-height: 300px;
  overflow-y: auto;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-muted);
  opacity: 0;
  transform: translateY(-4px);
  transition: opacity 0.26s ease 0.06s, transform 0.3s cubic-bezier(0.16, 1, 0.3, 1) 0.06s;
}

.update-row__notes-body-wrap.is-open > .update-row__notes-body {
  opacity: 1;
  transform: translateY(0);
}

.update-row__notes-body :deep(h2),
.update-row__notes-body :deep(h3) {
  position: relative;
  margin: 14px 0 6px;
  padding-left: 10px;
  font-size: 12.5px;
  color: var(--text-h);
}

.update-row__notes-body :deep(h2)::before,
.update-row__notes-body :deep(h3)::before {
  content: "";
  position: absolute;
  left: 0;
  top: 2px;
  bottom: 2px;
  width: 3px;
  border-radius: 3px;
  background: linear-gradient(180deg, #3b82f6, #60a5fa);
}

.track--dev .update-row__notes-body :deep(h2)::before,
.track--dev .update-row__notes-body :deep(h3)::before {
  background: linear-gradient(180deg, #10b981, #34d399);
}

.update-row__notes-body :deep(h2):first-child,
.update-row__notes-body :deep(h3):first-child { margin-top: 0; }

.update-row__notes-body :deep(p) { margin: 6px 0; }

.update-row__notes-body :deep(ul),
.update-row__notes-body :deep(ol) {
  margin: 6px 0;
  padding-left: 20px;
}

.update-row__notes-body :deep(ul) { list-style: none; }
.update-row__notes-body :deep(ul > li) { position: relative; margin: 3px 0; padding-left: 4px; }
.update-row__notes-body :deep(ul > li)::before {
  content: "";
  position: absolute;
  left: -14px;
  top: 0.55em;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
}

.track--dev .update-row__notes-body :deep(ul > li)::before {
  background: #10b981;
}

.update-row__notes-body :deep(ol) { list-style: decimal; }
.update-row__notes-body :deep(ol > li) { margin: 3px 0; }
.update-row__notes-body :deep(ol > li)::marker { color: var(--accent); font-weight: 700; }

.update-row__notes-body :deep(code) {
  padding: 1.5px 5px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: #eef2f8;
  font-size: 10.5px;
  color: var(--text-h);
}

.update-row__notes-body :deep(pre) {
  margin: 8px 0;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: #0f1b33;
  overflow-x: auto;
}

.update-row__notes-body :deep(pre code) {
  padding: 0;
  border: 0;
  background: transparent;
  color: #dbe6ff;
}

.update-row__notes-body :deep(a) {
  color: #3b82f6;
  text-decoration: none;
  border-bottom: 1px dashed rgba(59, 130, 246, 0.4);
  transition: border-color 120ms;
}

.update-row__notes-body :deep(a:hover) {
  border-bottom-style: solid;
}

.update-row__notes-body :deep(blockquote) {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid var(--border-strong);
  border-radius: 0 6px 6px 0;
  background: rgba(16, 42, 92, 0.04);
  color: var(--text-muted);
}

.update-row__notes-body :deep(strong) { color: var(--text-h); }

.update-row__notes-body :deep(hr) {
  margin: 10px 0;
  border: 0;
  height: 1px;
  background: var(--border);
}

/* --- reduced motion --- */
@media (prefers-reduced-motion: reduce) {
  .update-row__arrow,
  .update-row__current-ver,
  .update-row__target-ver,
  .update-row__progress-bar::after,
  .update-row__edge-shine,
  .update-row__restart,
  .update-row__dot {
    animation: none !important;
    transition: none !important;
  }
}

/* --- responsive --- */
@media (max-width: 620px) {
  .update-row__stage { min-height: auto; }
  .update-row__restart { width: 100%; }
}
</style>
