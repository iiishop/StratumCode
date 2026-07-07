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
.relation__text { margin: 0; color: #4f6480; font-size: 11.5px; line-height: 1.55; }
</style>
