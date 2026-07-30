<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import HighlightedText from './HighlightedText.vue'

const props = defineProps({
  kind: { type: String, required: true },
  label: { type: String, required: true },
  detail: { type: String, default: '' },
  status: { type: String, default: '' },
  symbol: { type: String, default: '·' },
  state: { type: String, default: '' },
  open: { type: Boolean, default: false },
  collapsible: { type: Boolean, default: false },
  createdAt: { type: Number, default: undefined },
})

const emit = defineEmits(['toggle'])
const expanded = computed(() => props.collapsible ? props.open : true)

// ── duration ───────────────────────────────

const elapsedSeconds = ref(0)
let _durationTimer = null

function formatElapsed(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(s / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  const seconds = s % 60
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
}

const formattedDuration = computed(() => {
  if (props.status === 'running') {
    return formatElapsed(elapsedSeconds.value)
  }
  if (props.createdAt == null) return ''
  const now = Date.now()
  const totalSeconds = (now - props.createdAt) / 1000
  return formatElapsed(totalSeconds)
})

function _startTimer() {
  _stopTimer()
  if (props.status === 'running') {
    elapsedSeconds.value = props.createdAt != null
      ? Math.max(0, Math.floor((Date.now() - props.createdAt) / 1000))
      : 0
    _durationTimer = setInterval(() => {
      elapsedSeconds.value++
    }, 1000)
  }
}

function _stopTimer() {
  if (_durationTimer != null) {
    clearInterval(_durationTimer)
    _durationTimer = null
  }
}

onMounted(() => {
  _startTimer()
})

onUnmounted(() => {
  _stopTimer()
})

watch(() => props.status, (newStatus, oldStatus) => {
  if (oldStatus === 'running' && newStatus !== 'running') {
    _stopTimer()
  }
})
</script>

<template>
  <article class="event-frame" :class="[`event-frame--${kind}`, state && `event-frame--${state}`, status && `event-frame--${status}`]">
    <div class="event-frame__rail">
      <span class="event-frame__node">{{ symbol }}</span>
    </div>
    <div class="event-frame__surface">
      <button
        class="event-frame__head"
        :class="{ 'is-static': !collapsible }"
        type="button"
        :disabled="!collapsible"
        @click="collapsible && emit('toggle')"
      >
        <span class="event-frame__titles">
          <span class="event-frame__label">{{ label }}</span>
          <small v-if="detail"><HighlightedText :text="detail" /></small>
        </span>
        <span v-if="status" class="event-frame__status" :class="{ 'is-running': status === 'running' }">{{ status }}</span>
        <span v-if="createdAt != null || status === 'running'" class="event-frame__duration">{{ formattedDuration }}</span>
        <span v-if="collapsible" class="event-frame__chevron" :class="{ 'is-open': open }">⌄</span>
      </button>
      <Transition name="event-frame-expand">
        <div v-show="expanded" class="event-frame__expand">
          <div class="event-frame__clip">
            <div class="event-frame__body"><slot /></div>
          </div>
        </div>
      </Transition>
    </div>
  </article>
</template>

<style scoped>
.event-frame {
  --event: #1756d1;
  --event-dim: color-mix(in srgb, var(--event) 60%, #7c8ba0);
  position: relative;
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  min-width: 0;
}
.event-frame--thinking    { --event: #c48b00; }
.event-frame--code-nav     { --event: #6658c7; }
.event-frame--terminal     { --event: #0f7d65; }
.event-frame--subagent     { --event: #6658c7; }
.event-frame--diff         { --event: #e56b2f; }
.event-frame--patch        { --event: #e56b2f; }
.event-frame--output       { --event: #1756d1; }
.event-frame--task-analysis { --event: #6658c7; }
.event-frame--stage        { --event: #1756d1; }
.event-frame--skill        { --event: #0f7d65; }
.event-frame--state-transition { --event: #7c8ba0; }
.event-frame--hypothesis   { --event: #1756d1; }
.event-frame--evidence     { --event: #0f7d65; }
.event-frame--evidence.event-frame--oppose { --event: #c48b00; }
.event-frame--relation     { --event: #6658c7; }
.event-frame--verdict      { --event: #1756d1; }
.event-frame--verdict.event-frame--supported   { --event: #0f7d65; }
.event-frame--verdict.event-frame--refuted     { --event: #c44747; }
.event-frame--verdict.event-frame--inconclusive { --event: #c48b00; }
.event-frame--step-result  { --event: #6658c7; }
.event-frame--safety-stop  { --event: #c44747; }
.event-frame--user-question { --event: #c48b00; }
.event-frame--accepted     { --event: #00a878; }
.event-frame--rejected     { --event: #e11d74; }
.event-frame--error        { --event: #c44747; }
.event-frame--no_progress  { --event: #9a6a00; }
.event-frame--usage        { --event: #7c8ba0; }

/* ── left rail ────────────────────────────── */

.event-frame__rail {
  position: relative;
  display: flex;
  justify-content: center;
  padding-top: 2px;
}

.event-frame__rail::after {
  position: absolute;
  top: 32px;
  bottom: -12px;
  left: 50%;
  width: 2px;
  content: "";
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--event) 24%, #d8e3f4),
    color-mix(in srgb, var(--event) 6%, rgba(216, 227, 244, .32))
  );
  border-radius: 1px;
}
.event-frame:last-child .event-frame__rail::after { display: none; }

/* ── node ─────────────────────────────────── */

.event-frame__node {
  position: relative;
  z-index: 1;
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 2px solid color-mix(in srgb, var(--event) 22%, #e0e8f5);
  border-radius: 50%;
  color: var(--event);
  background: linear-gradient(135deg, color-mix(in srgb, var(--event) 6%, #ffffff), #ffffff 70%);
  box-shadow:
    0 0 0 4px color-mix(in srgb, var(--event) 4%, #f0f4fa),
    0 1px 3px rgba(22, 53, 98, 0.06);
  font: 800 10px/1 var(--mono, monospace);
  transition:
    box-shadow 240ms ease,
    border-color 240ms ease,
    transform 180ms cubic-bezier(0.28, 1.4, 0.55, 1);
}

/* solid nodes: tool / thinking / terminal / code-nav / subagent */
.event-frame--thinking .event-frame__node,
.event-frame--code-nav .event-frame__node,
.event-frame--terminal .event-frame__node,
.event-frame--tool .event-frame__node,
.event-frame--subagent .event-frame__node {
  color: #fff;
  background: linear-gradient(145deg, color-mix(in srgb, #fff 26%, var(--event)), var(--event));
  border-color: var(--event);
  box-shadow:
    0 0 0 4px color-mix(in srgb, var(--event) 8%, transparent),
    0 2px 8px color-mix(in srgb, var(--event) 28%, transparent);
}

/* running pulse */
.event-frame:has(.event-frame__status.is-running) .event-frame__node {
  animation: node-pulse 1.55s ease-in-out infinite;
}

/* ── surface card ─────────────────────────── */

.event-frame__surface {
  position: relative;
  min-width: 0;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--event) 10%, #dce4f2);
  border-radius: 10px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--event) 2.5%, #ffffff), #ffffff 70%);
  box-shadow:
    0 1px 4px rgba(22, 53, 98, 0.025),
    0 2px 8px rgba(22, 53, 98, 0.018);
  animation: event-frame-in 240ms cubic-bezier(.16, 1, .3, 1) both;
  transition:
    border-color 220ms ease,
    box-shadow 220ms ease,
    transform 220ms cubic-bezier(.16, 1, .3, 1);
}

.event-frame__surface::before {
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  content: "";
  background: linear-gradient(
    180deg,
    var(--event),
    color-mix(in srgb, var(--event) 30%, transparent)
  );
  border-radius: 0 2px 2px 0;
  opacity: .56;
}

/* accepted / rejected state surfaces */
.event-frame--accepted .event-frame__surface {
  border-color: color-mix(in srgb, var(--event) 34%, #a8dcc8);
  background: linear-gradient(180deg, color-mix(in srgb, var(--event) 4%, #fafffc), #fafffc);
}
.event-frame--rejected .event-frame__surface {
  border-color: color-mix(in srgb, var(--event) 34%, #e8c0d0);
  background: linear-gradient(180deg, color-mix(in srgb, var(--event) 3%, #fffafa), #fffafa);
}

/* ── head bar ─────────────────────────────── */

.event-frame__head {
  display: flex;
  width: 100%;
  min-height: 42px;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  border: 0;
  color: var(--text-h, #102a5c);
  background: transparent;
  text-align: left;
  cursor: pointer;
  position: relative;
  z-index: 1;
  transition: background 160ms ease;
}
.event-frame__head.is-static { cursor: default; }
.event-frame__head:disabled { opacity: 1; }
.event-frame__head:not(.is-static):hover {
  background: color-mix(in srgb, var(--event) 3.5%, transparent);
}

/* ── label badge ──────────────────────────── */

.event-frame__titles {
  display: flex;
  min-width: 0;
  flex: 1 1 auto;
  align-items: center;
  gap: 8px;
}

.event-frame__label {
  flex: 0 0 auto;
  overflow: hidden;
  max-width: 180px;
  padding: 3px 8px;
  border-radius: 5px;
  color: var(--event);
  background: color-mix(in srgb, var(--event) 7%, transparent);
  font: 760 9.5px/1.3 var(--mono, monospace);
  letter-spacing: .05em;
  text-overflow: ellipsis;
  text-transform: uppercase;
  white-space: nowrap;
}

.event-frame__titles small {
  display: flex;
  min-height: 18px;
  align-items: center;
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--text-muted, #71809c);
  font-size: var(--font-caption, 11px);
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── status badge ─────────────────────────── */

.event-frame__status {
  flex: 0 0 auto;
  margin-left: auto;
  padding: 3px 8px;
  border-radius: 5px;
  color: var(--event);
  background: color-mix(in srgb, var(--event) 7%, transparent);
  font: 700 8.5px/1.3 var(--mono, monospace);
  letter-spacing: .05em;
  text-transform: uppercase;
  white-space: nowrap;
  transition:
    background 160ms ease,
    box-shadow 160ms ease;
}

.event-frame__status.is-running {
  color: #5c4200;
  background: rgba(245, 200, 66, .2);
  animation: status-glow 1.8s ease-in-out infinite;
}

.event-frame--no_progress .event-frame__status {
  color: #6e4c00;
  background: rgba(245, 200, 66, .16);
}

/* ── chevron ──────────────────────────────── */

.event-frame__chevron {
  flex-shrink: 0;
  color: var(--text-muted, #7c8ba0);
  font-size: 11px;
  transition: transform .26s cubic-bezier(.22, 1, .36, 1);
}
.event-frame__chevron.is-open { transform: rotate(180deg); }

/* ── expand body ──────────────────────────── */

.event-frame__expand { position: relative; z-index: 1; }
.event-frame__clip { min-height: 0; overflow: hidden; }

.event-frame__body {
  padding: 1px 14px 13px;
  min-width: 0;
  overflow-wrap: anywhere;
}

/* ── keyframes ────────────────────────────── */

@keyframes event-frame-in {
  from {
    opacity: 0;
    transform: translateY(6px) scale(.99);
  }
}

@keyframes node-pulse {
  50% {
    box-shadow:
      0 0 0 8px color-mix(in srgb, var(--event) 10%, transparent),
      0 0 20px color-mix(in srgb, var(--event) 22%, transparent);
  }
}

@keyframes status-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 200, 66, 0); }
  50% { box-shadow: 0 0 0 5px rgba(245, 200, 66, .14); }
}

/* ── collapse transition ──────────────────── */

.event-frame-expand-enter-active,
.event-frame-expand-leave-active {
  max-height: min(70vh, 720px);
  overflow: hidden;
  transition:
    max-height 240ms cubic-bezier(.16, 1, .3, 1),
    opacity 170ms ease,
    transform 230ms cubic-bezier(.16, 1, .3, 1);
  will-change: max-height, opacity, transform;
}

.event-frame-expand-enter-from,
.event-frame-expand-leave-to {
  max-height: 0;
  opacity: 0;
  transform: translateY(-4px);
}

.event-frame-expand-enter-to,
.event-frame-expand-leave-from {
  max-height: min(70vh, 720px);
  opacity: 1;
  transform: translateY(0);
}

@media (prefers-reduced-motion: reduce) {
  .event-frame__surface,
  .event-frame__node,
  .event-frame__chevron,
  .event-frame__expand,
  .event-frame__body,
  .event-frame__label,
  .event-frame__status { transition-duration: .01ms !important; }
  .event-frame__surface { animation: none; }
  .event-frame__status.is-running { animation: none; }
  .event-frame:has(.event-frame__status.is-running) .event-frame__node { animation: none; }
}
</style>
