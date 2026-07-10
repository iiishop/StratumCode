<script setup>
import { computed, ref } from 'vue'
import { highlightCode } from '../../lib/highlight'
import { renderMarkdown } from '../../lib/markdown'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const copied = ref(false)
let copyTimer

async function copyContent() {
  try {
    await navigator.clipboard.writeText(props.event.content || '')
    copied.value = true
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = false }, 1600)
  } catch { /* clipboard denied */ }
}

const parts = computed(() => renderMarkdown(props.event.content))
</script>

<template>
  <EventFrame kind="output" symbol="&rsaquo;" label="Response" :status="event.streaming ? 'writing' : 'done'">
    <div class="output-copy">
      <button class="output-copy-btn" :class="{ copied }" @click.stop="copyContent" :aria-label="copied ? 'Copied' : 'Copy'">
        <svg v-if="!copied" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6 9 17l-5-5"/></svg>
      </button>
      <template v-for="(part, index) in parts" :key="index">
        <pre v-if="part.type === 'code'"><code v-html="highlightCode(part.content, part.lang)"></code></pre>
        <div v-else class="output-md" v-html="part.html"></div>
      </template>
      <span v-if="event.streaming" class="output-cursor"></span>
    </div>
  </EventFrame>
</template>

<style scoped>
.output-copy {
  position: relative;
  padding: 4px 0;
  color: var(--text-h, #102a5c);
  font-size: var(--font-body, 14px);
  line-height: 1.72;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.output-copy-btn {
  position: absolute;
  top: 0;
  right: 0;
  display: flex; align-items: center; justify-content: center;
  width: 28px; height: 28px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 7px;
  color: var(--text-muted, #71809c);
  background: #fff;
  cursor: pointer;
  opacity: 0;
  transition: opacity .15s, color .15s, border-color .15s;
  z-index: 2;
}

.output-copy:hover .output-copy-btn {
  opacity: 1;
}

.output-copy-btn:hover {
  border-color: var(--accent-border, rgba(23,86,209,.32));
  color: var(--accent-text, #1748a3);
}

.output-copy-btn.copied {
  opacity: 1;
  border-color: #00a878;
  color: #00a878;
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

.output-md {
  white-space: normal;
}

.output-md :deep(p) {
  margin: 0 0 8px;
}

.output-md :deep(p:last-child) {
  margin-bottom: 0;
}

.output-md :deep(h2),
.output-md :deep(h3),
.output-md :deep(h4) {
  margin: 14px 0 6px;
  color: var(--text-h, #102a5c);
  font-weight: 650;
  line-height: 1.3;
}

.output-md :deep(h2) { font-size: 16px; }
.output-md :deep(h3) { font-size: 14.5px; }
.output-md :deep(h4) { font-size: 13.5px; }

.output-md :deep(strong) {
  color: var(--text-h, #102a5c);
  font-weight: 650;
}

.output-md :deep(em) {
  font-style: italic;
  color: var(--text, #3f5274);
}

.output-md :deep(.md-inline) {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(23, 86, 209, .07);
  color: var(--accent-text, #1748a3);
  font: .9em/1.5 var(--mono, monospace);
}

.output-md :deep(a) {
  color: var(--accent-text, #1748a3);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.output-md :deep(ul),
.output-md :deep(ol) {
  margin: 4px 0 8px;
  padding-left: 20px;
}

.output-md :deep(li) {
  margin-bottom: 2px;
}

.output-md :deep(li::marker) {
  color: var(--text-muted, #71809c);
}

.output-md :deep(blockquote) {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid color-mix(in srgb, var(--accent, #1756d1) 30%, #d4e0f2);
  border-radius: 0 6px 6px 0;
  background: rgba(23, 86, 209, .03);
  color: var(--text, #3f5274);
}

.output-md :deep(blockquote p) {
  margin: 0;
}

.output-md :deep(table) {
  width: 100%;
  margin: 8px 0;
  border-collapse: collapse;
  font-size: 12.5px;
  line-height: 1.55;
}

.output-md :deep(th) {
  padding: 7px 10px;
  border-bottom: 2px solid var(--border, #d9e3f5);
  color: var(--text-h, #102a5c);
  font-weight: 650;
  text-align: left;
  background: rgba(23, 86, 209, .04);
}

.output-md :deep(td) {
  padding: 6px 10px;
  border-bottom: 1px solid var(--border, #d9e3f5);
  color: var(--text, #3f5274);
}

.output-md :deep(tr:last-child td) {
  border-bottom: 0;
}

.output-md :deep(br) {
  display: block;
  content: '';
  margin-top: 4px;
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
