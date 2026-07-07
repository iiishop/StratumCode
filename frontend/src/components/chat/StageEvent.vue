<script setup>
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })

const labels = {
  support: 'Support evidence',
  oppose: 'Opposing evidence',
  audit: 'Audit evidence',
  evaluate: 'Evaluate verdict',
}
</script>

<template>
  <EventFrame
    kind="stage"
    symbol="S"
    :label="event.label || 'Agent stage'"
    :detail="`${labels[event.phase] || event.phase || 'starting'} · ${event.provider || ''} · ${event.model || ''}`"
    :status="event.state"
    :state="event.state"
    :open="false"
  />
</template>
