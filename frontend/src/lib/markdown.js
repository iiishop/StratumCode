export function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function parseInline(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`\n]+)`/g, '<code class="md-inline">$1</code>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
}

export function parseBlock(text) {
  if (!text) return ''
  let remaining = text

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

    if (trimmed.startsWith('<') && trimmed.endsWith('>')) {
      if (inList) { out.push('</ul>'); inList = false }
      if (inBlockquote) { out.push('</blockquote>'); inBlockquote = false }
      out.push(trimmed)
      continue
    }

    const hMatch = trimmed.match(/^(#{1,3})\s+(.+)/)
    if (hMatch) {
      if (inList) { out.push('</ul>'); inList = false }
      if (inBlockquote) { out.push('</blockquote>'); inBlockquote = false }
      const level = Math.min(hMatch[1].length + 1, 3)
      out.push(`<h${level}>${parseInline(hMatch[2])}</h${level}>`)
      continue
    }

    const bqMatch = trimmed.match(/^>\s?(.+)/)
    if (bqMatch) {
      if (inList) { out.push('</ul>'); inList = false }
      if (!inBlockquote) { out.push('<blockquote>'); inBlockquote = true }
      out.push(`<p>${parseInline(bqMatch[1])}</p>`)
      continue
    }

    if (inBlockquote) { out.push('</blockquote>'); inBlockquote = false }

    const ulMatch = trimmed.match(/^[-*]\s+(.+)/)
    if (ulMatch) {
      if (!inList) { out.push('<ul>'); inList = true }
      out.push(`<li>${parseInline(ulMatch[1])}</li>`)
      continue
    }

    const olMatch = trimmed.match(/^\d+\.\s+(.+)/)
    if (olMatch) {
      if (inList) { out.push('</ul>'); inList = false }
      out.push(`<ol><li>${parseInline(olMatch[1])}</li></ol>`)
      continue
    }

    if (inList) { out.push('</ul>'); inList = false }

    out.push(`<p>${parseInline(trimmed)}</p>`)
  }

  if (inList) out.push('</ul>')
  if (inBlockquote) out.push('</blockquote>')

  return out.join('')
}

export function renderMarkdown(content) {
  if (!content) return []
  const result = []
  const regex = /```(\w*)\n([\s\S]*?)```/g
  let last = 0
  let match
  while ((match = regex.exec(content)) !== null) {
    if (match.index > last) {
      result.push({ type: 'md', html: parseBlock(content.slice(last, match.index)) })
    }
    result.push({ type: 'code', lang: match[1] || 'plaintext', content: match[2].trim() })
    last = match.index + match[0].length
  }
  if (last < content.length) {
    result.push({ type: 'md', html: parseBlock(content.slice(last)) })
  }
  return result
}
