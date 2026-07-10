<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'
import HighlightedText from './HighlightedText.vue'
const props = defineProps({ event: { type: Object, required: true } })

const diagnostics = computed(() => {
  const match = String(props.event.output || '').match(/<lsp-diagnostics>\n?([\s\S]*?)\n?<\/lsp-diagnostics>/)
  if (!match) return []
  return match[1].split('\n').map(line => line.trim()).filter(Boolean)
})

const displayOutput = computed(() => String(props.event.output || '').replace(/\n?<lsp-diagnostics>[\s\S]*?<\/lsp-diagnostics>/, '').trimEnd())

const lspStatus = computed(() => {
  const meta = props.event.metadata || {}
  if (props.event.name !== 'read' || !('lsp_checked' in meta)) return null
  if (meta.lsp_checked) {
    return {
      ok: true,
      label: meta.lsp_server || 'LSP',
      detail: `${meta.diagnostics || 0} diagnostics`,
    }
  }
  return {
    ok: false,
    label: 'not used',
    detail: meta.lsp_error || 'no matching enabled LSP server',
  }
})
</script>

<template>
  <EventFrame
    kind="tool"
    :symbol="event.symbol || 'T'"
    :label="event.name"
    :detail="event.description"
    :status="event.status"
    :open="event.open"
    collapsible
    @toggle="event.open = !event.open"
  >
    <div class="tool-io">
      <div><span>INPUT</span><pre><HighlightedText :text="event.input" context="tool-data" /></pre></div>
      <div v-if="diagnostics.length" class="tool-io__diagnostics">
        <span>LSP</span>
        <ul>
          <li v-for="item in diagnostics" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div v-else-if="lspStatus" class="tool-io__lsp" :class="{ 'is-ok': lspStatus.ok }">
        <span>LSP</span>
        <p><b>{{ lspStatus.label }}</b>{{ lspStatus.detail }}</p>
      </div>
      <div v-if="displayOutput"><span>RESULT</span><pre><HighlightedText :text="displayOutput" context="tool-data" /></pre></div>
    </div>
  </EventFrame>
</template>

<style scoped>
.tool-io {
  display: grid;
  gap: 9px;
}

.tool-io div {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  min-width: 0;
}

.tool-io span {
  padding-top: 9px;
  color: var(--text-muted, #71809c);
  font: 700 8.5px/1 var(--mono, monospace);
  letter-spacing: .09em;
  text-align: right;
}

.tool-io pre {
  min-width: 0;
  margin: 0;
  padding: 9px 12px;
  overflow: auto;
  border: 1px solid rgba(23, 86, 209, .1);
  border-radius: 8px;
  color: var(--text, #3f5274);
  background: color-mix(in srgb, var(--event, #1756d1) 4%, #f7f9fd);
  font: var(--font-code, 12px)/1.55 var(--mono, monospace);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.tool-io__diagnostics ul {
  min-width: 0;
  margin: 0;
  padding: 8px 12px 8px 26px;
  border: 1px solid rgba(196, 71, 71, .18);
  border-radius: 8px;
  color: #7b2e2e;
  background: rgba(196, 71, 71, .055);
  font: var(--font-code, 12px)/1.55 var(--mono, monospace);
}

.tool-io__diagnostics li + li {
  margin-top: 4px;
}

.tool-io__lsp p {
  min-width: 0;
  margin: 0;
  padding: 8px 12px;
  border: 1px solid rgba(196, 139, 0, .18);
  border-radius: 8px;
  color: #795b00;
  background: rgba(245, 200, 66, .08);
  font: var(--font-code, 12px)/1.55 var(--mono, monospace);
}

.tool-io__lsp.is-ok p {
  border-color: rgba(17, 134, 111, .16);
  color: #0d6f5c;
  background: rgba(17, 134, 111, .055);
}

.tool-io__lsp b {
  margin-right: 8px;
  font-weight: 800;
}
</style>
