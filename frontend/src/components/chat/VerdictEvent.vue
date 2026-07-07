<script setup>
import EventFrame from './EventFrame.vue'
defineProps({ event: { type: Object, required: true } })
</script>

<template>
  <EventFrame
    kind="verdict"
    :state="event.verdict"
    :symbol="event.verdict === 'supported' ? '✓' : event.verdict === 'refuted' ? '×' : '?'"
    label="Evidence verdict"
    :status="event.verdict"
    open
  >
    <div class="verdict__score">{{ Math.round(event.confidence * 100) }}%</div>
    <p>{{ event.summary }}</p>
  </EventFrame>
</template>

<style scoped>
.verdict__score { float: right; margin: 0 0 8px 12px; color: #1756d1; font: 800 21px/1 var(--mono); }
p { margin: 0; color: #304a69; font-size: 12px; line-height: 1.65; }
</style>
