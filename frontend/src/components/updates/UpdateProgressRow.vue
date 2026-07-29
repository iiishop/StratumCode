<script setup>
import { computed, ref } from 'vue'
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
const progressStyle = computed(() => ({ '--progress': `${Math.min(100, Math.max(0, props.progress))}%` }))
const notesExpanded = ref(false)
const parsedNotes = computed(() => props.releaseBody ? parseBlock(props.releaseBody) : '')

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
  if (done) return 'Updated'
  if (running) return 'Updating…'
  return props.available ? 'Update available' : 'Current'
})

const accentTrack = computed(() => props.title.toLowerCase().includes('dev') ? 'dev' : 'stable')
</script>

<template>
  <article
    class="update-row"
    :class="[`track--${accentTrack}`, { 'is-running': running, 'is-done': done, 'is-error': failed, 'is-disabled': disabled }]"
    :style="progressStyle"
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
      <div class="update-row__version">
        <span class="update-row__track" aria-hidden="true">
          <span class="update-row__track-target">{{ targetLabel }}</span>
        </span>
        <span v-if="done" class="update-row__shine" aria-hidden="true"></span>
        <span v-if="done" class="update-row__particles" aria-hidden="true">
          <span v-for="index in 10" :key="index" :style="{ '--i': index }"></span>
        </span>
        <span class="update-row__current">{{ currentLabel }}</span>
        <button
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
      </div>
      <button v-if="done" class="update-row__restart" type="button" @click="emit('restart')">Restart</button>
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

.track--stable::before { background: var(--accent); }
.track--dev::before { background: #10b981; }

.update-row.is-disabled {
  opacity: 0.5;
}

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
  color: var(--accent);
  border-color: var(--accent-border);
  background: var(--accent-bg);
}

.update-row__release-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-bottom: 10px;
}

.update-row__release-name {
  font: 500 12px/1.3 var(--heading);
  color: var(--text-h);
}

.update-row__release-date {
  padding: 1px 6px;
  border-radius: 999px;
  font: 9.5px/1 var(--mono);
  color: var(--text-muted);
  background: var(--code-bg);
  border: 1px solid var(--border);
}

.update-row__behind {
  font: 10px/1 var(--mono);
  color: var(--text-muted);
}

.update-row__stage {
  display: flex;
  align-items: center;
  gap: 10px;
}

.update-row__version {
  position: relative;
  display: flex;
  min-width: 0;
  min-height: 34px;
  flex: 1;
  align-items: center;
  gap: 8px;
  overflow: hidden;
  padding: 0 4px;
}

.update-row__current {
  position: relative;
  z-index: 3;
  font: 12px/1 var(--mono);
  transition: opacity 180ms ease, transform 180ms ease;
  white-space: nowrap;
  color: var(--text-h);
}

.update-row__arrow,
.update-row__restart {
  flex: 0 0 auto;
  cursor: pointer;
}

.update-row__arrow {
  position: relative;
  z-index: 4;
  display: grid;
  width: 27px;
  height: 27px;
  padding: 0;
  place-items: center;
  border: 1px solid var(--accent-border);
  border-radius: 50%;
  color: #ffffff;
  background: var(--accent);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.2);
  transition: left 260ms cubic-bezier(0.16, 1, 0.3, 1), transform 260ms cubic-bezier(0.16, 1, 0.3, 1), opacity 220ms ease;
}

.track--dev .update-row__arrow {
  background: #10b981;
  border-color: rgba(16, 185, 129, 0.5);
}

.update-row__arrow:disabled {
  cursor: default;
  opacity: 0.38;
}

.update-row__track {
  position: absolute;
  top: 50%;
  left: 0;
  z-index: 1;
  width: var(--progress);
  height: 1.1em;
  overflow: hidden;
  border: 1px solid rgba(23, 86, 209, 0.34);
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(255, 255, 255, 0.28), transparent 28%), var(--accent);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12);
  opacity: 0;
  pointer-events: none;
  transform: translateY(-50%);
  transition: width 260ms cubic-bezier(0.16, 1, 0.3, 1);
}

.track--dev .update-row__track {
  background: linear-gradient(90deg, rgba(255, 255, 255, 0.28), transparent 28%), #10b981;
  border-color: rgba(16, 185, 129, 0.34);
}

.update-row__track-target {
  display: flex;
  height: 100%;
  align-items: center;
  padding-left: 40px;
  color: #ffffff;
  font: 12px/1 var(--mono);
  white-space: nowrap;
}

/* --- running / done states (kept from original) --- */

.update-row.is-running .update-row__version {
  padding-left: 4px;
}

.update-row.is-running .update-row__current {
  opacity: 0.8;
  transform: scale(0.96);
}

.update-row.is-running .update-row__track,
.update-row.is-done .update-row__track {
  opacity: 1;
}

