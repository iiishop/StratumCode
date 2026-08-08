<script setup>
import { computed } from 'vue'

const props = defineProps({ event: { type: Object, required: true } })

const submitted = computed(() => props.event.answer_status === 'submitted')
const answerText = computed(() => {
  if (!submitted.value) return ''
  return props.event.selected_option_label || props.event.response || ''
})
</script>

<template>
  <div class="qr" :class="{ 'is-pending': !submitted }">
    <div class="qr__head">
      <span class="qr__badge">{{ submitted ? 'Answered' : 'Question' }}</span>
      <span class="qr__question">{{ event.question || 'Agent asked for input' }}</span>
    </div>
    <div v-if="submitted" class="qr__answer">
      <span class="qr__answer-label">Your answer</span>
      <span class="qr__answer-text">{{ answerText }}</span>
    </div>
  </div>
</template>

<style scoped>
.qr {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-left-width: 3px;
  border-left-color: var(--accent);
  border-radius: var(--radius-sm);
  background: var(--bg-raised);
}

.qr.is-pending {
  border-left-color: var(--yellow);
}

.qr__head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.qr__badge {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 99px;
  color: #fff;
  background: var(--accent);
  font: 700 8px/1.4 var(--mono);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.qr.is-pending .qr__badge {
  background: var(--yellow);
  color: #5c4200;
}

.qr__question {
  min-width: 0;
  color: var(--text-h);
  font: 600 11px/1.45 var(--sans);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.qr__answer {
  display: flex;
  align-items: baseline;
  gap: 7px;
  min-width: 0;
}

.qr__answer-label {
  flex-shrink: 0;
  color: var(--text-muted);
  font: 8px/1 var(--mono);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.qr__answer-text {
  min-width: 0;
  color: var(--text);
  font: 11px/1.5 var(--sans);
  overflow-wrap: anywhere;
}
</style>
