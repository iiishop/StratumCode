<script setup>
import EventFrame from './EventFrame.vue'
import HighlightedText from './HighlightedText.vue'
defineProps({ event: { type: Object, required: true } })
</script>

<template>
  <EventFrame
    kind="thinking"
    symbol="···"
    :label="event.done ? 'Reasoning complete' : 'Reasoning'"
    :status="event.done ? 'done' : 'running'"
    :open="event.open"
    collapsible
    @toggle="event.open = !event.open"
  >
    <p class="thinking-copy"><HighlightedText :text="event.text" /></p>
  </EventFrame>
</template>

<style scoped>
.thinking-copy { margin: 0; padding: 2px 3px 1px; color: #596c84; font-size: 11px; line-height: 1.65; white-space: pre-wrap; }
</style>
