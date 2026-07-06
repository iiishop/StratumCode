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
.tool-io { display: grid; gap: 7px; }
.tool-io div { display: grid; grid-template-columns: 43px minmax(0, 1fr); gap: 7px; align-items: start; }
.tool-io span { padding-top: 7px; color: #8a9bb0; font: 700 8px/1 var(--mono, monospace); letter-spacing: .08em; }
.tool-io pre { margin: 0; padding: 7px 9px; overflow: auto; border-radius: 7px; color: #39516d; background: rgba(224, 233, 247, .52); font: 10px/1.5 var(--mono, monospace); white-space: pre-wrap; }
</style>
