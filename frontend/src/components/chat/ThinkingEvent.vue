<script setup>
import { computed } from 'vue'
import { highlightCode } from '../../lib/highlight'
import { renderMarkdown } from '../../lib/markdown'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const parts = computed(() => renderMarkdown(props.event.text))
const toolCalls = computed(() => parseToolCallReasoning(props.event.text || ''))

function parseToolCallReasoning(text) {
  const lines = String(text || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean)
  if (lines[0] !== 'Calling tools:') return []
  const calls = lines.slice(1).map(parseToolCallLine)
  return calls.length && calls.every(Boolean) ? calls : []
}

function parseToolCallLine(line) {
  const text = line.replace(/^[-*]\s+/, '')
  const match = text.match(/^([A-Za-z_][\w-]*)(?:\((.*?)\))?(?:\s+for\s+([^:]+))?(?::\s*(.*))?$/)
  if (!match) return null
  return {
    tool: match[1],
    subject: match[2] || '',
    targets: (match[3] || '').split(',').map(item => item.trim()).filter(Boolean),
    reason: match[4] || '',
  }
}
</script>

<template>
  <EventFrame
    kind="thinking"
    symbol="···"
    :label="event.done ? 'Reasoning complete' : 'Reasoning'"
    :status="event.done ? 'done' : 'running'"
    :open="event.open"
    collapsible
    @toggle="event.open = !event.open"
  >
    <div v-if="toolCalls.length" class="tool-reasoning">
      <div class="tool-reasoning__head">
        <span>Tool calls</span>
        <small>{{ toolCalls.length }}</small>
      </div>
      <div class="tool-reasoning__list">
        <div v-for="(call, index) in toolCalls" :key="index" class="tool-reasoning__item">
          <div class="tool-reasoning__main">
            <span class="tool-reasoning__tool">{{ call.tool }}</span>
            <code v-if="call.subject">{{ call.subject }}</code>
          </div>
          <div v-if="call.reason" class="tool-reasoning__reason">{{ call.reason }}</div>
          <div v-if="call.targets.length" class="tool-reasoning__targets">
            <span v-for="target in call.targets" :key="target">{{ target }}</span>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="thinking-copy">
      <template v-for="(part, index) in parts" :key="index">
        <pre v-if="part.type === 'code'"><code v-html="highlightCode(part.content, part.lang)"></code></pre>
        <div v-else class="thinking-md" v-html="part.html"></div>
      </template>
    </div>
  </EventFrame>
</template>

<style scoped>
.thinking-copy {
  padding: 4px 0 0;
  color: var(--text, #3f5274);
  font-size: 13px;
  line-height: 1.68;
  overflow-wrap: anywhere;
}

.thinking-copy pre {
  margin: 8px 0;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid rgba(23, 86, 209, .1);
  border-radius: 7px;
  background: #f7f9fd;
  white-space: pre;
  font: 11px/1.55 var(--mono, monospace);
}

.thinking-md { white-space: normal; }
.thinking-md :deep(p) { margin: 0 0 6px; }
.thinking-md :deep(p:last-child) { margin-bottom: 0; }
.thinking-md :deep(h2),
.thinking-md :deep(h3),
.thinking-md :deep(h4) { margin: 10px 0 4px; color: var(--text-h, #102a5c); font-weight: 650; line-height: 1.3; }
.thinking-md :deep(h2) { font-size: 14px; }
.thinking-md :deep(h3) { font-size: 13px; }
.thinking-md :deep(strong) { color: var(--text-h, #102a5c); font-weight: 650; }
.thinking-md :deep(em) { font-style: italic; }
.thinking-md :deep(.md-inline) { padding: 1px 4px; border-radius: 3px; background: rgba(23, 86, 209, .07); color: var(--accent-text, #1748a3); font: .9em/1.4 var(--mono, monospace); }
.thinking-md :deep(a) { color: var(--accent-text, #1748a3); text-decoration: underline; text-underline-offset: 2px; }
.thinking-md :deep(ul),
.thinking-md :deep(ol) { margin: 3px 0 6px; padding-left: 18px; }
.thinking-md :deep(li) { margin-bottom: 1px; }
.thinking-md :deep(li::marker) { color: var(--text-muted, #71809c); }
.thinking-md :deep(blockquote) { margin: 6px 0; padding: 4px 10px; border-left: 3px solid color-mix(in srgb, var(--accent, #1756d1) 30%, #d4e0f2); border-radius: 0 5px 5px 0; background: rgba(23, 86, 209, .03); }
.thinking-md :deep(blockquote p) { margin: 0; }
.thinking-md :deep(table) { width: 100%; margin: 6px 0; border-collapse: collapse; font-size: 11.5px; line-height: 1.5; }
.thinking-md :deep(th) { padding: 5px 8px; border-bottom: 2px solid var(--border, #d9e3f5); text-align: left; background: rgba(23, 86, 209, .04); }
.thinking-md :deep(td) { padding: 4px 8px; border-bottom: 1px solid var(--border, #d9e3f5); }

.tool-reasoning {
  display: grid;
  gap: 8px;
  padding-top: 4px;
}

.tool-reasoning__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--text-muted, #71809c);
  font: 10px/1.2 var(--mono, monospace);
}

.tool-reasoning__head span {
  color: var(--text-h, #102a5c);
  font-weight: 700;
}

.tool-reasoning__list {
  display: grid;
  gap: 6px;
}

.tool-reasoning__item {
  display: grid;
  gap: 5px;
  padding: 8px 10px;
  border: 1px solid rgba(23, 86, 209, .12);
  border-radius: 7px;
  background: #f8fbff;
}

.tool-reasoning__main {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.tool-reasoning__tool {
  flex-shrink: 0;
  padding: 2px 6px;
  border-radius: 5px;
  color: #1748a3;
  background: rgba(23, 86, 209, .08);
  font: 700 10px/1.35 var(--mono, monospace);
}

.tool-reasoning__main code {
  min-width: 0;
  overflow: hidden;
  color: var(--text, #3f5274);
  text-overflow: ellipsis;
  white-space: nowrap;
  font: 10px/1.4 var(--mono, monospace);
}

.tool-reasoning__reason {
  color: var(--text-h, #102a5c);
  font-size: 12px;
  line-height: 1.45;
}

.tool-reasoning__targets {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tool-reasoning__targets span {
  padding: 1px 5px;
  border-radius: 4px;
  color: #7a5a00;
  background: rgba(245, 200, 66, .22);
  font: 9px/1.45 var(--mono, monospace);
}
</style>
