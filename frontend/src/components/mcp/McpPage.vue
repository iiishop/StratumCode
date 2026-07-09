<script setup>
import { reactive, ref } from 'vue'
import ChatEvent from '../chat/ChatEvent.vue'

const props = defineProps({
  servers: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['refresh', 'start', 'delete', 'configure'])

const hint = ref('https://exa.ai/mcp')
const installing = ref(false)
const installError = ref('')
const configValues = reactive({})
const installMessage = reactive({ id: 'mcp-installer', role: 'assistant', events: [] })
const activeEvents = new Map()

function addEvent(packet) {
  const data = reactive(packet.data || {})
  installMessage.events.push({
    id: packet.id,
    type: packet.event,
    data,
    createdAt: Date.now(),
  })
  activeEvents.set(packet.id, { type: packet.event, data })
}

function applyPacket(packet) {
  if (packet.op === 'start') {
    addEvent(packet)
    return
  }
  if (packet.op === 'delta') {
    const target = activeEvents.get(packet.id)
    if (target) target.data[packet.field] = `${target.data[packet.field] || ''}${packet.value || ''}`
    return
  }
  if (packet.op === 'update') {
    const target = activeEvents.get(packet.id)
    if (target) Object.assign(target.data, packet.patch)
    return
  }
  if (packet.op === 'error') throw new Error(packet.message || 'MCP installer failed')
}

async function install() {
  const text = hint.value.trim()
  if (!text || installing.value) return
  installing.value = true
  installError.value = ''
  installMessage.events.splice(0)
  activeEvents.clear()
  try {
    const response = await fetch('/api/subagents/mcp-install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hint: text }),
    })
    if (!response.ok || !response.body) throw new Error(`Install failed (${response.status})`)
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) if (line.trim()) applyPacket(JSON.parse(line))
      if (done) break
    }
    if (buffer.trim()) applyPacket(JSON.parse(buffer))
    emit('refresh')
  } catch (reason) {
    installError.value = reason.message
  } finally {
    installing.value = false
  }
}

function valueFor(serverId, key) {
  const id = `${serverId}:${key}`
  if (!(id in configValues)) configValues[id] = ''
  return id
}

function saveRequirement(server, key) {
  const id = valueFor(server.id, key)
  if (!configValues[id]) return
  emit('configure', server.id, { [key]: configValues[id] })
  configValues[id] = ''
}

function statusClass(status) {
  if (status === 'running') return 'is-running'
  if (status === 'error') return 'is-error'
  return 'is-idle'
}
</script>

<template>
  <main class="mcp-page">
    <section class="mcp-page__install">
      <div class="mcp-page__heading">
        <div>
          <h1>MCP</h1>
          <p>Paste a docs page, raw MCP URL, or config hint. The installer agent will infer and start the server.</p>
        </div>
        <button type="button" :disabled="installing || !hint.trim()" @click="install">
          {{ installing ? 'Installing' : 'Install' }}
        </button>
      </div>
      <textarea v-model="hint" spellcheck="false"></textarea>
      <p v-if="installError" class="mcp-page__error">{{ installError }}</p>
      <div v-if="installMessage.events.length" class="mcp-page__timeline">
        <ChatEvent v-for="event in installMessage.events" :key="event.id" :event="event" />
      </div>
    </section>

    <section class="mcp-page__servers">
      <div class="mcp-page__section-head">
        <strong>Servers</strong>
        <span>{{ loading ? 'loading' : `${servers.length} configured` }}</span>
      </div>
      <p v-if="error" class="mcp-page__error">{{ error }}</p>
      <article v-for="server in servers" :key="server.id" class="mcp-server">
        <header>
          <span class="mcp-server__mark" :class="statusClass(server.status)">{{ server.name.slice(0, 2).toUpperCase() }}</span>
          <div>
            <strong>{{ server.name }}</strong>
            <small>{{ server.transport === 'http' ? server.url : [server.command, ...(server.args || [])].join(' ') }}</small>
          </div>
          <button type="button" title="Restart" @click="emit('start', server.id)">Start</button>
          <button type="button" title="Delete" @click="emit('delete', server.id)">Delete</button>
        </header>

        <p class="mcp-server__status" :class="statusClass(server.status)">
          {{ server.status }} · {{ server.status_message || 'not started' }}
        </p>

        <div v-if="server.requirements?.length" class="mcp-server__requirements">
          <label v-for="req in server.requirements" :key="req.key">
            <span>{{ req.key }} {{ req.configured ? '(set)' : '(required)' }}</span>
            <input
              :value="configValues[valueFor(server.id, req.key)]"
              type="password"
              placeholder="Paste value"
              @input="configValues[valueFor(server.id, req.key)] = $event.target.value"
            />
            <button type="button" @click="saveRequirement(server, req.key)">Save</button>
          </label>
        </div>

        <div class="mcp-server__tools">
          <span v-for="tool in server.tools" :key="tool.name" :title="tool.description">
            {{ tool.name }}
          </span>
        </div>
      </article>
    </section>
  </main>
