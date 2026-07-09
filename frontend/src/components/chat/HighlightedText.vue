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

function looksLikeURL(text, matchIndex, matchLength) {
  const charBefore = text[matchIndex - 1] || ''
  const after = text.slice(matchIndex + matchLength)
  // domain segment: preceded by dot, followed by path-like continuation
  if (charBefore === '.' && /^\w+(?:\/|#|\?|$)/.test(after)) return true
  // URL path segment: preceded by / or ., protocol somewhere before
  if (/[/.]/.test(charBefore)) {
    const beforeAll = text.slice(0, matchIndex)
    if (/https?:\/\/|ftp:\/\//.test(beforeAll)) return true
  }
  return false
}

const toolNames = computed(() => new Set(
  (providedToolNames?.value || []).map(name => String(name).toLowerCase()),
))

const tokenPattern = computed(() => {
  const tools = [...toolNames.value].map(escapeRegExp).join('|')
  const agentPattern = '(@[\\w-]+)'
  const filePattern = `((?:(?:[A-Za-z]:)?[\\w.-]*[\\\\/])?[\\w-]+\\.(?:${fileExtensions}))`
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
    else if (match[2]) type = looksLikeURL(props.text, match.index, value.length) ? 'text' : 'file'
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
.semantic-token--tool { border-color: rgba(245, 200, 66, .4); color: #5c4200; background: rgba(245, 200, 66, .14); }
.semantic-token--agent { border-color: rgba(102, 88, 199, .3); color: #4c3fc3; background: rgba(102, 88, 199, .08); }
.semantic-token--code { border-color: rgba(23, 86, 209, .15); color: var(--text, #3f5274); background: rgba(23, 86, 209, .05); }
.semantic-token__mark { color: currentColor; font-size: .85em; opacity: .72; }
</style>
