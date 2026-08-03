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
  if (running.value) return 'Updating\u2026'
  return props.available ? 'Update available' : 'Current'
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

// -- start animation when state becomes running ----------------------
watch(() => props.state, async (s, prev) => {
  if (s === 'running') {
    completionFired = false
    await nextTick()
    if (!arrowBtn.value || !versionCurrent.value || !progressBar.value) return

    // Kill any stale animation
    progressTl?.kill()
    spinOutTl?.kill()

    const arrow = arrowBtn.value
    const curLabel = versionCurrent.value
    const bar = progressBar.value
    const labelHeight = curLabel.offsetHeight

    // Phase 1: arrow shrink + text fade + move left + rotate 90°
    const arrowRect = arrow.getBoundingClientRect()
    const labelRect = curLabel.getBoundingClientRect()
    const moveX = labelRect.left - arrowRect.left + labelRect.width + 8

    progressTl = gsap.timeline({ defaults: { ease: 'power2.inOut' } })

    progressTl
      .to(curLabel, { opacity: 0.8, scale: 0.96, duration: 0.26 }, 0)
      .to(arrow, {
        scale: 0.86,
        x: moveX,
        rotate: 90,
        duration: 0.26,
        ease: 'power3.inOut',
      }, 0)
      .set(bar, {
        height: `${labelHeight * 1.1}px`,
        width: '100%',
      }, 0.1)
      .to(bar, { opacity: 1, duration: 0.18 }, 0.18)
      .set(arrow, { position: 'absolute' }, 0)

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
  if (pct !== prevProgress) {
    prevProgress = pct
    // Arrow follows progress
    if (arrowBtn.value && progressBar.value) {
      const barWidth = progressBar.value.offsetWidth
      const arrowW = arrowBtn.value.offsetWidth
      const left = (barWidth * pct) / 100 - arrowW / 2
      gsap.to(arrowBtn.value, { left, duration: 0.26, ease: 'power2.out' })
    }
  }
  if (pct < 100) {
    requestAnimationFrame(trackProgress)
  } else {
    // Progress complete → spin-out + firework
    onProgressComplete()
  }
}

// -- completion animation --------------------------------------------
async function onProgressComplete() {
  if (completionFired) return
  completionFired = true
  await nextTick()
  if (!arrowBtn.value || !fireworkContainer.value || !edgeShine.value || !versionTarget.value) return

  const arrow = arrowBtn.value
  const fireworks = fireworkContainer.value
  const shine = edgeShine.value
  const newLabel = versionTarget.value
  const restart = restartBtn.value

  spinOutTl = gsap.timeline()

  // Spin out: 0.1s = 1 rotation, 0.3s = 5 rotations total, size to 200%, opacity to 0
  spinOutTl
    .to(arrow, {
      rotate: 360 * 5 + 90,
      scale: 2,
      opacity: 0,
      duration: 0.3,
      ease: 'power4.in',
      onComplete: () => {
        // Firework burst at arrow position
        spawnFireworks(fireworks)
        arrow.style.display = 'none'
      },
    }, 0)
    // Edge shine slides around progress bar
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
    }, 0.3)
    .to(shine, { opacity: 0, duration: 0.15 }, 0.9)
    // New version flash
    .fromTo(newLabel, { opacity: 0.5 }, {
      opacity: 1,
      duration: 0.22,
      repeat: 1,
      yoyo: true,
      ease: 'power1.inOut',
    }, 0.5)
    // Fade to restart button
    .fromTo(versionCurrent.value, { display: 'block' }, { display: 'none', duration: 0 }, 0.8)
    .fromTo(newLabel, { opacity: 1 }, {
      opacity: 0,
      scale: 0.96,
      duration: 0.2,
      ease: 'power2.in',
    }, 0.82)
  if (restart) {
    spinOutTl.fromTo(restart, { opacity: 0, scale: 0.92 }, {
      opacity: 1,
      scale: 1,
      duration: 0.22,
      ease: 'back.out(1.4)',
    }, 0.9)
  }
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
    <div class="update-row__header">
      <div class="update-row__channel">
        <h3>{{ title }}</h3>
        <span class="update-row__badge" :class="{ 'is-alert': available && !failed, 'is-done': done }">
          {{ statusText }}
        </span>
      </div>
      <a
        v-if="releaseUrl"
        class="update-row__gh-link"
        :href="releaseUrl"
        target="_blank"
        rel="noopener"
        title="View on GitHub"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" />
        </svg>
      </a>
    </div>

    <div v-if="releaseName" class="update-row__release-meta">
      <span class="update-row__release-name">{{ releaseName }}</span>
      <span v-if="relativeTime" class="update-row__release-date">{{ relativeTime }}</span>
      <span v-if="accentTrack === 'dev' && commitsBehind" class="update-row__behind">{{ commitsBehind }} commits behind {{ branchName || 'main' }}</span>
    </div>

    <div class="update-row__stage">
      <!-- Progress bar background -->
      <div
        ref="progressBar"
        class="update-row__progress-bar"
        :class="{ 'is-visible': running || done }"
        :style="barStyle"
      >
        <!-- Edge shine element -->
        <span ref="edgeShine" class="update-row__edge-shine"></span>
        <!-- Firework container (aligned with arrow end position) -->
        <span ref="fireworkContainer" class="update-row__fireworks"></span>
      </div>

      <!-- Version display area -->
      <div class="update-row__version-row">
        <span ref="versionCurrent" class="update-row__current-ver">{{ currentLabel }}</span>
        <span
          ref="versionTarget"
          class="update-row__target-ver"
          :style="{ '--reveal': `${Math.min(100, Math.max(0, props.progress))}%` }"
        >{{ targetLabel }}</span>

        <!-- Arrow button -->
        <button
          ref="arrowBtn"
          class="update-row__arrow"
          type="button"
          :disabled="!available || disabled || running || done"
          aria-label="Start update"
          @click="emit('start')"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        </button>

        <!-- Restart button (revealed after done) -->
        <button
          v-if="done"
          ref="restartBtn"
          class="update-row__restart"
          type="button"
          @click="emit('restart')"
        >Restart</button>
      </div>
    </div>

    <p class="update-row__detail">{{ message || detail }}</p>

    <div v-if="releaseBody" class="update-row__notes">
      <button class="update-row__notes-toggle" type="button" @click="notesExpanded = !notesExpanded">
        {{ notesExpanded ? 'Hide release notes' : 'Show release notes' }}
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" :class="{ 'is-open': notesExpanded }">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      <div v-if="notesExpanded" class="update-row__notes-body" v-html="parsedNotes"></div>
    </div>
  </article>
</template>

<style scoped>
/* --- row base --- */
.update-row {
  position: relative;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-raised);
  overflow: hidden;
}

