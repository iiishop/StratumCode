<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'
import HighlightedText from './HighlightedText.vue'

const props = defineProps({ event: { type: Object, required: true } })

const terminal = computed(() => {
  if (props.event.metadata?.session_id) return props.event.metadata
  try {
    const parsed = JSON.parse(props.event.output || '{}')
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
})

const command = computed(() => terminal.value.command || props.event.name || 'terminal')
const output = computed(() => terminal.value.output || props.event.output || '')
const detail = computed(() => {
  const term = terminal.value
  const mode = term.background ? 'background' : 'blocking'
  const shell = term.resolved_shell || term.shell || 'auto'
  return `${mode} · ${shell} · ${term.cwd || '.'}`
})
</script>

<template>
  <EventFrame
    kind="terminal"
    symbol="$"
    label="terminal"
    :detail="command"
    :status="event.status"
    :open="event.open"
    collapsible
    @toggle="event.open = !event.open"
  >
    <div class="terminal-event" :class="{ 'is-background': terminal.background }">
      <div class="terminal-event__meta">
        <span>{{ detail }}</span>
        <b v-if="terminal.session_id">{{ terminal.session_id }}</b>
        <b v-if="terminal.pid">pid {{ terminal.pid }}</b>
      </div>
      <div class="terminal-event__io">
        <span>INPUT</span>
        <pre><HighlightedText :text="event.input" context="tool-data" /></pre>
      </div>
      <div v-if="output" class="terminal-event__io">
        <span>OUTPUT</span>
        <pre><HighlightedText :text="output" context="tool-data" /></pre>
      </div>
    </div>
  </EventFrame>
</template>

<style scoped>
.terminal-event {
  display: grid;
  gap: 9px;
}

.terminal-event__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  color: #617791;
  font: 9px/1.35 var(--mono, monospace);
}

.terminal-event__meta b,
.terminal-event__meta span {
  padding: 3px 6px;
  border-radius: 5px;
  background: rgba(15, 125, 101, .08);
}

.terminal-event.is-background .terminal-event__meta b:first-of-type {
  color: #8a5b00;
  background: rgba(245, 200, 66, .18);
}

.terminal-event__io {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  min-width: 0;
}

.terminal-event__io span {
  padding-top: 9px;
  color: var(--text-muted, #71809c);
  font: 700 8.5px/1 var(--mono, monospace);
  letter-spacing: .09em;
  text-align: right;
}

.terminal-event__io pre {
  min-width: 0;
  max-height: 360px;
  margin: 0;
  padding: 9px 12px;
  overflow: auto;
  border: 1px solid rgba(15, 125, 101, .14);
  border-radius: 8px;
  color: var(--text, #3f5274);
  background: #f7fbf9;
  font: var(--font-code, 12px)/1.55 var(--mono, monospace);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}
</style>
