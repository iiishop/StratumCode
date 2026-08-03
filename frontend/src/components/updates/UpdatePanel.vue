<script setup>
import { computed, reactive, ref } from 'vue'
import UpdateProgressRow from './UpdateProgressRow.vue'

const props = defineProps({
  status: { type: Object, required: true },
  applyUpdate: { type: Function, required: true },
  restartApp: { type: Function, required: true },
  checking: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'refresh'])
const activeChannel = ref('')
const rows = reactive({
  stable: { state: 'idle', progress: 0, message: '' },
  dev: { state: 'idle', progress: 0, message: '' },
})

const hasUpdate = computed(() => props.status.stable_available || props.status.dev_available)
const overviewHint = computed(() => {
  if (props.status.stable_available && props.status.dev_available) {
    return `Stable v${props.status.latest_version} · dev +${props.status.commits_behind}`
  }
  if (props.status.stable_available) return `Stable v${props.status.latest_version} available`
  if (props.status.dev_available) return `Dev +${props.status.commits_behind} commits`
  return 'Up to date'
})

const release = computed(() => props.status.latest_release || {})
const releaseNotes = computed(() => {
  if (!props.status.stable_available) return 'Your installation is current. No update needed.'
  return ''
})
const devTarget = computed(() => props.status.remote_short_commit || 'origin/main')
const devDetail = computed(() => {
  if (!props.status.dev_available) return 'Local commit is current with origin/main.'
  return ''
})

const diagnostics = computed(() => props.status.diagnostics || [])
const showDiagnostics = computed(() => diagnostics.value.length > 0)

async function start(channel) {
  activeChannel.value = channel
  rows[channel].state = 'running'
  rows[channel].progress = 1
  rows[channel].message = 'Starting update.'
  try {
    await props.applyUpdate(channel, (event) => {
      rows[channel].message = event.message || rows[channel].message
      rows[channel].progress = Math.max(rows[channel].progress, Number(event.progress) || rows[channel].progress)
      if (event.op === 'done') rows[channel].state = 'done'
      if (event.op === 'error') rows[channel].state = 'error'
    })
    if (rows[channel].state === 'running') rows[channel].state = 'done'
    rows[channel].progress = 100
    emit('refresh')
  } catch (reason) {
    rows[channel].state = 'error'
    rows[channel].message = reason.message || 'Update failed.'
  }
}

async function restart() {
  await props.restartApp()
}
</script>

<template>
  <Teleport to="body">
    <Transition name="update-panel">
      <div class="update-panel__wrap">
        <div class="update-panel__scrim" @click="emit('close')"></div>
        <section class="update-panel" role="dialog" aria-modal="true" aria-label="Updates">
          <header class="update-panel__head">
            <div class="update-panel__head-title">
              <span class="update-panel__head-mark">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M4 17l6-6-6-6"/><path d="M12 19h8"/>
                </svg>
              </span>
              <div>
                <h2>Updates</h2>
                <p>{{ status.repo || 'iiishop/StratumCode' }} — Stable follows GitHub releases, dev tracks origin/main</p>
              </div>
            </div>
            <div class="update-panel__head-actions">
              <button class="update-panel__check" type="button" :disabled="checking" @click="emit('refresh', true)">
                <span v-if="checking" class="update-panel__check-spinner"></span>
                <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/>
                </svg>
                {{ checking ? 'Checking' : 'Check now' }}
              </button>
              <button class="update-panel__close" type="button" aria-label="Close updates" @click="emit('close')">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
          </header>

          <div class="update-panel__overview" :class="{ 'has-update': hasUpdate }">
            <div class="update-panel__overview-left">
              <span class="update-panel__overview-label">Current version</span>
              <div class="update-panel__overview-main">
                <span class="update-panel__overview-ver">v{{ status.current_version }}</span>
                <span class="update-panel__overview-commit">{{ status.short_commit || 'unknown' }}</span>
              </div>
            </div>
            <div class="update-panel__overview-right">
              <span class="update-panel__overview-dot" :class="{ 'is-update': hasUpdate }"></span>
              <span class="update-panel__overview-hint" :class="{ 'is-update': hasUpdate }">{{ overviewHint }}</span>
            </div>
          </div>

          <div class="update-panel__body">
            <div v-if="showDiagnostics" class="update-panel__warnings">
              <div v-for="(d, i) in diagnostics" :key="i" class="update-panel__warning">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                <div>
                  <p class="update-panel__warning-msg">{{ d.message }}</p>
                  <p class="update-panel__warning-hint">{{ d.hint }}</p>
                </div>
              </div>
            </div>
        <UpdateProgressRow
          title="Stable release"
          :current-label="`v${status.current_version}`"
          :target-label="status.latest_version ? `v${status.latest_version}` : 'No release'"
          :detail="releaseNotes"
          :available="status.stable_available"
          :disabled="Boolean(activeChannel && activeChannel !== 'stable')"
          :progress="rows.stable.progress"
          :state="rows.stable.state"
          :message="rows.stable.message"
          :release-name="release.name"
          :release-body="release.body"
          :release-url="release.html_url"
          :release-date="release.published_at"
          @start="start('stable')"
          @restart="restart"
        />
        <UpdateProgressRow
          title="Dev update"
          :current-label="status.short_commit || 'unknown'"
          :target-label="devTarget"
          :detail="devDetail"
          :available="status.dev_available"
          :disabled="Boolean(activeChannel && activeChannel !== 'dev')"
          :progress="rows.dev.progress"
          :state="rows.dev.state"
          :message="rows.dev.message"
          :commits-behind="status.commits_behind"
          branch-name="main"
          @start="start('dev')"
          @restart="restart"
        />
      </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.update-panel__wrap {
  position: fixed;
  inset: 0;
  z-index: 40;
  pointer-events: none;
}