.update-row::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  border-radius: 3px 0 0 3px;
  background: var(--border-strong);
}

.track--stable::before { background: #3b82f6; }
.track--dev::before { background: #10b981; }

.update-row.is-disabled { opacity: 0.5; }
.update-row.is-error { border-color: var(--err-border); }
.update-row.is-error .update-row__detail { color: var(--err); }

/* --- header --- */
.update-row__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.update-row__channel {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.update-row__channel h3 {
  margin: 0;
  color: var(--text-h);
  font: 570 13px/1.2 var(--heading);
}

.update-row__badge {
  flex-shrink: 0;
  padding: 1px 7px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-muted);
  font: 9.5px/1.3 var(--mono);
  background: var(--code-bg);
}

.update-row__badge.is-alert {
  border-color: rgba(245, 200, 66, 0.7);
  color: #8a6d14;
  background: var(--yellow-bg);
}

.update-row__badge.is-done {
  border-color: rgba(16, 185, 129, 0.4);
  color: #065f46;
  background: rgba(16, 185, 129, 0.1);
}

.update-row__gh-link {
  flex-shrink: 0;
  display: grid;
  width: 28px;
  height: 28px;
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

/* --- release meta --- */
.update-row__release-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-bottom: 10px;
}

.update-row__release-name { font: 500 12px/1.3 var(--heading); color: var(--text-h); }
.update-row__release-date {
  padding: 1px 6px;
  border-radius: 999px;
  font: 9.5px/1 var(--mono);
  color: var(--text-muted);
  background: var(--code-bg);
  border: 1px solid var(--border);
}
.update-row__behind { font: 10px/1 var(--mono); color: var(--text-muted); }

/* --- stage --- */
.update-row__stage {
  position: relative;
  min-height: 34px;
}

/* --- progress bar --- */
.update-row__progress-bar {
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  z-index: 1;
  height: 0;
  pointer-events: none;
  opacity: 0;
  border: 1px solid rgba(59, 130, 246, 0.34);
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(59, 130, 246, 0.13), rgba(59, 130, 246, 0.06) 70%);
  overflow: hidden;
  transition: opacity 0.18s ease;
}

.track--dev .update-row__progress-bar {
  border-color: rgba(16, 185, 129, 0.34);
  background: linear-gradient(90deg, rgba(16, 185, 129, 0.13), rgba(16, 185, 129, 0.06) 70%);
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
    rgba(59, 130, 246, 0.28),
    rgba(59, 130, 246, 0.18) 60%,
    rgba(255, 255, 255, 0.24) 100%
  );
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.14);
  transition: width 0.26s cubic-bezier(0.16, 1, 0.3, 1);
}

