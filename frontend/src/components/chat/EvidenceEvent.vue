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
.evidence__claim {
  margin: 0 0 10px;
  padding: 6px 0 0;
  color: var(--text-h, #102a5c);
  font-size: 12.5px;
  line-height: 1.6;
  font-weight: 550;
  overflow-wrap: anywhere;
}

blockquote {
  margin: 0;
  padding: 10px 12px;
  border-left: 3px solid var(--event, #11866f);
  border-radius: 0 7px 7px 0;
  color: var(--text, #3f5274);
  background: color-mix(in srgb, var(--event, #11866f) 5%, #f9fbfe);
  font: 10.5px/1.6 var(--mono, monospace);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.evidence__source {
  display: block;
  margin-top: 9px;
  color: var(--text-muted, #71809c);
  font: 700 8.5px/1 var(--mono, monospace);
  letter-spacing: .05em;
  text-transform: uppercase;
  overflow-wrap: anywhere;
}
</style>
