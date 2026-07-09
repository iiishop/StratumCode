<script setup>
import { computed, ref } from 'vue'
import { highlightCode } from '../../lib/highlight'
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

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function parseInline(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`\n]+)`/g, '<code class="md-inline">$1</code>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
}

function parseBlock(text) {
  if (!text) return ''
  let remaining = text

  // tables: detect | col | col |\n |---| ---| \n | val | val | sequences
  remaining = remaining.replace(/(?:^\|?.+\|.+\n)(?:\|[-: ]+\|.+\n)(?:\|?.+\|.+\n?)+/gm, (match) => {
    const rows = match.trim().split('\n')
    if (rows.length < 2) return match
    const headerCells = rows[0].replace(/^\||\|$/g, '').split('|').map(c => parseInline(c.trim()))
    const aligns = (rows[1] || '').replace(/^\||\|$/g, '').split('|').map(c => {
      const t = c.trim()
      if (t.startsWith(':') && t.endsWith(':')) return 'center'
      if (t.endsWith(':')) return 'right'
      return 'left'
    })
    const headHTML = headerCells.map((c, i) => `<th style="text-align:${aligns[i] || 'left'}">${c}</th>`).join('')
    const bodyRows = rows.slice(2).map(row => {
      const cells = row.replace(/^\||\|$/g, '').split('|').map((c, i) =>
        `<td style="text-align:${aligns[i] || 'left'}">${parseInline(c.trim())}</td>`
      ).join('')
      return `<tr>${cells}</tr>`
    }).join('')
    return `<table><thead><tr>${headHTML}</tr></thead><tbody>${bodyRows}</tbody></table>\n`
  })

  const lines = remaining.split('\n')
  const out = []
  let inList = false
  let inBlockquote = false

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      if (inList) { out.push('</ul>'); inList = false }
      if (inBlockquote) { out.push('</blockquote>'); inBlockquote = false }
      out.push('<br>')
      continue
    }

    // raw HTML (tables etc.) — pass through unescaped
    if (trimmed.startsWith('<') && trimmed.endsWith('>')) {
      if (inList) { out.push('</ul>'); inList = false }
      if (inBlockquote) { out.push('</blockquote>'); inBlockquote = false }
      out.push(trimmed)
      continue
    }

    // headers
    const hMatch = trimmed.match(/^(#{1,3})\s+(.+)/)
    if (hMatch) {
      if (inList) { out.push('</ul>'); inList = false }
      if (inBlockquote) { out.push('</blockquote>'); inBlockquote = false }
      const level = Math.min(hMatch[1].length + 1, 3)
      out.push(`<h${level}>${parseInline(hMatch[2])}</h${level}>`)
      continue
    }

    // blockquote
    const bqMatch = trimmed.match(/^>\s?(.+)/)
    if (bqMatch) {
      if (inList) { out.push('</ul>'); inList = false }
      if (!inBlockquote) { out.push('<blockquote>'); inBlockquote = true }
      out.push(`<p>${parseInline(bqMatch[1])}</p>`)
      continue
    }

    if (inBlockquote) { out.push('</blockquote>'); inBlockquote = false }

    // unordered list
    const ulMatch = trimmed.match(/^[-*]\s+(.+)/)
    if (ulMatch) {
      if (!inList) { out.push('<ul>'); inList = true }
      out.push(`<li>${parseInline(ulMatch[1])}</li>`)
      continue
    }

    // ordered list
    const olMatch = trimmed.match(/^\d+\.\s+(.+)/)
    if (olMatch) {
      if (inList) { out.push('</ul>'); inList = false }
      out.push(`<ol><li>${parseInline(olMatch[1])}</li></ol>`)
      continue
    }

    if (inList) { out.push('</ul>'); inList = false }

    // paragraph
    out.push(`<p>${parseInline(trimmed)}</p>`)
  }

  if (inList) out.push('</ul>')
  if (inBlockquote) out.push('</blockquote>')

  return out.join('')
}

const parts = computed(() => {
  const result = []
  const regex = /```(\w*)\n([\s\S]*?)```/g
  let last = 0
  let match
  while ((match = regex.exec(props.event.content)) !== null) {
    if (match.index > last) {
      result.push({ type: 'md', html: parseBlock(props.event.content.slice(last, match.index)) })
    }
    result.push({ type: 'code', lang: match[1] || 'plaintext', content: match[2].trim() })
    last = match.index + match[0].length
  }
  if (last < props.event.content.length) {
    result.push({ type: 'md', html: parseBlock(props.event.content.slice(last)) })
  }
  return result
})
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