</template>

<style scoped>
.mcp-page {
  width: min(1100px, 100%);
  margin: 0 auto;
  padding: 42px 52px 72px;
}

.mcp-page__install,
.mcp-page__servers {
  border-top: 1px solid var(--border-strong);
  padding-top: 22px;
}

.mcp-page__heading,
.mcp-page__section-head,
.mcp-server header,
.mcp-server__requirements label {
  display: flex;
  align-items: center;
}

.mcp-page__heading,
.mcp-page__section-head {
  justify-content: space-between;
  gap: 20px;
}

.mcp-page h1 {
  margin: 0;
  color: var(--text-h);
  font: 570 34px/1.05 var(--heading);
}

.mcp-page p {
  margin: 8px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.6;
}

.mcp-page button {
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-h);
  background: #fff;
  font: 650 10px/1 var(--mono);
  cursor: pointer;
}

.mcp-page__heading button {
  border-color: var(--accent);
  color: #fff;
  background: var(--accent);
}

.mcp-page button:disabled {
  opacity: .45;
  cursor: default;
}

.mcp-page textarea {
  width: 100%;
  min-height: 112px;
  margin-top: 18px;
  padding: 13px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-h);
  background: #fff;
  font: 12px/1.55 var(--mono);
  resize: vertical;
  outline: none;
}

.mcp-page textarea:focus,
.mcp-server__requirements input:focus {
  border-color: var(--accent-border);
  box-shadow: 0 0 0 3px var(--accent-bg);
}

.mcp-page__timeline {
  display: grid;
  gap: 8px;
  margin-top: 18px;
}

.mcp-page__servers {
  margin-top: 38px;
}

.mcp-page__section-head strong {
  color: var(--text-h);
  font-size: 13px;
}

.mcp-page__section-head span,
.mcp-server__status {
  color: var(--text-muted);
  font: 9px/1 var(--mono);
}

.mcp-page__error {
  padding: 9px 10px;
  border: 1px solid var(--err-border);
  border-radius: var(--radius-sm);
  color: var(--err);
  background: var(--err-bg);
}

.mcp-server {
  margin-top: 10px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, .88);
}

.mcp-server header {
  gap: 10px;
}

.mcp-server__mark {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  place-items: center;
  border-radius: 8px;
  color: #fff;
  background: #7c8fa8;
  font: 800 9px/1 var(--mono);
}

.mcp-server__mark.is-running { background: var(--ok); }
.mcp-server__mark.is-error { background: var(--err); }

.mcp-server header > div {
  min-width: 0;
  flex: 1;
  display: grid;
  gap: 2px;
}

.mcp-server strong {
  color: var(--text-h);
  font-size: 12px;
}

.mcp-server small {
  overflow: hidden;
  color: var(--text-muted);
  font: 9px/1.3 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mcp-server__status {
  margin-top: 9px;
}

.mcp-server__status.is-running { color: var(--ok); }
.mcp-server__status.is-error { color: var(--err); }

.mcp-server__requirements {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.mcp-server__requirements label {
  gap: 8px;
}

.mcp-server__requirements span {
  width: 150px;
  color: var(--text-muted);
  font: 9px/1.2 var(--mono);
}

.mcp-server__requirements input {
  min-width: 0;
  flex: 1;
  height: 30px;
  padding: 0 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-h);
  background: #fff;
  outline: none;
}

.mcp-server__tools {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.mcp-server__tools span {
  padding: 4px 6px;
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--accent-text);
  background: var(--accent-bg);
  font: 9px/1 var(--mono);
}

@media (max-width: 720px) {
  .mcp-page {
    padding: 30px 18px 52px;
  }

  .mcp-page__heading,
  .mcp-server__requirements label {
    align-items: stretch;
    flex-direction: column;
  }

  .mcp-server__requirements span {
    width: auto;
  }
}
</style>
