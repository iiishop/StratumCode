<script setup>
import { ref } from 'vue'
import EventFrame from './EventFrame.vue'
defineProps({ event: { type: Object, required: true } })
const open = ref(true)
</script>

<template>
  <EventFrame
    kind="evidence"
    :state="event.stance"
    :symbol="event.stance === 'support' ? '+' : '−'"
    :label="event.stance === 'support' ? 'Supporting evidence' : 'Opposing evidence'"
    :detail="event.source_uri"
    :status="`${Math.round(event.strength * 100)}% weight`"
    :open="open"
    collapsible
    @toggle="open = !open"
  >
    <p class="evidence__claim">{{ event.claim }}</p>
    <blockquote>{{ event.excerpt }}</blockquote>
    <span class="evidence__source">{{ event.source_type }} · {{ event.id }}</span>
  </EventFrame>
</template>

<style scoped>
.evidence__claim { margin: 0 0 8px; color: #233d5c; font-size: 12px; line-height: 1.55; }
blockquote { margin: 0; padding: 8px 10px; border-left: 2px solid #8ca7cc; border-radius: 0 7px 7px 0; color: #536a86; background: rgba(235, 242, 252, .75); font: 10.5px/1.55 var(--mono); white-space: pre-wrap; }
.evidence__source { display: block; margin-top: 7px; color: #8a9bb1; font: 700 9px/1 var(--mono); text-transform: uppercase; }
</style>
