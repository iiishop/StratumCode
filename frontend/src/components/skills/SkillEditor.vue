<script setup>
import { computed, nextTick, ref } from 'vue'
import { renderMarkdown } from '../../lib/markdown'

const props = defineProps({
  modelValue: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

const editingIdx = ref(-1)
const editingMeta = ref(false)
const editText = ref('')
const metaEditText = ref('')

function parseMeta(raw) {
  if (!raw || !raw.startsWith('---')) return null
  const lines = raw.split(/\r?\n/)
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === '---')
  if (end === -1) return null
  return {
    meta: parseYamlBlock(lines.slice(1, end), 0, 0).value,
    body: lines.slice(end + 1).join('\n').trim(),
  }
}

function parseYamlBlock(lines, start, indent) {
  let index = start
  while (index < lines.length && !lines[index].trim()) index++
  const isList = index < lines.length && countIndent(lines[index]) === indent && lines[index].trim().startsWith('- ')
  const value = isList ? [] : {}

  while (index < lines.length) {
    const line = lines[index]
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) {
      index++
      continue
    }

    const currentIndent = countIndent(line)
    if (currentIndent < indent) break
    if (currentIndent > indent) {
      index++
      continue
    }

    if (isList) {
      if (!trimmed.startsWith('- ')) break
      const rest = trimmed.slice(2).trim()
      if (!rest) {
        const child = parseYamlBlock(lines, index + 1, nextIndent(lines, index + 1))
        value.push(child.value)
        index = child.index
      } else {
        value.push(parseScalar(rest))
        index++
      }
      continue
    }

    const match = trimmed.match(/^([^:#][^:]*):\s*(.*)$/)
    if (!match) {
      index++
      continue
    }

    const key = match[1].trim()
    const rest = match[2].trim()
    if (!rest) {
      const childIndent = nextIndent(lines, index + 1)
      if (childIndent > currentIndent) {
        const child = parseYamlBlock(lines, index + 1, childIndent)
        value[key] = child.value
        index = child.index
      } else {
        value[key] = ''
        index++
      }
    } else {
      value[key] = parseScalar(rest)
      index++
    }
  }

  return { value, index }
}

function countIndent(line) {
  return line.match(/^(\s*)/)?.[1].length || 0
}

function nextIndent(lines, start) {
  for (let index = start; index < lines.length; index++) {
    if (lines[index].trim()) return countIndent(lines[index])
  }
  return 0
}

function parseScalar(value) {
  const raw = value.replace(/\s+#.*$/, '').trim()
  if (raw.startsWith('[') && raw.endsWith(']')) {
    return raw.slice(1, -1).split(',').map(item => unquote(item.trim())).filter(Boolean)
  }
  if (raw === 'true') return true
  if (raw === 'false') return false
  if (raw === 'null') return null
  return unquote(raw)
}

function unquote(value) {
  return value.replace(/^['"]|['"]$/g, '')
}

function humanize(value) {
  return String(value).replace(/[-_]/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}

function formatValue(value) {
  if (Array.isArray(value)) return value.map(formatValue).join(', ')
  if (value && typeof value === 'object') return Object.entries(value).map(([key, val]) => `${humanize(key)}: ${formatValue(val)}`).join('; ')
  return String(value ?? '')
}

function splitBlocks(raw) {
  if (!raw) return []
  const rawBlocks = []
  let buffer = ''
  let inFence = false

  for (const line of raw.split('\n')) {
    const trimmed = line.trim()
    if (trimmed.startsWith('```')) {
      if (inFence) {
        buffer += '\n' + line
        rawBlocks.push(buffer)
        buffer = ''
        inFence = false
      } else {
        if (buffer.trim()) rawBlocks.push(buffer)
        buffer = line
        inFence = true
      }
      continue
    }
    if (inFence) {
      buffer += '\n' + line
      continue
    }
    if (!trimmed) {
      if (buffer.trim()) rawBlocks.push(buffer)
      buffer = ''
      continue
    }
    buffer += (buffer ? '\n' : '') + line
  }
  if (buffer.trim()) rawBlocks.push(buffer)

  const merged = []
  for (const block of rawBlocks) {
    const kind = listKindOf(block)
    if (merged.length && kind && kind === listKindOf(merged[merged.length - 1])) merged[merged.length - 1] += '\n' + block
    else merged.push(block)
  }
  return merged
}

function listKindOf(block) {
  const first = block.trim().split('\n')[0] || ''
  if (/^[-*]\s/.test(first)) return 'ul'
  if (/^\d+\.\s/.test(first)) return 'ol'
  return ''
}

function renderHTML(raw) {
  const trimmed = raw.trim()
  if (trimmed.startsWith('```')) {
    const lines = trimmed.split('\n')
    const code = lines.slice(1, lines[lines.length - 1] === '```' ? -1 : lines.length).join('\n') || ''
    return `<pre class="se-code"><code>${esc(code)}</code></pre>`
  }
  return renderMarkdown(trimmed).map(block => block.type === 'code'
    ? `<pre class="se-code"><code>${esc(block.content)}</code></pre>`
    : block.html
  ).join('')
}

function esc(value) {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

const parsed = computed(() => parseMeta(props.modelValue || ''))
const bodyBlocks = computed(() => {
  const raw = parsed.value?.body || props.modelValue || ''
  return splitBlocks(raw).map((rawBlock, index) => ({
    id: index + 100,
    raw: rawBlock,
    html: renderHTML(rawBlock),
  }))
})

const metaBadges = computed(() => {
  const meta = parsed.value?.meta || {}
  const keywords = Array.isArray(meta.keywords) ? meta.keywords : []
  const tags = meta.metadata?.hermes?.tags || meta.tags || []
  const related = meta.metadata?.hermes?.related_skills || meta.related_skills || []
  return [
    ...keywords.map(text => ({ text, rel: false })),
    ...tags.map(text => ({ text, rel: false })),
    ...related.map(text => ({ text, rel: true })),
  ]
})

const metaRest = computed(() => {
  const result = []
  collectMetaRows(parsed.value?.meta || {}, '', result)
  return result.filter(row => row.value)
})

function collectMetaRows(value, prefix, result) {
  for (const [key, val] of Object.entries(value || {})) {
    const path = prefix ? `${prefix}.${key}` : key
    if (['name', 'description', 'keywords', 'tags', 'related_skills'].includes(key)) continue
    if (path === 'metadata.hermes.tags' || path === 'metadata.hermes.related_skills') continue
    if (val && typeof val === 'object' && !Array.isArray(val)) {
      collectMetaRows(val, path, result)
      continue
    }
    result.push({ key: path, label: humanize(path.split('.').join(' ')), value: formatValue(val) })
  }
}

function startEditMeta() {
  if (!parsed.value) return
  const raw = props.modelValue || ''
  const lines = raw.split(/\r?\n/)
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === '---')
  metaEditText.value = lines.slice(0, end + 1).join('\n')
  editingMeta.value = true
  nextTick(focusEditor)
}

function commitEditMeta() {
  if (!editingMeta.value) return
  const raw = props.modelValue || ''
  const lines = raw.split(/\r?\n/)
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === '---')
  emit('update:modelValue', metaEditText.value.trimEnd() + '\n' + lines.slice(end + 1).join('\n'))
  editingMeta.value = false
}

function startEdit(id) {
  editingIdx.value = id
  editText.value = bodyBlocks.value.find(block => block.id === id)?.raw || ''
  nextTick(focusEditor)
}

function commitEdit() {
  if (editingIdx.value < 0) return
  const id = editingIdx.value
  const body = bodyBlocks.value.map(block => block.id === id ? editText.value : block.raw)
  const prefix = parsed.value ? frontmatterPrefix(props.modelValue || '') : ''
  emit('update:modelValue', prefix + body.join('\n\n'))
  editingIdx.value = -1
}

function frontmatterPrefix(raw) {
  const lines = raw.split(/\r?\n/)
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === '---')
  return end === -1 ? '' : lines.slice(0, end + 1).join('\n').trimEnd() + '\n\n'
}

function focusEditor() {
  const textarea = document.querySelector('.se-edit')
  if (!textarea) return
  textarea.focus()
  textarea.style.height = 'auto'
  textarea.style.height = textarea.scrollHeight + 'px'
}

function onTextareaInput(event) {
  event.target.style.height = 'auto'
  event.target.style.height = event.target.scrollHeight + 'px'
}
</script>

<template>
  <div class="se">
    <div v-if="parsed" class="se__block se__block--meta" @click="startEditMeta">
      <textarea
        v-if="editingMeta"
        v-model="metaEditText"
        class="se-edit se-edit-meta"
        spellcheck="false"
        @blur="commitEditMeta"
        @keydown.escape="commitEditMeta"
        @input="onTextareaInput"
      />
      <div v-else class="se-meta">
        <div v-if="parsed.meta.name" class="se-meta__name">{{ humanize(parsed.meta.name) }}</div>
        <div v-if="parsed.meta.description" class="se-meta__desc">{{ parsed.meta.description }}</div>
        <div v-if="metaBadges.length" class="se-meta__badges">
          <span v-for="badge in metaBadges" :key="`${badge.rel}-${badge.text}`" class="se-meta__tag" :class="{ 'se-meta__tag--rel': badge.rel }">{{ badge.text }}</span>
        </div>
        <div v-if="metaRest.length" class="se-meta__rest">
          <span v-for="row in metaRest" :key="row.key" class="se-meta__kv">
            <b>{{ row.label }}</b> {{ row.value }}
          </span>
        </div>
      </div>
    </div>

    <div v-for="block in bodyBlocks" :key="block.id" class="se__block" @click="startEdit(block.id)">
      <textarea
        v-if="editingIdx === block.id"
        v-model="editText"
        class="se-edit"
        spellcheck="false"
        @blur="commitEdit"
        @keydown.escape="commitEdit"
        @input="onTextareaInput"
      />
      <div v-else class="se__view" v-html="block.html" />
    </div>
    <p v-if="!bodyBlocks.length && !parsed" class="se__empty">Empty - click to start writing.</p>
  </div>
</template>

<style scoped>
.se { min-height: 120px; max-height: calc(100vh - 180px); overflow: auto; padding: 0; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-raised); }
.se__block { cursor: text; }
.se-meta { padding: 16px 18px 14px; border-bottom: 1px solid var(--border); }
.se-meta__name { margin-bottom: 4px; color: var(--text-h); font: 600 15px/1.2 var(--sans); letter-spacing: -.2px; }
.se-meta__desc { margin-bottom: 10px; color: var(--text-muted); font: 11px/1.5 var(--mono); }
.se-meta__badges, .se-meta__rest { display: flex; flex-wrap: wrap; align-items: center; }
.se-meta__badges { gap: 5px; }
.se-meta__tag { padding: 2px 7px; border: 1px solid var(--accent-border); border-radius: 4px; color: var(--accent-text); background: var(--accent-bg); font: 9px var(--mono); }
.se-meta__tag--rel { border-color: rgba(124, 58, 237, .2); color: #7c3aed; background: rgba(124, 58, 237, .08); }
.se-meta__rest { gap: 6px 14px; margin-top: 8px; }
.se-meta__kv { color: var(--text-muted); font: 10px var(--mono); }
.se-meta__kv b { color: var(--text); font-weight: 550; }
.se__view { min-height: 28px; padding: 6px 14px; color: var(--text-h); font: 11px/1.55 var(--mono); transition: background .08s; }
.se__block:hover .se__view { background: var(--accent-bg); }
.se-edit { position: relative; z-index: 2; display: block; width: 100%; min-height: 28px; margin: 0; padding: 6px 14px; overflow: hidden; border: 1px solid var(--accent-border); outline: none; color: var(--text-h); background: var(--accent-bg); box-shadow: 0 0 0 2px var(--accent-bg); font: 11px/1.55 var(--mono); resize: none; }
.se__empty { padding: 32px 14px; color: var(--text-muted); font: 12px var(--mono); }
.se__view :deep(h1), .se__view :deep(h2), .se__view :deep(h3) { margin: 4px 0 2px; color: var(--text-h); font-family: var(--sans); }
.se__view :deep(h1) { font-size: 16px; }
.se__view :deep(h2) { font-size: 14px; }
.se__view :deep(h3) { font-size: 13px; }
.se__view :deep(p) { margin: 2px 0; }
.se__view :deep(ul), .se__view :deep(ol) { margin: 2px 0; padding-left: 20px; }
.se__view :deep(ul) { list-style-type: disc; }
.se__view :deep(ol) { list-style-type: decimal; }
.se__view :deep(li) { margin: 2px 0; }

.se__view :deep(code) { padding: 1px 4px; border-radius: 3px; color: var(--accent-text); background: var(--code-bg); font: 10px var(--mono); }
.se__view :deep(.se-code) { margin: 0; padding: 8px 14px; border-radius: 0; background: var(--code-bg); font: 11px/1.5 var(--mono); }
.se__view :deep(.se-code code) { display: block; padding: 0; border-radius: 0; background: none; }
.se__view :deep(blockquote) { margin: 4px 0; padding: 2px 10px; border-left: 2px solid var(--accent-border); color: var(--text-muted); }
.se__view :deep(table) { width: 100%; border-collapse: collapse; }
.se__view :deep(th), .se__view :deep(td) { padding: 4px 8px; border: 1px solid var(--border); font-size: 10px; text-align: left; }
.se__view :deep(a) { color: var(--accent); }
</style>
