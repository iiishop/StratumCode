<script setup>
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })
</script>

<template>
  <EventFrame
    kind="step-result"
    :state="event.next_step"
    symbol="S"
    label="Step result"
    :detail="event.continue_reason"
    :status="event.next_step"
  >
    <div v-if="event.target_unknown_ids?.length" class="step-result__targets">
      <small>target unknowns</small>
      <span v-for="id in event.target_unknown_ids" :key="id">{{ id }}</span>
    </div>
  </EventFrame>
</template>

<style scoped>
.step-result__targets {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.step-result__targets small {
  color: #71809c;
  font: 700 9px/1 var(--mono);
  text-transform: uppercase;
}
.step-result__targets span {
  padding: 3px 6px;
  border: 1px solid #d8e2ef;
  border-radius: 5px;
  background: #fff;
  font: 10px/1 var(--mono);
}
</style>
