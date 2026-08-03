<script setup>
import { computed, reactive, ref } from 'vue'
import UpdateProgressRow from './UpdateProgressRow.vue'

const props = defineProps({
  status: { type: Object, required: true },
  applyUpdate: { type: Function, required: true },
  restartApp: { type: Function, required: true },
})

const emit = defineEmits(['close', 'refresh'])
const activeChannel = ref('')
const rows = reactive({
  stable: { state: 'idle', progress: 0, message: '' },
  dev: { state: 'idle', progress: 0, message: '' },
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
    <div class="update-panel__scrim" @click="emit('close')"></div>
    <section class="update-panel" role="dialog" aria-modal="true" aria-label="Updates">
      <header class="update-panel__head">
        <div>
          <h2>Updates</h2>
          <p>{{ status.repo || 'iiishop/StratumCode' }} — Stable follows GitHub releases, dev tracks origin/main</p>
        </div>
        <div class="update-panel__head-actions">
          <button class="update-panel__check" type="button" @click="emit('refresh', true)">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/>
            </svg>
            Check now
          </button>
          <button class="update-panel__close" type="button" aria-label="Close updates" @click="emit('close')">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      </header>

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
  </Teleport>
</template>

<style scoped>
.update-panel__scrim {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: rgba(16, 42, 92, 0.18);
  backdrop-filter: blur(2px);
}

.update-panel {
  position: fixed;
  right: 18px;
  bottom: 50px;
  z-index: 41;
  width: min(640px, calc(100vw - 28px));
  max-height: min(720px, calc(100dvh - 72px));
  overflow: hidden;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 4px 32px rgba(16, 42, 92, 0.12), 0 0 0 1px rgba(16, 42, 92, 0.04);
}

.update-panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 18px 14px;
  border-bottom: 1px solid var(--border);
}

.update-panel__head h2 {
  margin: 0;
  color: var(--text-h);
  font: 570 20px/1.1 var(--heading);
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
  background: transparent;
  font: 600 11px/1 var(--sans);
  cursor: pointer;
  transition: border-color var(--transition-fast), background var(--transition-fast), color var(--transition-fast);
}

.update-panel__check:hover {
  border-color: var(--accent-border);
  color: var(--accent);
  background: var(--accent-bg);
}

.update-panel__check svg {
  flex-shrink: 0;
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
}

.update-panel__close:hover {
  color: var(--text-h);
  background: var(--code-bg-hover);
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
