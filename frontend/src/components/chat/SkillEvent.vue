<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })
const available = computed(() => Array.isArray(props.event.available) ? props.event.available : [])
const selected = computed(() => Array.isArray(props.event.selected) ? props.event.selected : [])
const choices = computed(() => Array.isArray(props.event.choices) ? props.event.choices : [])
const isSelection = computed(() => available.value.length > 0)
const isLoaded = computed(() => props.event.status === 'loaded')

const label = computed(() => {
  if (props.event.status === 'selecting') return `${props.event.name} · ${available.value.length} available`
  if (isSelection.value && props.event.status === 'done') return `Selection done · ${selected.value.length || 0} chosen`
  return props.event.name
})

const detail = computed(() => props.event.target_id || props.event.description || '')
</script>

<template>
  <EventFrame
    kind="skill"
    symbol="S"
    :label="label"
    :detail="detail"
    :status="event.status"
    :open="Boolean(event.open)"
    :collapsible="Boolean(available.length || event.path || event.reason)"
    @toggle="event.open = !event.open"
  >
    <ul v-if="available.length" class="skill-event__list">
      <li v-for="item in available" :key="item.name">
        <strong>{{ item.name }}</strong>
        <span>{{ item.description }}</span>
      </li>
    </ul>
    <p v-if="isSelection && event.status === 'done'" class="skill-event__summary">
      {{ selected.length ? `Selected: ${selected.join(', ')}` : 'No skill selected for this model step.' }}
    </p>
    <ul v-if="choices.length" class="skill-event__choices">
      <li v-for="item in choices" :key="item.name">
        <strong>{{ item.name }}</strong>
        <span>{{ item.reason }}</span>
      </li>
    </ul>
    <p v-if="event.reason && isLoaded" class="skill-event__meta"><span>Reason:</span> {{ event.reason }}</p>
    <p v-if="event.path" class="skill-event__meta"><span>Path:</span> {{ event.path }}</p>
  </EventFrame>
</template>

<style scoped>
.skill-event__list { display: grid; gap: 7px; margin: 0; padding: 2px 0; list-style: none; }
.skill-event__list li { display: grid; grid-template-columns: minmax(100px, 170px) minmax(0, 1fr); gap: 10px; }
.skill-event__list strong { color: var(--text-h); font: 600 10px var(--mono); }
.skill-event__list span { color: var(--text-muted); font: 10px/1.45 var(--mono); overflow-wrap: anywhere; }
.skill-event__choices { display: grid; gap: 5px; margin: 8px 0 0; padding: 0; list-style: none; }
.skill-event__choices li { display: grid; gap: 2px; }
.skill-event__choices strong { color: var(--text-h); font: 600 10px var(--mono); }
.skill-event__choices span { color: var(--text-muted); font: 10px/1.45 var(--mono); overflow-wrap: anywhere; }
.skill-event__summary { margin: 8px 0 0; color: var(--text-muted); font: 10px/1.45 var(--mono); overflow-wrap: anywhere; }
.skill-event__meta { margin: 6px 0 0; color: var(--text-muted); font: 10px/1.45 var(--mono); overflow-wrap: anywhere; }
.skill-event__meta span { color: var(--text-h); font-weight: 600; }
</style>
