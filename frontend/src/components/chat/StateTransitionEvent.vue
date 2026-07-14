<script setup>
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })
</script>

<template>
  <EventFrame
    kind="state-transition"
    symbol=">"
    label="State transition"
    :detail="`${event.from_state || 'unknown'} -> ${event.to_state || 'unknown'}${event.reason ? ` · ${event.reason}` : ''}`"
    :status="event.to_state || ''"
    :state="event.to_state"
    :open="false"
  />
</template>