.track--dev .update-row__progress-bar::after {
  background: linear-gradient(90deg,
    rgba(16, 185, 129, 0.28),
    rgba(16, 185, 129, 0.18) 60%,
    rgba(255, 255, 255, 0.24) 100%
  );
}

/* --- edge shine --- */
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

/* --- version row --- */
.update-row__version-row {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding: 0 4px;
}

.update-row__current-ver {
  font: 12px/1 var(--mono);
  color: var(--text-h);
  white-space: nowrap;
  transition: opacity 0.26s ease, transform 0.26s ease;
}

.update-row__target-ver {
  position: absolute;
  left: 4px;
  top: 50%;
  transform: translateY(-50%);
  font: 12px/1 var(--mono);
  color: #ffffff;
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  clip-path: inset(0 calc(100% - var(--reveal, 0%)) 0 0);
  transition: clip-path 0.26s cubic-bezier(0.16, 1, 0.3, 1);
}

.update-row.is-running .update-row__target-ver,
.update-row.is-done .update-row__target-ver {
  opacity: 1;
}

/* --- arrow button --- */
.update-row__arrow {
  position: relative;
  z-index: 4;
  display: grid;
  width: 27px;
  height: 27px;
  padding: 0;
  place-items: center;
  border: 1px solid rgba(59, 130, 246, 0.5);
  border-radius: 50%;
  color: #ffffff;
  background: #3b82f6;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.2), 0 2px 8px rgba(59, 130, 246, 0.25);
  cursor: pointer;
  flex-shrink: 0;
  margin-left: auto;
}

.track--dev .update-row__arrow {
  background: #10b981;
  border-color: rgba(16, 185, 129, 0.5);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.2), 0 2px 8px rgba(16, 185, 129, 0.25);
}

.update-row__arrow:disabled {
  cursor: default;
  opacity: 0.38;
}

/* --- indicator glow (trail left of arrow during progress) --- */
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

/* --- firework particles --- */
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

/* --- restart button --- */
.update-row__restart {
  height: 30px;
  padding: 0 14px;
  border: 1px solid #3b82f6;
  border-radius: var(--radius-sm);
  color: #ffffff;
  background: #3b82f6;
  font: 11px/1 var(--mono);
  cursor: pointer;
  flex-shrink: 0;
  margin-left: auto;
  box-shadow: 0 1px 4px rgba(59, 130, 246, 0.25);
}

.track--dev .update-row__restart {
  border-color: #10b981;
  background: #10b981;
  box-shadow: 0 1px 4px rgba(16, 185, 129, 0.25);
}

/* --- detail --- */
.update-row__detail {
  margin: 10px 0 0;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.5;
  min-height: 18px;
}

/* --- release notes --- */
.update-row__notes { margin-top: 10px; }

