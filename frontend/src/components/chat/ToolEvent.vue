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

const frameSymbol = computed(() => {
  if (props.event.name === 'resolve_unknowns') return 'R'
  return props.event.symbol || 'T'
})

const frameLabel = computed(() => {
  if (props.event.name === 'resolve_unknowns') return 'RESOLVE UNKNOWN'
  return props.event.name
})

const frameDetail = computed(() => {
  if (props.event.name !== 'resolve_unknowns') return props.event.description
  try {
    const input = JSON.parse(props.event.input || '{}')
    const ids = Array.isArray(input.resolutions)
      ? input.resolutions.map(item => item?.unknown_id || item?.id).filter(Boolean)
      : []
    return ids.length ? `Resolve ${ids.join(', ')}` : 'Resolve investigation unknowns'
  } catch {
    return 'Resolve investigation unknowns'
  }
})

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

const investigationContract = computed(() => {
  const meta = props.event.metadata || {}
  const contract = props.event.investigation_contract || meta.investigation_contract || null
  return contract && typeof contract === 'object' ? contract : null
})

const contractItems = computed(() => {
  const contract = investigationContract.value
  if (!contract) return []
  return [
    ['Hypothesis', contract.hypothesis],
    ['Expected', contract.expected_observation],
    ['Impact', contract.decision_impact],
    ['Stop', contract.stop_condition],
  ].filter(([, value]) => String(value || '').trim())
})

// 结论类工具（resolve/record/finish）像质量门一样直接显示成功/失败原因，
// 不用展开 RESULT 才能看到。成功取 input.reason（模型的原因），失败取 output.error。
const verdictTools = ['resolve_unknowns', 'record_investigation_findings', 'finish_investigation']
const verdict = computed(() => {
  if (!verdictTools.includes(props.event.name)) return null
  let out = null
  try { out = JSON.parse(props.event.output || '{}') } catch { /* keep null */ }
  let inp = null
  try { inp = JSON.parse(props.event.input || '{}') } catch { /* keep null */ }
  if (props.event.status === 'error' || (out && out.error)) {
    const err = out && (out.error || out.message)
    const text = typeof err === 'string' ? err : (err && (err.message || JSON.stringify(err))) || 'Tool failed'
    return { ok: false, text: String(text).slice(0, 600) }
  }
  if (inp && String(inp.reason || '').trim()) {
    return { ok: true, text: String(inp.reason).slice(0, 600) }
  }
  if (props.event.name === 'resolve_unknowns' && Array.isArray(out && out.unknown_ids)) {
    return { ok: true, text: `Resolved ${out.unknown_ids.join(', ')}` }
  }
  if (out && out.recorded) return { ok: true, text: 'Findings recorded' }
  return null
})
</script>

<template>
  <EventFrame
    kind="tool"
    :symbol="frameSymbol"
    :label="frameLabel"
    :detail="frameDetail"
    :status="event.status"
    :open="event.open"
    collapsible
    @toggle="event.open = !event.open"
  >
    <div class="tool-io">
      <div v-if="verdict" class="tool-io__verdict" :class="verdict.ok ? 'is-ok' : 'is-error'">
        <span>{{ verdict.ok ? 'SUCCESS' : 'FAILED' }}</span>
        <p><HighlightedText :text="verdict.text" /></p>
      </div>
      <div v-if="contractItems.length" class="tool-io__contract">
        <span>CONTRACT</span>
        <dl>
          <template v-for="([label, value]) in contractItems" :key="label">
            <dt>{{ label }}</dt>
            <dd><HighlightedText :text="String(value)" /></dd>
          </template>
        </dl>
      </div>
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

.tool-io__verdict p {
  min-width: 0;
  margin: 0;
  padding: 8px 12px;
  border: 1px solid rgba(17, 134, 111, .16);
  border-radius: 8px;
  color: #0d6f5c;
  background: rgba(17, 134, 111, .055);
  font: var(--font-code, 12px)/1.55 var(--mono, monospace);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tool-io__verdict.is-error p {
  border-color: rgba(196, 71, 71, .18);
  color: #7b2e2e;
  background: rgba(196, 71, 71, .055);
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

.tool-io__contract dl {
  min-width: 0;
  margin: 0;
  padding: 9px 12px;
  border: 1px solid rgba(102, 88, 199, .14);
  border-radius: 8px;
  background: rgba(102, 88, 199, .045);
}

.tool-io__contract dt {
  margin: 0 0 3px;
  color: #4f45a4;
  font: 800 9px/1.35 var(--mono, monospace);
  letter-spacing: .04em;
  text-transform: uppercase;
}

.tool-io__contract dd {
  min-width: 0;
  margin: 0 0 8px;
  color: var(--text, #3f5274);
  font: var(--font-code, 12px)/1.5 var(--mono, monospace);
  overflow-wrap: anywhere;
}

.tool-io__contract dd:last-child {
  margin-bottom: 0;
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
