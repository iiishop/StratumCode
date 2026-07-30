<script setup>
import { computed, provide, ref, watch, onMounted, onUnmounted } from 'vue'
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
import UserQuestionEvent from './UserQuestionEvent.vue'
import UsageEvent from './UsageEvent.vue'

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
  user_question: UserQuestionEvent,
  usage: UsageEvent,
}

// ── elapsed timer ──
const ev = props.event
if (ev.startTime == null) {
  ev.startTime = Date.now()
  ev.elapsed = 0
  ev.isDone = false
}

const elapsed = ref(ev.elapsed || 0)
const isDone = ref(ev.isDone || false)

let timer = null
let doneTimer = null

function formatElapsed(ms) {
  const totalSec = Math.floor(ms / 1000)
  const hh = String(Math.floor(totalSec / 3600)).padStart(2, '0')
  const mm = String(Math.floor((totalSec % 3600) / 60)).padStart(2, '0')
  const ss = String(totalSec % 60).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

const elapsedDisplay = computed(() => formatElapsed(elapsed.value))

function startTimer() {
  if (timer) return
  timer = setInterval(() => {
    elapsed.value = Date.now() - ev.startTime
    ev.elapsed = elapsed.value
  }, 1000)
}

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  if (doneTimer) {
    clearTimeout(doneTimer)
    doneTimer = null
  }
}

function markDone() {
  if (isDone.value) return
  isDone.value = true
  ev.isDone = true
  elapsed.value = Date.now() - ev.startTime
  ev.elapsed = elapsed.value
  stopTimer()
}

// Watch event.data for changes; reset the 2s done-detection timer on each change.
watch(
  () => props.event.data,
  () => {
    if (isDone.value) return
    if (doneTimer) clearTimeout(doneTimer)
    doneTimer = setTimeout(() => {
      markDone()
    }, 2000)
  },
  { deep: true, immediate: false }
)

onMounted(() => {
  if (!isDone.value) {
    startTimer()
    // Start the done-detection timer immediately in case data never changes.
    doneTimer = setTimeout(() => {
      markDone()
    }, 2000)
  }
})

onUnmounted(() => {
  stopTimer()
})
</script>

<template>
  <div class="chat-event">
    <div class="chat-event__time" aria-label="Event elapsed time">{{ elapsedDisplay }}</div>
    <component
      :is="eventComponents[event.type]"
      v-if="eventComponents[event.type]"
      :event="event.data"
      @answer="$emit('answer', $event)"
    />
  </div>
</template>

<style scoped>
.chat-event {
  position: relative;
}

.chat-event__time {
  font-family: var(--mono);
  font-size: 9px;
  color: var(--text-muted);
  margin-bottom: 2px;
  user-select: none;
}
</style>