.update-row__notes-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  background: var(--code-bg);
  font: 10px/1 var(--mono);
  cursor: pointer;
  transition: color 120ms, border-color 120ms;
}

.update-row__notes-toggle:hover {
  color: var(--accent-text);
  border-color: var(--accent-border);
}

.update-row__notes-toggle svg { transition: transform 180ms; }
.update-row__notes-toggle svg.is-open { transform: rotate(180deg); }

.update-row__notes-body {
  margin-top: 8px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: rgba(248, 250, 253, 0.6);
  max-height: 260px;
  overflow-y: auto;
  font-size: 11px;
  line-height: 1.55;
  color: var(--text-muted);
}

.update-row__notes-body :deep(h2),
.update-row__notes-body :deep(h3) {
  margin: 12px 0 4px;
  font-size: 12px;
  color: var(--text-h);
}

.update-row__notes-body :deep(h2):first-child,
.update-row__notes-body :deep(h3):first-child { margin-top: 0; }

.update-row__notes-body :deep(p) { margin: 4px 0; }

.update-row__notes-body :deep(ul),
.update-row__notes-body :deep(ol) {
  margin: 4px 0;
  padding-left: 18px;
}

.update-row__notes-body :deep(li) { margin: 2px 0; }

.update-row__notes-body :deep(code) {
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--code-bg);
  font-size: 10px;
}

.update-row__notes-body :deep(a) { color: #3b82f6; }

.update-row__notes-body :deep(blockquote) {
  margin: 6px 0;
  padding: 4px 10px;
  border-left: 2px solid var(--border-strong);
  color: var(--text-muted);
}

.update-row__notes-body :deep(strong) { color: var(--text-h); }

/* --- running/done states --- */
.is-running .update-row__progress-bar.is-visible { opacity: 1; }

/* running 时进度条斜纹流动 */
.is-running .update-row__progress-bar::after {
  background-image:
    linear-gradient(90deg,
      rgba(59, 130, 246, 0.28),
      rgba(59, 130, 246, 0.18) 60%,
      rgba(255, 255, 255, 0.24) 100%),
    repeating-linear-gradient(45deg, rgba(255, 255, 255, 0.16) 0 6px, transparent 6px 12px);
  background-size: 100% 100%, 200% 100%;
  animation: bar-stripes 1.1s linear infinite;
}

.track--dev.is-running .update-row__progress-bar::after {
  background-image:
    linear-gradient(90deg,
      rgba(16, 185, 129, 0.28),
      rgba(16, 185, 129, 0.18) 60%,
      rgba(255, 255, 255, 0.24) 100%),
    repeating-linear-gradient(45deg, rgba(255, 255, 255, 0.16) 0 6px, transparent 6px 12px);
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

/* done 时进度条绿光脉冲 */
.is-done .update-row__progress-bar.is-visible::after {
  background: linear-gradient(90deg,
    rgba(16, 185, 129, 0.34),
    rgba(16, 185, 129, 0.18) 60%,
    rgba(255, 255, 255, 0.22) 100%);
  animation: done-glow 1.4s ease-out;
}

@keyframes done-glow {
  0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.55); }
  100% { box-shadow: 0 0 0 14px rgba(16, 185, 129, 0); }
}

/* 行 hover 提升 */
.update-row {
  transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
}

.update-row:hover {
  transform: translateY(-1px);
  border-color: var(--accent-border);
  box-shadow: 0 8px 22px rgba(16, 42, 92, 0.08);
}

/* 箭头 hover */
.update-row__arrow:not(:disabled):hover {
  transform: scale(1.12);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 3px 12px rgba(59, 130, 246, 0.4);
}

.track--dev .update-row__arrow:not(:disabled):hover {
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 3px 12px rgba(16, 185, 129, 0.4);
}

/* --- reduced motion --- */
@media (prefers-reduced-motion: reduce) {
  .update-row__arrow,
  .update-row__current-ver,
  .update-row__target-ver,
  .update-row__progress-bar::after,
  .update-row__edge-shine,
  .update-row__restart {
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
