<script setup>
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

function fmt(value) {
  return Number(value || 0).toLocaleString()
}

function cost(value) {
  return Number(value || 0).toFixed(6)
}

const currency = props.event.total?.currency || props.event.delta?.currency || 'USD'
</script>

<template>
  <EventFrame
    kind="usage"
    symbol="¥"
    label="Token usage"
    :detail="`${fmt(event.delta?.total_tokens || 0)} tokens  ·  ${currency} ${cost(event.delta?.cost || 0)}`"
    status="metered"
    state="done"
  >
    <div class="usage">
      <span class="usage__col"><small>input</small><b>{{ fmt(event.delta?.input_tokens) }}</b></span>
      <span class="usage__col"><small>output</small><b>{{ fmt(event.delta?.output_tokens) }}</b></span>
      <span class="usage__col"><small>cache</small><b>{{ fmt(event.delta?.cached_tokens) }}</b></span>
      <span class="usage__col usage__col--cost"><small>cost</small><b>{{ cost(event.delta?.cost) }}</b></span>
    </div>
  </EventFrame>
</template>

<style scoped>
.usage {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
}

.usage__col {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 54px;
}

.usage__col small {
  color: var(--text-muted, #71809c);
  font: 8px/1 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .05em;
}

.usage__col b {
  color: var(--text-h, #102a5c);
  font: 700 11px/1.3 var(--mono, monospace);
}

.usage__col--cost b {
  color: var(--accent-text, #1748a3);
}
</style>
