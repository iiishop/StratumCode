<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const labels = {
  support: 'Support evidence',
  oppose: 'Opposing evidence',
  audit: 'Audit evidence',
  evaluate: 'Evaluate verdict',
}

const progress = computed(() => Array.isArray(props.event.progress) ? props.event.progress : [])
const current = computed(() => (
  [...progress.value].reverse().find(item => item.state === 'running')
  || progress.value.at(-1)
))
const effortBits = computed(() => [
  props.event.effort,
  props.event.risk ? `${props.event.risk} risk` : '',
  props.event.quality_gate,
].filter(Boolean))
const detail = computed(() => {
  if (!progress.value.length) {
    return [
      labels[props.event.phase] || props.event.phase || 'starting',
      props.event.provider,
      props.event.model,
      ...effortBits.value,
    ].filter(Boolean).join(' · ')
  }
  const completed = progress.value.filter(item => item.state === 'done').length
  const suffix = current.value?.detail ? ` · ${current.value.detail}` : ''
  const effort = effortBits.value.length ? ` · ${effortBits.value.join(' · ')}` : ''
  return `${completed}/${progress.value.length} · ${current.value?.label || 'Preparing'}${suffix}${effort}`
})
const open = computed(() => props.event.open ?? progress.value.length > 0)
</script>

<template>
  <EventFrame
    kind="stage"
    symbol="S"
    :label="event.label || 'Agent stage'"
    :detail="detail"
    :status="event.state"
    :state="event.state"
    :open="open"
    :collapsible="progress.length > 0"
    @toggle="event.open = !open"
  >
    <ol v-if="progress.length" class="stage-progress" aria-live="polite">
      <li
        v-for="item in progress"
        :key="item.id"
        class="stage-progress__item"
        :class="`is-${item.state || 'pending'}`"
      >
        <span class="stage-progress__marker" aria-hidden="true">
          {{ item.state === 'done' ? '✓' : item.state === 'error' ? '!' : '' }}
        </span>
        <span class="stage-progress__content">
          <strong>{{ item.label }}</strong>
          <small v-if="item.description">{{ item.description }}</small>
        </span>
        <span v-if="item.detail" class="stage-progress__detail">{{ item.detail }}</span>
      </li>
    </ol>
  </EventFrame>
</template>

<style scoped>
.stage-progress {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 2px 0 0;
  list-style: none;
}

.stage-progress__item {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  min-height: 42px;
  align-items: center;
  gap: 9px;
  padding: 7px 0;
  border-top: 1px solid #e8eef7;
}

.stage-progress__marker {
  display: grid;
  width: 16px;
  height: 16px;
  place-items: center;
  border: 1.5px solid #b9c7dc;
  border-radius: 50%;
  color: #fff;
  font: 800 10px/1 var(--mono, monospace);
}

.stage-progress__item.is-running .stage-progress__marker {
  border-color: #c48b00;
  border-top-color: transparent;
  animation: stage-progress-spin .8s linear infinite;
}

.stage-progress__item.is-done .stage-progress__marker {
  border-color: #0f7d65;
  background: #0f7d65;
}

.stage-progress__item.is-error .stage-progress__marker {
  border-color: #c44747;
  background: #c44747;
}

.stage-progress__content {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.stage-progress__content strong {
  overflow: hidden;
  color: var(--text-h, #102a5c);
  font-size: 11px;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-progress__content small {
  overflow: hidden;
  color: var(--text-muted, #71809c);
  font-size: 10px;
  line-height: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-progress__detail {
  max-width: 150px;
  overflow: hidden;
  color: #53627b;
  font: 650 9px/1.4 var(--mono, monospace);
  text-overflow: ellipsis;
  white-space: nowrap;
}

@keyframes stage-progress-spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 640px) {
  .stage-progress__item {
    grid-template-columns: 18px minmax(0, 1fr);
  }
  .stage-progress__detail {
    grid-column: 2;
    max-width: 100%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .stage-progress__item.is-running .stage-progress__marker { animation: none; }
}
</style>