.update-panel__scrim {
  position: absolute;
  inset: 0;
  pointer-events: auto;
  background: rgba(16, 42, 92, 0.18);
  backdrop-filter: blur(2px);
  transition: opacity 0.22s ease;
}

.update-panel {
  position: absolute;
  right: 18px;
  bottom: 50px;
  z-index: 41;
  pointer-events: auto;
  width: min(640px, calc(100vw - 28px));
  max-height: min(720px, calc(100dvh - 72px));
  overflow: hidden;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  background:
    radial-gradient(120% 90% at 100% 0%, rgba(59, 130, 246, 0.07), transparent 55%),
    linear-gradient(180deg, #ffffff, #f8fafd);
  box-shadow:
    0 12px 44px rgba(16, 42, 92, 0.16),
    0 2px 8px rgba(16, 42, 92, 0.08),
    0 0 0 1px rgba(16, 42, 92, 0.04);
}

/* 顶部 accent 光带 */
.update-panel::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 2px;
  background: linear-gradient(90deg, #3b82f6, #10b981 55%, rgba(16, 185, 129, 0.1));
  opacity: 0.85;
}

/* 入场/退场动画 */
.update-panel-enter-active,
.update-panel-leave-active {
  transition: opacity 0.22s ease;
}
.update-panel-enter-active .update-panel,
.update-panel-leave-active .update-panel {
  transition: transform 0.28s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.22s ease;
}
.update-panel-enter-from,
.update-panel-leave-to {
  opacity: 0;
}
.update-panel-enter-from .update-panel {
  transform: translateY(16px) scale(0.97);
  opacity: 0;
}
.update-panel-leave-to .update-panel {
  transform: translateY(10px) scale(0.98);
  opacity: 0;
}

.update-panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 18px 14px;
  border-bottom: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.6);
}

.update-panel__head-title {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
}

.update-panel__head-mark {
  display: grid;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  place-items: center;
  margin-top: 2px;
  border: 1px solid rgba(59, 130, 246, 0.28);
  border-radius: 10px;
  color: #ffffff;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  box-shadow: 0 3px 10px rgba(59, 130, 246, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.25);
}

.update-panel__head h2 {
  margin: 0;
  color: var(--text-h);
  font: 570 20px/1.1 var(--heading);
  letter-spacing: -0.01em;
}

.update-panel__head p {
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 10.5px;
  line-height: 1.4;
}

