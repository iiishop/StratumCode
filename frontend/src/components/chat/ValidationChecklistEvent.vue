<script setup>
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const items = props.event.items || []
const total = props.event.total || items.length
</script>

<template>
  <EventFrame kind="stage" symbol="V" label="Verification checklist" :detail="`${items.length} items`" open>
    <ul class="checklist">
      <li v-for="(item, idx) in items" :key="idx" class="checklist__item">
        <span class="checklist__marker">{{ idx + 1 }}</span>
        <span class="checklist__step">{{ item.step_id }}</span>
        <span class="checklist__check">{{ item.check }}</span>
      </li>
      <li v-if="!items.length" class="checklist__empty">
        No completion conditions defined in patch plan.
      </li>
    </ul>
  </EventFrame>
</template>

<style scoped>
.checklist {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.checklist__item {
  display: grid;
  grid-template-columns: 20px 56px minmax(0, 1fr);
  align-items: baseline;
  gap: 8px;
  padding: 7px 0;
  border-top: 1px solid #e8eef7;
  font-size: 11px;
  line-height: 1.5;
}

.checklist__marker {
  display: grid;
  width: 18px;
  height: 18px;
  place-items: center;
  border: 1.5px solid #b9c7dc;
  border-radius: 4px;
  color: #53627b;
  font: 700 9px/1 var(--mono, monospace);
  background: #f9fbfe;
  flex-shrink: 0;
}

.checklist__step {
  color: var(--text-muted, #71809c);
  font: 650 10px/1.4 var(--mono, monospace);
  white-space: nowrap;
}

.checklist__check {
  color: var(--text-h, #102a5c);
  font-size: 11px;
  min-width: 0;
  word-break: break-word;
}

.checklist__empty {
  padding: 12px 0;
  color: var(--text-muted, #71809c);
  font-size: 11px;
  font-style: italic;
}
</style>