.update-row.is-running .update-row__arrow {
  position: absolute;
  left: clamp(0px, calc(var(--progress) - 14px), calc(100% - 27px));
  transform: rotate(90deg) scale(0.86);
}

.update-row.is-running .update-row__arrow::before {
  content: "";
  position: absolute;
  right: 24px;
  width: 48px;
  height: 12px;
  border-radius: 999px;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.58));
  filter: blur(5px);
}

.update-row.is-done .update-row__current {
  display: none;
}

.update-row.is-done .update-row__track-target {
  animation: update-label-flash 520ms ease both;
}

.update-row.is-done .update-row__track {
  width: 100%;
  background: var(--accent);
}

.track--dev.is-done .update-row__track {
  background: #10b981;
}

.update-row.is-done .update-row__arrow {
  position: absolute;
  left: calc(100% - 27px);
  opacity: 1;
  animation: update-knob-finish 300ms cubic-bezier(0.55, 0, 1, 0.45) both;
}

/* --- shine / particles --- */

.update-row__shine {
  position: absolute;
  top: 50%;
  left: 0;
  z-index: 3;
  width: 100%;
  height: 1.1em;
  border-radius: 999px;
  pointer-events: none;
  transform: translateY(-50%);
}

.update-row__shine::before {
  content: "";
  position: absolute;
  width: 26px;
  height: 2px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.96);
  animation: update-edge-shine 900ms ease both;
}

.update-row__particles {
  position: absolute;
  left: calc(100% - 16px);
  top: 50%;
  z-index: 5;
}

.update-row__particles span {
  position: absolute;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
  transform: rotate(calc(var(--i) * 36deg)) translateX(0);
  animation: update-particle 480ms ease-out both;
}

/* --- restart --- */

.update-row__restart {
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  color: #ffffff;
  background: var(--accent);
  font: 11px/1 var(--mono);
}

.track--dev .update-row__restart {
  border-color: #10b981;
  background: #10b981;
}

/* --- detail --- */

.update-row__detail {
  margin: 10px 0 0;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.5;
  min-height: 18px;
}

.update-row.is-error {
  border-color: var(--err-border);
}

.update-row.is-error .update-row__detail {
  color: var(--err);
}

/* --- release notes --- */

.update-row__notes {
  margin-top: 10px;
}

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

.update-row__notes-toggle svg {
  transition: transform 180ms;
}

.update-row__notes-toggle svg.is-open {
  transform: rotate(180deg);
}

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
.update-row__notes-body :deep(h3):first-child {
  margin-top: 0;
}

.update-row__notes-body :deep(p) {
  margin: 4px 0;
}

.update-row__notes-body :deep(ul),
.update-row__notes-body :deep(ol) {
  margin: 4px 0;
  padding-left: 18px;
}

.update-row__notes-body :deep(li) {
  margin: 2px 0;
}

.update-row__notes-body :deep(code) {
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--code-bg);
  font-size: 10px;
}

.update-row__notes-body :deep(a) {
  color: var(--accent);
}

.update-row__notes-body :deep(blockquote) {
  margin: 6px 0;
  padding: 4px 10px;
  border-left: 2px solid var(--border-strong);
  color: var(--text-muted);
}

.update-row__notes-body :deep(strong) {
  color: var(--text-h);
}

/* --- keyframes --- */

@keyframes update-label-flash {
  0%, 100% { opacity: 1; }
  45% { opacity: 0.42; }
}

@keyframes update-knob-finish {
  0% { opacity: 0.72; transform: rotate(90deg) scale(0.86); }
  34% { opacity: 0.84; transform: rotate(450deg) scale(1.12); }
  100% { opacity: 0; transform: rotate(1890deg) scale(2); }
}

@keyframes update-edge-shine {
  0% { left: 0; top: 0; transform: rotate(0deg); }
  25% { left: calc(100% - 26px); top: 0; transform: rotate(0deg); }
  50% { left: calc(100% - 26px); top: calc(100% - 2px); transform: rotate(90deg); }
  75% { left: 0; top: calc(100% - 2px); transform: rotate(180deg); }
  100% { left: 0; top: 0; transform: rotate(270deg); }
}

@keyframes update-particle {
  0% { opacity: 1; transform: rotate(calc(var(--i) * 36deg)) translateX(0) scale(1); }
  100% { opacity: 0; transform: rotate(calc(var(--i) * 36deg)) translateX(34px) scale(0.3); }
}

@media (prefers-reduced-motion: reduce) {
  .update-row__arrow,
  .update-row__current,
  .update-row__track,
  .update-row__shine::before,
  .update-row__particles span {
    animation: none;
    transition: none;
  }
}

@media (max-width: 620px) {
  .update-row__stage {
    align-items: stretch;
    flex-direction: column;
  }

  .update-row__restart {
    width: 100%;
  }
}
</style>
