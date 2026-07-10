<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'
import HighlightedText from './HighlightedText.vue'

const props = defineProps({ event: { type: Object, required: true } })

const metadata = computed(() => props.event.metadata || {})
const detail = computed(() => [
  metadata.value.operation || props.event.title || '',
  metadata.value.server || '',
  metadata.value.path || '',
].filter(Boolean).join(' · '))
</script>

<template>
  <EventFrame
    kind="code-nav"
    symbol="L5"
    label="Code navigation"
    :detail="detail"
    :status="event.status"
    :state="event.status === 'error' ? 'error' : 'done'"
    :open="event.open"
    collapsible
    @toggle="event.open = !event.open"
  >
    <div class="code-nav">
      <div><span>INPUT</span><pre><HighlightedText :text="event.input" context="tool-data" /></pre></div>
      <div v-if="event.output"><span>{{ event.status === 'error' ? 'ERROR' : 'RESULT' }}</span><pre><HighlightedText :text="event.output" context="tool-data" /></pre></div>
    </div>
  </EventFrame>
</template>

<style scoped>
.code-nav {
  display: grid;
  gap: 9px;
}

.code-nav div {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  min-width: 0;
}

.code-nav span {
  padding-top: 9px;
  color: var(--text-muted, #71809c);
  font: 700 8.5px/1 var(--mono, monospace);
  letter-spacing: .09em;
  text-align: right;
}

.code-nav pre {
  min-width: 0;
  margin: 0;
  padding: 9px 12px;
  overflow: auto;
  border: 1px solid rgba(102, 88, 199, .14);
  border-radius: 8px;
  color: var(--text, #3f5274);
  background: rgba(102, 88, 199, .045);
  font: var(--font-code, 12px)/1.55 var(--mono, monospace);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}
</style>
