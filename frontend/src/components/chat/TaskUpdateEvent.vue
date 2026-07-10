<script setup>
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })
</script>

<template>
  <EventFrame
    kind="task-update"
    symbol="T"
    label="Task update"
    :detail="`${event.items?.length || 0} task items updated`"
    status="updated"
    state="done"
  >
    <div class="task-update">
      <p v-for="item in event.items || []" :key="`${item.status}:${item.text}`">
        <b>{{ item.status }}</b>{{ item.text }}
      </p>
    </div>
  </EventFrame>
</template>

<style scoped>
.task-update {
  display: grid;
  gap: 5px;
}
.task-update p {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 10px;
  line-height: 1.45;
}
.task-update b {
  margin-right: 7px;
  color: #11866f;
  font: 800 8px/1 var(--mono, monospace);
  text-transform: uppercase;
}
</style>
