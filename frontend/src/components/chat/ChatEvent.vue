<script setup>
import { computed, provide } from 'vue'
import ThinkingEvent from './ThinkingEvent.vue'
import ToolEvent from './ToolEvent.vue'
import CodeNavEvent from './CodeNavEvent.vue'
import SubagentEvent from './SubagentEvent.vue'
import DiffEvent from './DiffEvent.vue'
import OutputEvent from './OutputEvent.vue'
import TaskAnalysisEvent from './TaskAnalysisEvent.vue'
import TaskUpdateEvent from './TaskUpdateEvent.vue'
import DesignPlanEvent from './DesignPlanEvent.vue'
import PatchPlanEvent from './PatchPlanEvent.vue'
import PatchEvent from './PatchEvent.vue'
import StageEvent from './StageEvent.vue'
import HypothesisEvent from './HypothesisEvent.vue'
import EvidenceEvent from './EvidenceEvent.vue'
import EvidenceRelationEvent from './EvidenceRelationEvent.vue'
import VerdictEvent from './VerdictEvent.vue'
import UsageEvent from './UsageEvent.vue'
import StepResultEvent from './StepResultEvent.vue'
import SafetyStopEvent from './SafetyStopEvent.vue'
import UserQuestionEvent from './UserQuestionEvent.vue'

const props = defineProps({
  event: { type: Object, required: true },
  events: { type: Array, default: () => [] },
})
defineEmits(['answer'])

provide('messageEvents', computed(() => props.events))

const eventComponents = {
  thinking: ThinkingEvent,
  tool: ToolEvent,
  code_nav: CodeNavEvent,
  subagent: SubagentEvent,
  diff: DiffEvent,
  output: OutputEvent,
  task_analysis: TaskAnalysisEvent,
  task_update: TaskUpdateEvent,
  design_plan: DesignPlanEvent,
  patch_plan: PatchPlanEvent,
  patch: PatchEvent,
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
