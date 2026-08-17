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
  overflow-x: auto;
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

.facts-md :deep(:not(pre) > code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(23, 86, 209, .07);
  color: var(--accent-text, #1748a3);
  font: .9em/1.5 var(--mono, monospace);
}

.facts-md :deep(pre) {
  margin: 8px 0;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid rgba(23, 86, 209, .1);
  border-radius: 7px;
  background: #f7f9fd;
  white-space: pre;
  font: 11px/1.55 var(--mono, monospace);
}

.facts-md :deep(h1),
.facts-md :deep(h2),
.facts-md :deep(h3),
.facts-md :deep(h4),
.facts-md :deep(h5),
.facts-md :deep(h6) {
  margin: 10px 0 5px;
  color: var(--text-h, #102a5c);
  font-weight: 650;
  line-height: 1.3;
}

.facts-md :deep(blockquote) {
  margin: 7px 0;
  padding: 5px 10px;
  border-left: 3px solid color-mix(in srgb, var(--accent, #1756d1) 30%, #d4e0f2);
  border-radius: 0 5px 5px 0;
  background: rgba(23, 86, 209, .03);
}

.facts-md :deep(table) {
  width: max-content;
  min-width: 100%;
  margin: 6px 0;
  border-collapse: collapse;
  font-size: 12px;
}

.facts-md :deep(th),
.facts-md :deep(td) {
  padding: 5px 8px;
  border-bottom: 1px solid var(--border, #d9e3f5);
}
</style>
