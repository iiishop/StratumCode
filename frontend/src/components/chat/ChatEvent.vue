<script setup>
import ThinkingEvent from './ThinkingEvent.vue'
import ToolEvent from './ToolEvent.vue'
import SubagentEvent from './SubagentEvent.vue'
import DiffEvent from './DiffEvent.vue'
import OutputEvent from './OutputEvent.vue'

defineProps({ event: { type: Object, required: true } })

const eventComponents = {
  thinking: ThinkingEvent,
  tool: ToolEvent,
  subagent: SubagentEvent,
  diff: DiffEvent,
  output: OutputEvent,
}
</script>

<template>
  <component :is="eventComponents[event.type]" v-if="eventComponents[event.type]" :event="event.data" />
</template>
