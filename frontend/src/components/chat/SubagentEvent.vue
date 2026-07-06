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
.agent-result { display: flex; gap: 8px; padding: 3px; color: #526982; font-size: 13px; line-height: 1.6; }.agent-result > span:first-child { color: #6658c7; font-weight: 800; }
.agent-scan { display: flex; gap: 4px; padding: 4px 3px; }.agent-scan i { width: 18px; height: 3px; border-radius: 3px; background: #6658c7; animation: scan 1s ease-in-out infinite alternate; }.agent-scan i:nth-child(2) { animation-delay: .15s; }.agent-scan i:nth-child(3) { animation-delay: .3s; }
@keyframes scan { to { opacity: .18; transform: scaleX(.55); } } @media (prefers-reduced-motion: reduce) { .agent-scan i { animation: none; } }
</style>
