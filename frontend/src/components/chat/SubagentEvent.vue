<script setup>
import EventFrame from './EventFrame.vue'
import HighlightedText from './HighlightedText.vue'
defineProps({ event: { type: Object, required: true } })
</script>

<template>
  <EventFrame
    kind="subagent"
    symbol="@"
    :label="event.name"
    :detail="event.task"
    :status="event.status"
    :open="event.open"
    collapsible
    @toggle="event.open = !event.open"
  >
    <div v-if="event.result" class="agent-result"><span>↳</span><span><HighlightedText :text="event.result" /></span></div>
    <div v-else class="agent-scan"><i></i><i></i><i></i></div>
  </EventFrame>
</template>

<style scoped>
.agent-result {
  display: flex;
  min-width: 0;
  gap: 10px;
  padding: 4px 0 0;
  color: var(--text, #3f5274);
  font-size: 13px;
  line-height: 1.65;
  overflow-wrap: anywhere;
}

.agent-result > span:first-child {
  flex: 0 0 auto;
  color: var(--event, #6658c7);
  font-weight: 800;
  font-size: 14px;
}

.agent-result > span:last-child { min-width: 0; }

.agent-scan {
  display: flex;
  gap: 5px;
  padding: 8px 2px;
}

.agent-scan i {
  width: 20px;
  height: 3px;
  border-radius: 3px;
  background: var(--event, #6658c7);
  animation: scan 1.2s ease-in-out infinite alternate;
}

.agent-scan i:nth-child(2) { animation-delay: .18s; }
.agent-scan i:nth-child(3) { animation-delay: .36s; }

@keyframes scan {
  to { opacity: .15; transform: scaleX(.5); }
}

@media (prefers-reduced-motion: reduce) {
  .agent-scan i { animation: none; }
}
</style>
