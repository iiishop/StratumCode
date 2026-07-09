<script setup>
import EventFrame from './EventFrame.vue'
defineProps({ event: { type: Object, required: true } })
</script>

<template>
  <EventFrame
    kind="relation"
    symbol="↔"
    label="Evidence relationship"
    :detail="`${event.source_id} ${event.relation} ${event.target_id}`"
    :status="`${Math.round(event.impact * 100)}% impact`"
    open
  >
    <p class="relation__text">{{ event.explanation }}</p>
  </EventFrame>
</template>

<style scoped>
.relation__text {
  margin: 0;
  padding: 4px 0 0;
  color: var(--text, #3f5274);
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
</style>
