<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import UpdatePanel from './UpdatePanel.vue'
import { useUpdates } from '../../composables/useUpdates'

const { status, loading, error, refresh, apply, restart } = useUpdates()
const open = ref(false)
let timer = 0

const releaseSuffix = computed(() => status.value.stable_available ? `(${status.value.latest_version})` : '')
const devSuffix = computed(() => status.value.dev_available ? `(+${status.value.commits_behind})` : '')
const commitLabel = computed(() => status.value.short_commit || 'unknown')
const hasUpdate = computed(() => status.value.stable_available || status.value.dev_available)

onMounted(() => {
  refresh()
  timer = window.setInterval(refresh, 60_000)
})

onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <div class="update-status">
    <button class="update-status__button" :class="{ 'has-update': hasUpdate }" type="button" @click="open = true">
      <span class="update-status__dot" :class="{ 'is-update': hasUpdate, 'is-checking': loading }"></span>
      <span class="update-status__version">
        v{{ status.current_version }}<span v-if="releaseSuffix" class="update-status__new">{{ releaseSuffix }}</span>
      </span>
      <span class="update-status__commit">
        {{ commitLabel }}<span v-if="devSuffix" class="update-status__new">{{ devSuffix }}</span>
      </span>
      <span v-if="loading" class="update-status__loading">
        <span class="update-status__spinner"></span>checking
      </span>
    </button>
    <span v-if="error" class="update-status__error">{{ error }}</span>
    <UpdatePanel
      v-if="open"
      :status="status"
      :apply-update="apply"
      :restart-app="restart"
      @close="open = false"
      @refresh="refresh"
    />
  </div>
</template>

<style scoped>
.update-status {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.update-status__button {
  display: inline-flex;
  max-width: 100%;
  height: 25px;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-muted);
  background: var(--code-bg);
  font: 10px/1 var(--mono);
  cursor: pointer;
  transition: border-color var(--transition-fast), background var(--transition-fast), color var(--transition-fast), transform 140ms ease, box-shadow var(--transition-fast);
}

.update-status__button:hover {
  border-color: var(--accent-border);
  color: var(--text-h);
  background: var(--accent-bg);
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(23, 86, 209, 0.14);
}

.update-status__button:active {
  transform: translateY(0) scale(0.96);
}

.update-status__button.has-update {
  border-color: rgba(245, 200, 66, 0.95);
  color: #ffffff;
  background:
    linear-gradient(135deg, rgba(245, 200, 66, 0.26), transparent 46%),
    var(--text-h);
  box-shadow: 0 0 0 3px rgba(245, 200, 66, 0.16);
  animation: update-pulse 2.6s ease-in-out infinite;
}

@keyframes update-pulse {
  0%, 100% {
    box-shadow: 0 0 0 3px rgba(245, 200, 66, 0.16);
  }
  50% {
    box-shadow: 0 0 0 7px rgba(245, 200, 66, 0.3);
  }
}

.update-status__button.has-update:hover {
  border-color: var(--yellow);
  color: #ffffff;
  background:
    linear-gradient(135deg, rgba(245, 200, 66, 0.34), transparent 46%),
    #0d347e;
}

.update-status__dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.18);
}

.update-status__dot.is-update {
  background: var(--yellow);
  box-shadow: 0 0 0 2px rgba(245, 200, 66, 0.22);
  animation: status-pulse 2.4s ease-in-out infinite;
}

.update-status__dot.is-checking {
  background: var(--border-strong);
  box-shadow: none;
}

@keyframes status-pulse {
  0%, 100% { box-shadow: 0 0 0 2px rgba(245, 200, 66, 0.22); }
  50% { box-shadow: 0 0 0 5px rgba(245, 200, 66, 0.34); }
}

.update-status__version,
.update-status__commit {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.update-status__new {
  margin-left: 4px;
  padding: 2px 5px;
  border-radius: 999px;
  color: #0d347e;
  background: var(--yellow);
  font-weight: 700;
}

.update-status__loading {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text-muted);
  font: 10px/1 var(--mono);
}

.update-status__spinner {
  width: 9px;
  height: 9px;
  border: 1.5px solid var(--border-strong);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: update-spin 0.7s linear infinite;
}

@keyframes update-spin {
  to { transform: rotate(360deg); }
}

.update-status__error {
  max-width: 220px;
  overflow: hidden;
  color: var(--err);
  text-overflow: ellipsis;
  white-space: nowrap;
  font: 10px/1 var(--mono);
}
</style>