.update-panel__head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.update-panel__check {
  display: inline-flex;
  height: 30px;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-h);
  background: linear-gradient(180deg, #ffffff, var(--code-bg));
  box-shadow: 0 1px 2px rgba(16, 42, 92, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.8);
  font: 600 11px/1 var(--sans);
  cursor: pointer;
  transition: border-color var(--transition-fast), background var(--transition-fast), color var(--transition-fast), transform 120ms ease, box-shadow var(--transition-fast);
}

.update-panel__check:hover {
  border-color: var(--accent-border);
  color: var(--accent);
  background: linear-gradient(180deg, #ffffff, var(--accent-bg));
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(23, 86, 209, 0.16), inset 0 1px 0 rgba(255, 255, 255, 0.8);
}

.update-panel__check:active {
  transform: translateY(0) scale(0.97);
}

.update-panel__check svg {
  flex-shrink: 0;
  transition: transform 300ms ease;
}

.update-panel__check-spinner {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
  border: 1.5px solid var(--accent-border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: check-spin 0.7s linear infinite;
}

@keyframes check-spin {
  to { transform: rotate(360deg); }
}

.update-panel__check:disabled {
  cursor: default;
  opacity: 0.75;
}

.update-panel__check:active svg {
  transform: rotate(180deg);
}

.update-panel__close {
  display: grid;
  width: 30px;
  height: 30px;
  padding: 0;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  background: transparent;
  cursor: pointer;
  transition: color var(--transition-fast), background var(--transition-fast), transform 120ms ease;
}

.update-panel__close:hover {
  color: var(--text-h);
  background: var(--code-bg-hover);
}

.update-panel__close:active {
  transform: scale(0.92);
}

.update-panel__overview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin: 14px 16px 0;
  padding: 12px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background:
    radial-gradient(120% 120% at 0% 0%, rgba(16, 185, 129, 0.06), transparent 55%),
    linear-gradient(180deg, #ffffff, #f6faf8);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9), 0 1px 3px rgba(16, 42, 92, 0.05);
}

.update-panel__overview.has-update {
  background:
    radial-gradient(120% 120% at 0% 0%, rgba(245, 200, 66, 0.1), transparent 55%),
    linear-gradient(180deg, #ffffff, #fbfaf4);
}

.update-panel__overview-left {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.update-panel__overview-label {
  font: 600 9px/1 var(--mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.update-panel__overview-main {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.update-panel__overview-ver {
  font: 600 22px/1 var(--heading);
  letter-spacing: -0.02em;
  color: var(--text-h);
}

.update-panel__overview-commit {
  overflow: hidden;
  max-width: 140px;
  padding: 2px 8px;
  border-radius: 999px;
  font: 9.5px/1 var(--mono);
  color: var(--text-muted);
  background: var(--code-bg);
  border: 1px solid var(--border);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.update-panel__overview-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.update-panel__overview-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.15);
}

.update-panel__overview-dot.is-update {
  background: var(--yellow);
  box-shadow: 0 0 0 3px rgba(245, 200, 66, 0.2);
  animation: overview-pulse 2.2s ease-in-out infinite;
}

@keyframes overview-pulse {
  0%, 100% { box-shadow: 0 0 0 3px rgba(245, 200, 66, 0.2); }
  50% { box-shadow: 0 0 0 7px rgba(245, 200, 66, 0.32); }
}

.update-panel__overview-hint {
  font: 600 11px/1.3 var(--sans);
  color: #047857;
}

.update-panel__overview-hint.is-update {
  color: #8a6d14;
}

.update-panel__body {
  display: grid;
  gap: 12px;
  padding: 16px;
  overflow: auto;
}

.update-panel__warnings {
  display: grid;
  gap: 6px;
}

.update-panel__warning {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid rgba(245, 200, 66, 0.5);
  border-radius: var(--radius-sm);
  background: var(--yellow-bg);
  color: #8a6d14;
}

.update-panel__warning svg {
  flex-shrink: 0;
  margin-top: 1px;
}

.update-panel__warning-msg {
  margin: 0;
  font: 500 11px/1.4 var(--mono);
}

.update-panel__warning-hint {
  margin: 3px 0 0;
  font-size: 10.5px;
  line-height: 1.35;
  opacity: 0.78;
}

@media (max-width: 640px) {
  .update-panel {
    right: 10px;
    bottom: 44px;
    width: calc(100vw - 20px);
  }
}
</style>
