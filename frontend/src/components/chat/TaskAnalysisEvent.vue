<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const summary = computed(() => props.event.intent?.summary || 'Task analyzed')
const intentType = computed(() => props.event.intent?.type || 'other')

function count(items) {
  return Array.isArray(items) ? items.length : 0
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
        <p v-for="item in event.unknowns" :key="item">{{ item }}</p>
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
