<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

const items = ref([])
const loading = ref(false)
const error = ref('')
const openIds = reactive({})
let timer = null

const runningCount = computed(() => items.value.filter(item => item.status === 'running').length)

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const data = await request('/api/terminal/processes')
    items.value = Array.isArray(data.items) ? data.items : []
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    loading.value = false
  }
}

async function terminate(item) {
  if (!item?.session_id || item.status !== 'running') return
  try {
    const updated = await request('/api/terminal/kill', {
      method: 'POST',
      body: JSON.stringify({ session_id: item.session_id }),
    })
    Object.assign(item, updated)
  } catch (err) {
    error.value = err.message || String(err)
  }
}

function toggle(item) {
  openIds[item.session_id] = !openIds[item.session_id]
}

function statusLabel(item) {
  if (item.status === 'running') return `running · ${duration(item)}`
  if (item.exit_code !== null && item.exit_code !== undefined) return `${item.status} · exit ${item.exit_code}`
  return item.status
}

function duration(item) {
  const ms = Number(item.duration_ms || 0)
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  return `${Math.round(ms / 60_000)}m`
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 2000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <section class="terminal-panel">
    <div class="terminal-panel__head">
      <div>
        <strong>Background terminal</strong>
        <small>{{ runningCount }} running · {{ items.length }} tracked</small>
      </div>
      <button type="button" :disabled="loading" @click="refresh">Refresh</button>
    </div>

    <p v-if="error" class="terminal-panel__error">{{ error }}</p>
    <p v-else-if="!items.length && !loading" class="terminal-panel__empty">No background terminal sessions.</p>

    <article v-for="item in items" :key="item.session_id" class="terminal-card" :class="`is-${item.status}`">
      <button type="button" class="terminal-card__summary" @click="toggle(item)">
        <i></i>
        <span>
          <strong>{{ item.command }}</strong>
          <small>{{ item.resolved_shell || item.shell }} · {{ item.cwd || '.' }}</small>
        </span>
        <b>{{ statusLabel(item) }}</b>
      </button>
      <div v-show="openIds[item.session_id]" class="terminal-card__body">
        <div class="terminal-card__meta">
          <span>{{ item.session_id }}</span>
          <span v-if="item.pid">pid {{ item.pid }}</span>
          <span v-if="item.output_chars">{{ item.output_chars }} chars</span>
        </div>
        <pre>{{ item.output || '(no output yet)' }}</pre>
        <button
          v-if="item.status === 'running'"
          type="button"
          class="terminal-card__kill"
          @click.stop="terminate(item)"
        >
          Stop process
        </button>
      </div>
    </article>
  </section>
</template>

<style scoped>
.terminal-panel {
  display: grid;
  gap: 9px;
}

.terminal-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 2px 2px 1px;
}

.terminal-panel__head div {
  display: grid;
  gap: 2px;
}

.terminal-panel__head strong {
  color: #294564;
  font-size: 12px;
}

.terminal-panel__head small {
  color: #8294aa;
  font: 9px/1 var(--mono, monospace);
}

.terminal-panel__head button,
.terminal-card__kill {
  height: 24px;
  padding: 0 8px;
  border: 1px solid #bfd0ea;
  border-radius: 6px;
  color: #0f7d65;
  background: #eefaf6;
  font: 700 9px/1 var(--mono, monospace);
  cursor: pointer;
}

.terminal-panel__head button:disabled {
  opacity: .45;
  cursor: default;
}

.terminal-panel__empty,
.terminal-panel__error {
  margin: 0;
  padding: 14px 12px;
  border: 1px dashed #cbd9ec;
  border-radius: 9px;
  color: #7188a3;
  background: #f8faff;
  font: 10px/1.45 var(--mono, monospace);
  text-align: center;
}

.terminal-panel__error {
  border-color: #efc7c7;
  color: #8a3333;
  background: #fff8f8;
}

.terminal-card {
  overflow: hidden;
  border: 1px solid #d7e2ef;
  border-left: 3px solid #94a8c2;
  border-radius: 9px;
  background: #fff;
}

.terminal-card.is-running {
  border-left-color: #f5c842;
  background: #fffdf5;
}

.terminal-card.is-exited {
  border-left-color: #0f7d65;
}

.terminal-card.is-failed,
.terminal-card.is-timeout,
.terminal-card.is-terminated {
  border-left-color: #c44747;
}

.terminal-card__summary {
  display: grid;
  width: 100%;
  grid-template-columns: 9px minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  padding: 10px;
  border: 0;
  color: #294564;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.terminal-card__summary i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #94a8c2;
}

.terminal-card.is-running .terminal-card__summary i {
  background: #f5c842;
  box-shadow: 0 0 0 4px rgba(245, 200, 66, .18);
}

.terminal-card__summary span {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.terminal-card__summary strong,
.terminal-card__summary small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.terminal-card__summary strong {
  font: 800 10px/1.25 var(--mono, monospace);
}

.terminal-card__summary small,
.terminal-card__summary b {
  color: #7188a3;
  font: 8.5px/1.3 var(--mono, monospace);
}

.terminal-card__summary b {
  justify-self: end;
}

.terminal-card__body {
  display: grid;
  gap: 8px;
  padding: 0 10px 10px 27px;
}

.terminal-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.terminal-card__meta span {
  padding: 2px 5px;
  border-radius: 4px;
  color: #617791;
  background: #eef4ff;
  font: 8px/1.35 var(--mono, monospace);
}

.terminal-card pre {
  max-height: 280px;
  margin: 0;
  padding: 9px;
  overflow: auto;
  border: 1px solid rgba(15, 125, 101, .12);
  border-radius: 7px;
  color: #36506d;
  background: #f7fbf9;
  font: 10px/1.5 var(--mono, monospace);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.terminal-card__kill {
  justify-self: start;
  color: #9b2f2f;
  background: #fff4f4;
  border-color: #efc7c7;
}
</style>
