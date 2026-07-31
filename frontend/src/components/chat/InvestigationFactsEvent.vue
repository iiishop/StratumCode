<script setup>
import { ref } from 'vue'
import { renderMarkdown } from '../../lib/markdown'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const open = ref(false)
const parts = renderMarkdown(props.event.content || '')
</script>

<template>
  <EventFrame kind="facts" label="Investigation facts" symbol="&#10003;" collapsible :open="open" @toggle="open = !open">
    <div class="facts-note">
      Verified facts collected across all investigation rounds.
    </div>
    <template v-for="(part, index) in parts" :key="index">
      <pre v-if="part.type === 'code'"><code>{{ part.content }}</code></pre>
      <div v-else class="facts-md" v-html="part.html"></div>
    </template>
  </EventFrame>
</template>

<style scoped>
.facts-note {
  margin-bottom: 8px;
  color: var(--text-muted, #71809c);
  font-size: 12px;
}

.facts-md {
  color: var(--text, #3f5274);
  font-size: var(--font-body, 14px);
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.facts-md :deep(p) {
  margin: 0 0 6px;
}

.facts-md :deep(ul),
.facts-md :deep(ol) {
  margin: 2px 0 8px;
  padding-left: 20px;
}

.facts-md :deep(li) {
  margin-bottom: 2px;
}

.facts-md :deep(li::marker) {
  color: var(--text-muted, #71809c);
}

.facts-md :deep(strong) {
  color: var(--text-h, #102a5c);
  font-weight: 650;
}

.facts-md :deep(.md-inline) {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(23, 86, 209, .07);
  color: var(--accent-text, #1748a3);
  font: .9em/1.5 var(--mono, monospace);
}
</style>
