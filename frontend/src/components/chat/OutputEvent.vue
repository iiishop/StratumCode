<script setup>
import { computed } from 'vue'
import { highlightCode } from '../../lib/highlight'
import EventFrame from './EventFrame.vue'
import HighlightedText from './HighlightedText.vue'

const props = defineProps({ event: { type: Object, required: true } })
const parts = computed(() => {
  const result = [], regex = /```(\w*)\n([\s\S]*?)```/g
  let last = 0, match
  while ((match = regex.exec(props.event.content)) !== null) {
    if (match.index > last) result.push({ type: 'text', content: props.event.content.slice(last, match.index) })
    result.push({ type: 'code', lang: match[1] || 'plaintext', content: match[2].trim() })
    last = match.index + match[0].length
  }
  if (last < props.event.content.length) result.push({ type: 'text', content: props.event.content.slice(last) })
  return result
})
</script>

<template>
  <EventFrame kind="output" symbol="›" label="Response" :status="event.streaming ? 'writing' : 'done'">
    <div class="output-copy">
      <template v-for="(part, index) in parts" :key="index">
        <pre v-if="part.type === 'code'"><code v-html="highlightCode(part.content, part.lang)"></code></pre>
        <span v-else><HighlightedText :text="part.content" /></span>
      </template>
      <span v-if="event.streaming" class="output-cursor"></span>
    </div>
  </EventFrame>
</template>

<style scoped>
.output-copy {
  padding: 4px 0;
  color: var(--text-h, #102a5c);
  font-size: var(--font-body, 14px);
  line-height: 1.72;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.output-copy pre {
  margin: 10px 0;
  padding: 12px 14px;
  overflow: auto;
  border: 1px solid rgba(23, 86, 209, .1);
  border-radius: 9px;
  background: #f7f9fd;
  white-space: pre;
  font: var(--font-code, 12px)/1.6 var(--mono, monospace);
}

.output-cursor {
  display: inline-block;
  width: 5px;
  height: 1em;
  margin-left: 2px;
  vertical-align: -2px;
  border-radius: 2px;
  background: var(--accent, #1756d1);
  animation: blink .75s step-end infinite;
}

@keyframes blink { 50% { opacity: 0; } }
</style>
