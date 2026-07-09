<script setup>
import EventFrame from './EventFrame.vue'
import HighlightedText from './HighlightedText.vue'
defineProps({ event: { type: Object, required: true } })
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
      <div v-if="event.output"><span>RESULT</span><pre><HighlightedText :text="event.output" context="tool-data" /></pre></div>
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
</style>
