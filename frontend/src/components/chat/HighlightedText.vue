<script setup>
import { computed, inject } from 'vue'
import FileReference from '../FileReference.vue'

const props = defineProps({
  text: { type: String, default: '' },
  context: {
    type: String,
    default: 'prose',
    validator: value => ['prose', 'tool-data'].includes(value),
  },
  highlightTools: { type: Boolean, default: false },
})

const providedToolNames = inject('toolNames', null)
const fileExtensions = 'py|js|jsx|ts|tsx|vue|json|md|css|scss|html|yaml|yml|toml|sh|rs|go|java|kt|swift|c|cc|cpp|h|hpp'

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

const toolNames = computed(() => new Set(
  (providedToolNames?.value || []).map(name => String(name).toLowerCase()),
))

const tokenPattern = computed(() => {
  const tools = [...toolNames.value].map(escapeRegExp).join('|')
  const agentPattern = '(@[\\w-]+)'
  const filePattern = `((?:(?:[A-Za-z]:)?[\\w.-]*[\\\\/])?[\\w.-]+\\.(?:${fileExtensions}))`
  const codePattern = '(`[^`\\n]+`)'
  return new RegExp(`${agentPattern}|${filePattern}|${codePattern}${tools ? `|(\\b(?:${tools})\\b)` : ''}`, 'gi')
})

const parts = computed(() => {
  const result = []
  let cursor = 0

  for (const match of props.text.matchAll(tokenPattern.value)) {
    if (match.index > cursor) result.push({ type: 'text', value: props.text.slice(cursor, match.index) })
    const value = match[0]
    let type = 'code'
    if (value.startsWith('@')) type = props.context === 'prose' ? 'agent' : 'text'
    else if (match[2]) type = 'file'
    else if (match[4] && toolNames.value.has(value.toLowerCase())) type = props.context === 'prose' && props.highlightTools ? 'tool' : 'text'
    else if (props.context === 'tool-data') type = 'text'
    result.push({ type, value: value.replace(/^`|`$/g, '') })
    cursor = match.index + value.length
  }

  if (cursor < props.text.length) result.push({ type: 'text', value: props.text.slice(cursor) })
  return result
})
</script>

<template>
  <template v-for="(part, index) in parts" :key="index">
    <span v-if="part.type === 'text'">{{ part.value }}</span>
    <FileReference v-else-if="part.type === 'file'" :path="part.value" />
    <span v-else class="semantic-token" :class="`semantic-token--${part.type}`">
      <span v-if="part.type === 'tool'" class="semantic-token__mark">›_</span>
      <span v-else-if="part.type === 'agent'" class="semantic-token__mark">@</span>
      {{ part.type === 'agent' ? part.value.slice(1) : part.value }}
    </span>
  </template>
</template>

<style scoped>
.semantic-token {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: 4px;
  margin-inline: 2px;
  padding: 1px 5px;
  border: 1px solid transparent;
  border-radius: 5px;
  vertical-align: 1px;
  font: 650 .91em/1.45 var(--mono, monospace);
  white-space: nowrap;
}
.semantic-token--tool { border-color: #ead37d; color: #785b00; background: #fff8d9; }
.semantic-token--agent { border-color: #d2cdf3; color: #5548ae; background: #f2f0ff; }
.semantic-token--code { border-color: #d8e0ec; color: #42566f; background: #f1f4f8; }
.semantic-token__mark { color: currentColor; font-size: .85em; opacity: .72; }
</style>
