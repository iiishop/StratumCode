<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })
const available = computed(() => Array.isArray(props.event.available) ? props.event.available : [])
const selection = computed(() => Boolean(available.value.length))
const selected = computed(() => Array.isArray(props.event.selected) ? props.event.selected : [])
</script>

<template>
  <EventFrame
    kind="skill"
    symbol="S"
    :label="selection ? event.name : `Loaded skill: ${event.name}`"
    :detail="event.description || event.target_id"
    :status="event.status"
    :open="Boolean(event.open)"
    :collapsible="Boolean(available.length || event.path)"
    @toggle="event.open = !event.open"
  >
    <ul v-if="available.length" class="skill-event__list">
      <li v-for="item in available" :key="item.name">
        <strong>{{ item.name }}</strong>
        <span>{{ item.description }}</span>
      </li>
    </ul>
    <p v-if="selection && event.status === 'done'" class="skill-event__path">
      {{ selected.length ? `Selected: ${selected.join(', ')}` : 'No skill selected for this model step.' }}
    </p>
    <p v-if="event.path" class="skill-event__path">{{ event.path }}</p>
  </EventFrame>
</template>

<style scoped>
.skill-event__list { display: grid; gap: 7px; margin: 0; padding: 2px 0; list-style: none; }
.skill-event__list li { display: grid; grid-template-columns: minmax(100px, 170px) minmax(0, 1fr); gap: 10px; }
.skill-event__list strong { color: var(--text-h); font: 600 10px var(--mono); }
.skill-event__list span, .skill-event__path { margin: 0; color: var(--text-muted); font: 10px/1.45 var(--mono); overflow-wrap: anywhere; }
</style>
