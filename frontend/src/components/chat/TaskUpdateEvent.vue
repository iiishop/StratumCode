<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const changes = computed(() => {
  if (Array.isArray(props.event.changes) && props.event.changes.length) return props.event.changes
  return (props.event.items || []).map(item => ({ action: 'update', item }))
})

const counts = computed(() => changes.value.reduce((acc, change) => {
  acc[change.action] = (acc[change.action] || 0) + 1
  return acc
}, {}))

const detail = computed(() => {
  const parts = [
    counts.value.add && `${counts.value.add} added`,
    counts.value.status && `${counts.value.status} status`,
    counts.value.update && `${counts.value.update} updated`,
  ].filter(Boolean)
  return parts.join(' · ') || `${props.event.items?.length || 0} task items checked`
})

function actionLabel(change) {
  return {
    add: 'added',
    status: `${change.before?.status || 'unknown'} -> ${change.item?.status || 'updated'}`,
    update: 'updated',
  }[change.action] || change.item?.status || 'updated'
}
</script>

<template>
  <EventFrame
    kind="task-update"
    symbol="T"
    label="Task update"
    :detail="detail"
    status="updated"
    state="done"
  >
    <div class="task-update">
      <p
        v-for="change in changes"
        :key="`${change.action}:${change.item?.id || change.item?.text}`"
        :class="`is-${change.action}`"
      >
        <b>{{ actionLabel(change) }}</b>
        <span>
          <i v-if="change.item?.id">{{ change.item.id }}</i>
          {{ change.item?.text }}
        </span>
        <small v-if="change.item?.reason">{{ change.item.reason }}</small>
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
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 4px 8px;
  margin: 0;
  padding: 6px 8px;
  border: 1px solid rgba(17, 134, 111, .12);
  border-radius: 7px;
  color: var(--text, #3f5274);
  background: rgba(17, 134, 111, .035);
  font-size: 10px;
  line-height: 1.45;
}
.task-update b {
  color: #11866f;
  font: 800 8px/1 var(--mono, monospace);
  text-transform: uppercase;
}
.task-update span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.task-update i {
  display: inline-block;
  margin-right: 6px;
  color: var(--text-muted, #71809c);
  font: 800 8px/1 var(--mono, monospace);
  font-style: normal;
}
.task-update small {
  grid-column: 2;
  color: var(--text-muted, #71809c);
  font-size: 9px;
  line-height: 1.35;
}
.task-update p.is-add {
  border-color: rgba(23, 86, 209, .14);
  background: rgba(23, 86, 209, .04);
}
.task-update p.is-add b { color: #1756d1; }
.task-update p.is-status {
  border-color: rgba(196, 139, 0, .16);
  background: rgba(245, 200, 66, .08);
}
.task-update p.is-status b { color: #9f7000; }
</style>
