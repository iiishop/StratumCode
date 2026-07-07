<script setup>
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })
</script>

<template>
  <EventFrame kind="usage" symbol="¥" label="Token usage" status="metered" state="done">
    <div class="usage">
      <span>↑ {{ event.delta?.input_tokens || 0 }}</span>
      <span>↓ {{ event.delta?.output_tokens || 0 }}</span>
      <span>cache {{ event.delta?.cached_tokens || 0 }}</span>
      <strong>{{ event.total?.currency || event.delta?.currency }} {{ (event.total?.cost || 0).toFixed(6) }}</strong>
    </div>
  </EventFrame>
</template>

<style scoped>
.usage { display: flex; flex-wrap: wrap; gap: 8px; color: #5f7193; font: 10px/1.4 var(--mono); }
.usage strong { color: #1756d1; }
</style>
