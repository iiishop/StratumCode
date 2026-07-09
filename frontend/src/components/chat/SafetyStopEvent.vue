<script setup>
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })
</script>

<template>
  <EventFrame
    kind="safety-stop"
    state="error"
    symbol="!"
    label="Safety stop"
    :detail="event.message"
    :status="event.reason"
  />
</template>
