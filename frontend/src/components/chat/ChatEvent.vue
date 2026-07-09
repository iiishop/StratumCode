<script setup>
import ThinkingEvent from './ThinkingEvent.vue'
import ToolEvent from './ToolEvent.vue'
import SubagentEvent from './SubagentEvent.vue'
import DiffEvent from './DiffEvent.vue'
import OutputEvent from './OutputEvent.vue'
import TaskAnalysisEvent from './TaskAnalysisEvent.vue'
import StageEvent from './StageEvent.vue'
import HypothesisEvent from './HypothesisEvent.vue'
import EvidenceEvent from './EvidenceEvent.vue'
import EvidenceRelationEvent from './EvidenceRelationEvent.vue'
import VerdictEvent from './VerdictEvent.vue'
import UsageEvent from './UsageEvent.vue'
import StepResultEvent from './StepResultEvent.vue'
import SafetyStopEvent from './SafetyStopEvent.vue'
import UserQuestionEvent from './UserQuestionEvent.vue'

defineProps({ event: { type: Object, required: true } })
defineEmits(['answer'])

const eventComponents = {
  thinking: ThinkingEvent,
  tool: ToolEvent,
  subagent: SubagentEvent,
  diff: DiffEvent,
  output: OutputEvent,
  task_analysis: TaskAnalysisEvent,
  stage: StageEvent,
  hypothesis: HypothesisEvent,
  evidence: EvidenceEvent,
  evidence_relation: EvidenceRelationEvent,
  verdict: VerdictEvent,
  step_result: StepResultEvent,
  safety_stop: SafetyStopEvent,
  user_question: UserQuestionEvent,
  usage: UsageEvent,
}
</script>

<template>
  <component
    :is="eventComponents[event.type]"
    v-if="eventComponents[event.type]"
    :event="event.data"
    @answer="$emit('answer', $event)"
  />
</template>
