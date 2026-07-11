<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const summary = computed(() => props.event.intent?.summary || 'Task analyzed')
const intentType = computed(() => props.event.intent?.type || 'other')
const scopeRows = computed(() => {
  const scope = props.event.scope || {}
  return [
    ...(scope.in || []).map(text => ({ label: 'in', text })),
    ...(scope.out || []).map(text => ({ label: 'out', text })),
    ...(scope.undecided || []).map(text => ({ label: 'undecided', text })),
  ]
})
const behaviorRows = computed(() => {
  const behavior = props.event.behavior_contract || {}
  return [
    ...(behavior.inputs || []).map(text => ({ label: 'input', text })),
    ...(behavior.outputs || []).map(text => ({ label: 'output', text })),
    ...(behavior.success_behaviors || []).map(text => ({ label: 'success', text })),
    ...(behavior.failure_behaviors || []).map(text => ({ label: 'failure', text })),
    ...(behavior.boundaries || []).map(text => ({ label: 'boundary', text })),
  ]
})

function count(items) {
  return Array.isArray(items) ? items.length : 0
}

function unknownText(item) {
  return typeof item === 'string' ? item : item?.question || item?.text || ''
}
</script>

<template>
  <EventFrame
    kind="task-analysis"
    symbol="A"
    label="Task analyzer"
    :detail="summary"
    status="done"
    state="done"
    open
  >
    <div class="task-analysis">
      <div class="task-analysis__intent">
        <span>{{ intentType }}</span>
        <strong>{{ summary }}</strong>
      </div>

      <section v-if="count(event.constraints)">
        <small>constraints</small>
        <p v-for="item in event.constraints" :key="item">{{ item }}</p>
      </section>

      <section v-if="count(event.acceptance_criteria)">
        <small>acceptance</small>
        <p v-for="item in event.acceptance_criteria" :key="item.id || item.text">
          <b>{{ item.id }}</b>{{ item.text }}
        </p>
      </section>

      <section v-if="behaviorRows.length">
        <small>behavior contract</small>
        <p v-for="item in behaviorRows" :key="`${item.label}:${item.text}`">
          <b>{{ item.label }}</b>{{ item.text }}
        </p>
      </section>

      <section v-if="scopeRows.length">
        <small>scope</small>
        <p v-for="item in scopeRows" :key="`${item.label}:${item.text}`">
          <b>{{ item.label }}</b>{{ item.text }}
        </p>
      </section>

      <section v-if="count(event.hypotheses)">
        <small>hypotheses</small>
        <p v-for="item in event.hypotheses" :key="item.text">
          <b>{{ item.certainty }}</b>{{ item.text }}
        </p>
      </section>

      <section v-if="count(event.clues)">
        <small>clues</small>
        <p v-for="item in event.clues" :key="`${item.kind}:${item.value}`">
          <b>{{ item.kind }}</b>{{ item.path || item.value }}<span v-if="item.line">:{{ item.line }}</span>
        </p>
      </section>

      <section v-if="count(event.unknowns)">
        <small>unknowns</small>
        <p v-for="item in event.unknowns" :key="item.id || unknownText(item)">
          <b v-if="item.id">{{ item.id }}</b>{{ unknownText(item) }}
          <span v-if="item.blocking === false">deferred</span>
        </p>
      </section>
    </div>
  </EventFrame>
</template>

<style scoped>
.task-analysis {
  display: grid;
  gap: 11px;
}

.task-analysis__intent {
  display: grid;
  gap: 5px;
  padding: 9px 10px;
  border: 1px solid color-mix(in srgb, var(--event, #6658c7) 14%, #dbe5f2);
  border-radius: 8px;
  background: color-mix(in srgb, var(--event, #6658c7) 5%, #fff);
}

.task-analysis__intent span,
.task-analysis section small {
  color: var(--event, #6658c7);
  font: 800 8px/1 var(--mono, monospace);
  letter-spacing: .06em;
  text-transform: uppercase;
}

.task-analysis__intent strong {
  color: var(--text-h, #102a5c);
  font-size: 12px;
  line-height: 1.45;
}

.task-analysis section {
  display: grid;
  gap: 6px;
}

.task-analysis p {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.45;
}

.task-analysis b {
  margin-right: 7px;
  color: var(--event, #6658c7);
  font: 800 8px/1 var(--mono, monospace);
  text-transform: uppercase;
}
</style>
