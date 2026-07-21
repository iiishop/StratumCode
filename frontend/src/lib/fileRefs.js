const HASH_FILE_REF_RE = /(^|[\s([{"'`])#([A-Za-z0-9._~:/\\-]+)/g

export function languageFromPath(path) {
  const ext = path.includes('.') ? path.split('.').pop().toLowerCase() : ''
  return ext || 'text'
}

export function tokenizeInlineFileRefs(text) {
  const value = text || ''
  const parts = []
  let cursor = 0
  let match
  while ((match = HASH_FILE_REF_RE.exec(value)) !== null) {
    const start = match.index + match[1].length
    const path = match[2].replace(/[.,;:!?]+$/, '')
    if (!isFileLike(path)) continue
    const end = start + path.length + 1
    if (start > cursor) parts.push({ type: 'text', text: value.slice(cursor, start) })
    parts.push({ type: 'file', path, lang: languageFromPath(path) })
    cursor = end
  }
  if (cursor < value.length) parts.push({ type: 'text', text: value.slice(cursor) })
  return parts.length ? parts : [{ type: 'text', text: value }]
}

export function extractInlineFileRefs(text) {
  const seen = new Set()
  return tokenizeInlineFileRefs(text)
    .filter(part => part.type === 'file' && !seen.has(part.path) && seen.add(part.path))
    .map(part => ({ path: part.path, lang: part.lang }))
}

function isFileLike(path) {
  return path.includes('/') || path.includes('\\') || /\.[A-Za-z0-9]{1,8}$/.test(path)
}
