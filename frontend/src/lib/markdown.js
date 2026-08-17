import MarkdownIt from 'markdown-it'
import { highlightCode } from './highlight.js'

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  breaks: false,
  highlight(code, language) {
    return highlightCode(code, language || '')
  },
})

markdown.validateLink = (url) => {
  const value = String(url || '').trim()
  if (/^(https?:|mailto:|#|\/|\.\/|\.\.\/)/i.test(value)) return true
  return /^[A-Za-z]:[\\/]/.test(value)
}

const defaultLinkOpen = markdown.renderer.rules.link_open || ((tokens, index, options, env, self) => self.renderToken(tokens, index, options))
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  const token = tokens[index]
  const hrefIndex = token.attrIndex('href')
  const href = hrefIndex >= 0 ? token.attrs[hrefIndex][1] : ''
  if (/^(https?:|mailto:)/i.test(href)) {
    token.attrSet('target', '_blank')
    token.attrSet('rel', 'noopener noreferrer')
  }
  return defaultLinkOpen(tokens, index, options, env, self)
}

export function escapeHtml(text) {
  return markdown.utils.escapeHtml(String(text || ''))
}

export function parseInline(text) {
  return markdown.renderInline(String(text || ''))
}

export function parseBlock(text) {
  return markdown.render(String(text || '')).trim()
}

export function renderMarkdown(content) {
  const html = parseBlock(content)
  return html ? [{ type: 'md', html }] : []
}
