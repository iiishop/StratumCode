<script setup>
import { computed, provide } from 'vue'
import ThinkingEvent from './ThinkingEvent.vue'
import ToolEvent from './ToolEvent.vue'
import TerminalEvent from './TerminalEvent.vue'
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
import SkillEvent from './SkillEvent.vue'
import StateTransitionEvent from './StateTransitionEvent.vue'
import HypothesisEvent from './HypothesisEvent.vue'
import EvidenceEvent from './EvidenceEvent.vue'
import EvidenceRelationEvent from './EvidenceRelationEvent.vue'
import VerdictEvent from './VerdictEvent.vue'
import StepResultEvent from './StepResultEvent.vue'
import SafetyStopEvent from './SafetyStopEvent.vue'
import InvestigationFactsEvent from './InvestigationFactsEvent.vue'
import QuestionRecord from './QuestionRecord.vue'
import ValidationChecklistEvent from './ValidationChecklistEvent.vue'
import ValidationResultEvent from './ValidationResultEvent.vue'
import QualityGateEvent from './QualityGateEvent.vue'
import MemoryEvent from './MemoryEvent.vue'

const props = defineProps({
  event: { type: Object, required: true },
  events: { type: Array, default: () => [] },
})
defineEmits(['answer'])

provide('messageEvents', computed(() => props.events))

const eventComponents = {
  thinking: ThinkingEvent,
  tool: ToolEvent,
  terminal: TerminalEvent,
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
  skill: SkillEvent,
  state_transition: StateTransitionEvent,
  hypothesis: HypothesisEvent,
  evidence: EvidenceEvent,
  evidence_relation: EvidenceRelationEvent,
  verdict: VerdictEvent,
  step_result: StepResultEvent,
  safety_stop: SafetyStopEvent,
  investigation_facts: InvestigationFactsEvent,
  user_question: QuestionRecord,
  verification_checklist: ValidationChecklistEvent,
  validation_result: ValidationResultEvent,
  quality_gate: QualityGateEvent,
  memory_write: MemoryEvent,
  memory_reference: MemoryEvent,
  memory_stale: MemoryEvent,
  memory_conflict: MemoryEvent,
}
</script>

<template>
  <div v-if="eventComponents[event.type]" class="chat-event">
    <component
      :is="eventComponents[event.type]"
      :event="event.data"
      @answer="$emit('answer', $event)"
    />
  </div>
</template>

<style scoped>
</style>
