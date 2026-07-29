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

const releaseNotes = computed(() => {
  const release = props.status.latest_release || {}
  if (!props.status.stable_available) return 'Latest stable release is already installed.'
  const published = release.published_at ? ` Published ${release.published_at.slice(0, 10)}.` : ''
  return `Release ${props.status.latest_version} is available.${published} Open GitHub for full notes.`
})
const devTarget = computed(() => props.status.remote_short_commit || 'origin/main')

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
          <p>Stable follows GitHub releases. Dev follows origin/main.</p>
        </div>
        <button class="update-panel__close" type="button" aria-label="Close updates" @click="emit('close')">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </header>

      <div class="update-panel__body">
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
          @start="start('stable')"
          @restart="restart"
        />
        <UpdateProgressRow
          title="Dev update"
          :current-label="status.short_commit || 'unknown'"
          :target-label="devTarget"
          :detail="status.dev_available ? `${status.commits_behind} commits available from main.` : 'Local commit is current with main.'"
          :available="status.dev_available"
          :disabled="Boolean(activeChannel && activeChannel !== 'dev')"
          :progress="rows.dev.progress"
          :state="rows.dev.state"
          :message="rows.dev.message"
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
  background: rgba(16, 42, 92, 0.16);
}

.update-panel {
  position: fixed;
  right: 18px;
  bottom: 50px;
  z-index: 41;
  width: min(620px, calc(100vw - 28px));
  max-height: min(720px, calc(100dvh - 72px));
  overflow: hidden;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.98);
  box-shadow: var(--shadow-md);
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
  margin: 5px 0 0;
  color: var(--text-muted);
  font-size: 11px;
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
  gap: 14px;
  padding: 16px;
  overflow: auto;
}

@media (max-width: 620px) {
  .update-panel {
    right: 10px;
    bottom: 44px;
    width: calc(100vw - 20px);
  }
}
</style>
