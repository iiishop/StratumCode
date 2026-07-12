<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'
import HighlightedText from './HighlightedText.vue'

const props = defineProps({ event: { type: Object, required: true } })

const payload = computed(() => {
  try {
    return JSON.parse(props.event.output || '{}')
  } catch {
    return {}
  }
})

const files = computed(() => payload.value.files || payload.value.items?.[0]?.files || [])
const diff = computed(() => payload.value.diff || '')
const patchId = computed(() => payload.value.patch_id || props.event.metadata?.patch_id || '')
const rollbackId = computed(() => payload.value.rollback_id || props.event.metadata?.rollback_id || '')
const status = computed(() => props.event.status || payload.value.status || 'running')
const frameState = computed(() => status.value === 'error' ? 'error' : status.value === 'running' ? 'running' : 'done')
const detail = computed(() => {
  const bits = [patchId.value, props.event.metadata?.step_id || payload.value.step_id, rollbackId.value].filter(Boolean)
  return bits.join(' · ') || props.event.name
})
</script>

<template>
  <EventFrame
    kind="patch"
    symbol="P"
    label="Patch"
    :detail="detail"
    :status="status"
    :state="frameState"
    :open="event.open"
    collapsible
    @toggle="event.open = !event.open"
  >
    <div class="patch-event">
      <div class="patch-event__meta">
        <span v-if="patchId"><b>patch</b>{{ patchId }}</span>
        <span v-if="payload.authorization_id || event.metadata?.authorization_id"><b>auth</b>{{ payload.authorization_id || event.metadata.authorization_id }}</span>
        <span v-if="payload.step_id || event.metadata?.step_id"><b>step</b>{{ payload.step_id || event.metadata.step_id }}</span>
        <span v-if="rollbackId"><b>rollback</b>{{ rollbackId }}</span>
        <span v-if="payload.next_state"><b>next</b>{{ payload.next_state }}</span>
      </div>

      <div v-if="files.length" class="patch-event__files">
        <div v-for="file in files" :key="file.path || file.rollback_id" class="patch-event__file">
          <code>{{ file.path || file.rollback_id }}</code>
          <span v-if="file.mode">{{ file.mode }}</span>
          <span v-if="file.operations_applied">{{ file.operations_applied }} ops</span>
          <span v-if="file.status">{{ file.status }}</span>
          <span v-if="file.lsp" :class="{ 'is-lsp-error': file.lsp.diagnostic_count }">LSP {{ file.lsp.diagnostic_count || 0 }}</span>
          <small v-if="file.before_hash && file.after_hash">{{ file.before_hash.slice(0, 18) }} -> {{ file.after_hash.slice(0, 18) }}</small>
        </div>
      </div>

      <div v-if="files.some(file => file.lsp?.diagnostics?.length)" class="patch-event__diagnostics">
        <div v-for="file in files.filter(item => item.lsp?.diagnostics?.length)" :key="`${file.path}:lsp`">
          <b>{{ file.path }}</b>
          <ul>
            <li v-for="(diagnostic, index) in file.lsp.diagnostics" :key="index">
              L{{ (diagnostic.range?.start?.line ?? 0) + 1 }} {{ diagnostic.message }}
            </li>
          </ul>
        </div>
      </div>

      <pre v-if="diff" class="patch-event__diff"><code><HighlightedText :text="diff" context="diff" /></code></pre>
      <pre v-else-if="event.output" class="patch-event__raw"><HighlightedText :text="event.output" context="tool-data" /></pre>
    </div>
  </EventFrame>
</template>

<style scoped>
.patch-event {
  display: grid;
  gap: 10px;
}

.patch-event__meta,
.patch-event__files {
  display: grid;
  gap: 6px;
}

.patch-event__meta span,
.patch-event__file {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  border: 1px solid rgba(23, 86, 209, .1);
  border-radius: 8px;
  background: color-mix(in srgb, var(--event, #e56b2f) 5%, #fff);
  color: var(--text, #3f5274);
  font: var(--font-code, 12px)/1.45 var(--mono, monospace);
}

.patch-event__meta b {
  color: var(--text-muted, #71809c);
  font-size: 9px;
  text-transform: uppercase;
}

.patch-event__file code {
  min-width: 0;
  overflow: hidden;
  color: var(--text-h, #102a5c);
  text-overflow: ellipsis;
}

.patch-event__file span,
.patch-event__file small {
  flex: 0 0 auto;
  color: var(--text-muted, #71809c);
}

.patch-event__file .is-lsp-error {
  color: #c44747;
}

.patch-event__diagnostics {
  display: grid;
  gap: 6px;
  padding: 9px 12px;
  border: 1px solid rgba(196, 71, 71, .16);
  border-radius: 8px;
  color: #7b2e2e;
  background: rgba(196, 71, 71, .055);
  font: var(--font-code, 12px)/1.5 var(--mono, monospace);
}

.patch-event__diagnostics b {
  color: #8f2f2f;
}

.patch-event__diagnostics ul {
  margin: 4px 0 0;
  padding-left: 18px;
}

.patch-event__diff,
.patch-event__raw {
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid rgba(23, 86, 209, .1);
  border-radius: 9px;
  background: #f8fafd;
  color: #304863;
  font: var(--font-code, 12px)/1.55 var(--mono, monospace);
  white-space: pre-wrap;
}
</style>
