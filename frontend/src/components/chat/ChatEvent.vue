<script setup>
import ThinkingEvent from './ThinkingEvent.vue'
import ToolEvent from './ToolEvent.vue'
import SubagentEvent from './SubagentEvent.vue'
import DiffEvent from './DiffEvent.vue'
import OutputEvent from './OutputEvent.vue'
import StageEvent from './StageEvent.vue'
import HypothesisEvent from './HypothesisEvent.vue'
import EvidenceEvent from './EvidenceEvent.vue'
import EvidenceRelationEvent from './EvidenceRelationEvent.vue'
import VerdictEvent from './VerdictEvent.vue'

defineProps({ event: { type: Object, required: true } })

const eventComponents = {
  thinking: ThinkingEvent,
  tool: ToolEvent,
  subagent: SubagentEvent,
  diff: DiffEvent,
  output: OutputEvent,
  stage: StageEvent,
  hypothesis: HypothesisEvent,
  evidence: EvidenceEvent,
  evidence_relation: EvidenceRelationEvent,
  verdict: VerdictEvent,
}
</script>

<template>
  <component :is="eventComponents[event.type]" v-if="eventComponents[event.type]" :event="event.data" />
</template>
