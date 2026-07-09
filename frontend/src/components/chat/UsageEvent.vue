<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

function fmt(value) {
  return Number(value || 0).toLocaleString()
}

function cost(value) {
  return Number(value || 0).toFixed(6)
}

const currency = computed(() => props.event.total?.currency || props.event.delta?.currency || 'USD')

const detail = computed(() => {
  const total = props.event.total
  if (!total) return ''
  return `Session: ${fmt(total.total_tokens)} tokens  ·  ${currency.value} ${cost(total.cost)}`
})
</script>

<template>
  <EventFrame
    kind="usage"
    symbol="¥"
    label="Token usage"
    :detail="detail"
    status="metered"
    state="done"
    open
  >
    <div class="usage">
      <div class="usage__row">
        <span class="usage__tag">call</span>
        <div class="usage__cols">
          <span class="usage__col"><small>input</small><b>{{ fmt(event.delta?.input_tokens) }}</b></span>
          <span class="usage__col"><small>output</small><b>{{ fmt(event.delta?.output_tokens) }}</b></span>
          <span class="usage__col"><small>cache</small><b>{{ fmt(event.delta?.cached_tokens) }}</b></span>
          <span class="usage__col usage__col--cost"><small>cost</small><b>{{ cost(event.delta?.cost) }}</b></span>
        </div>
      </div>
      <div class="usage__row usage__row--total">
        <span class="usage__tag">total</span>
        <div class="usage__cols">
          <span class="usage__col"><small>input</small><b>{{ fmt(event.total?.input_tokens) }}</b></span>
          <span class="usage__col"><small>output</small><b>{{ fmt(event.total?.output_tokens) }}</b></span>
          <span class="usage__col"><small>cache</small><b>{{ fmt(event.total?.cached_tokens) }}</b></span>
          <span class="usage__col usage__col--cost"><small>cost</small><b>{{ cost(event.total?.cost) }}</b></span>
        </div>
      </div>
    </div>
  </EventFrame>
</template>

<style scoped>
.usage {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.usage__row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.usage__row--total {
  padding-top: 10px;
  border-top: 1px solid color-mix(in srgb, var(--event, #7c8ba0) 18%, transparent);
}

.usage__tag {
  flex-shrink: 0;
  min-width: 32px;
  padding: 3px 7px;
  border-radius: 5px;
  color: var(--event, #7c8ba0);
  background: color-mix(in srgb, var(--event, #7c8ba0) 9%, transparent);
  font: 700 8px/1.3 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .06em;
  text-align: center;
}

.usage__row--total .usage__tag {
  color: var(--accent-text, #1748a3);
  background: rgba(23, 86, 209, .09);
}

.usage__cols {
  display: flex;
  flex: 1;
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
