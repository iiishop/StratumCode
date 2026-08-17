<script setup>
import { computed, ref } from 'vue'
import EventFrame from './EventFrame.vue'

const props = defineProps({
  event: { type: Object, required: true },
})

const open = ref(false)
const records = computed(() => Array.isArray(props.event.records) ? props.event.records : [])
const refs = computed(() => Array.isArray(props.event.refs) ? props.event.refs : Array.isArray(props.event.items) ? props.event.items : [])
const kind = computed(() => {
  if (props.event.operation) return 'memory-status'
  if (props.event.items) return 'memory-reference'
  if (props.event.conflicts) return 'memory-conflict'
  if (props.event.stale) return 'memory-stale'
  return 'memory-write'
})
const label = computed(() => {
  if (kind.value === 'memory-status') return 'Memory'
  if (kind.value === 'memory-reference') return 'Memory reference'
  if (kind.value === 'memory-conflict') return 'Memory conflict'
  if (kind.value === 'memory-stale') return 'Stale memory'
  return 'Memory write'
})
const detail = computed(() => props.event.summary || `${records.value.length} record(s), ${refs.value.length} reference(s)`)
const resultEntries = computed(() => {
  const result = props.event.result && typeof props.event.result === 'object' ? props.event.result : {}
  return Object.entries(result)
})
</script>

<template>
  <EventFrame
    :kind="kind"
    :label="label"
    :detail="detail"
    :status="event.status || 'done'"
    symbol="M"
    :open="open"
    collapsible
    @toggle="open = !open"
  >
    <div class="memory-event">
      <div v-if="event.operation" class="memory-event__status">
        <span>{{ event.operation }}</span>
        <small v-if="event.provider || event.model">
          {{ event.provider || 'provider' }} / {{ event.model || 'model' }}{{ event.inherited ? ' · inherited' : '' }}
        </small>
        <div v-if="resultEntries.length" class="memory-event__chips">
          <b v-for="[key, value] in resultEntries" :key="key">{{ key }} {{ value }}</b>
        </div>
      </div>
      <div v-if="records.length" class="memory-event__group">
        <span>Records</span>
        <article v-for="record in records.slice(0, 6)" :key="record.id" class="memory-event__item">
          <strong>{{ record.kind || 'memory' }}</strong>
          <p>{{ record.statement }}</p>
          <small>{{ record.scope }} · {{ record.confidence }} · {{ record.freshness }}</small>
        </article>
      </div>
      <div v-if="refs.length" class="memory-event__group">
        <span>References</span>
        <article v-for="ref in refs.slice(0, 6)" :key="ref.id || ref.ref_key" class="memory-event__item">
          <strong>{{ ref.label || ref.ref_key }}</strong>
          <p>{{ ref.content }}</p>
          <small>{{ ref.confidence || 'indexed' }}</small>
        </article>
      </div>
    </div>
  </EventFrame>
</template>

<style scoped>
.memory-event {
  display: grid;
  gap: 12px;
}
.memory-event__status {
  display: grid;
  gap: 6px;
  padding: 9px 10px;
  border: 1px solid #d7e9e5;
  border-radius: 8px;
  background: linear-gradient(180deg, #fbfffe, #f4fbf9);
}
.memory-event__status > span {
  color: #0f4d46;
  font: 760 10px/1.2 var(--mono);
  text-transform: uppercase;
}
.memory-event__status small {
  color: #5d7580;
  font: 9px/1.2 var(--mono);
}
.memory-event__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.memory-event__chips b {
  padding: 3px 7px;
  border-radius: 999px;
  color: #1d5c53;
  background: rgba(47, 125, 115, .1);
  font: 700 9px/1 var(--mono);
}
.memory-event__group {
  display: grid;
  gap: 7px;
}
.memory-event__group > span {
  color: #667a93;
  font: 700 9px/1 var(--mono);
  text-transform: uppercase;
}
.memory-event__item {
  padding: 9px 10px;
  border: 1px solid #dbe6f2;
  border-radius: 8px;
  background: #fbfdff;
}
.memory-event__item strong {
  display: block;
  color: #0d2d5c;
  font: 700 10px/1.2 var(--mono);
}
.memory-event__item p {
  margin: 5px 0;
  color: #254466;
  font: 11px/1.45 var(--sans);
}
.memory-event__item small {
  color: #6c829d;
  font: 9px/1 var(--mono);
}
</style>
