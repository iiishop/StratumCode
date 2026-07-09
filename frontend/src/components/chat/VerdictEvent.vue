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
.verdict__score {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
  color: var(--event, #1756d1);
  font: 800 22px/1 var(--mono, monospace);
}

p {
  margin: 0;
  padding: 4px 0 0;
  color: var(--text, #3f5274);
  font-size: 12.5px;
  line-height: 1.65;
}
</style>
