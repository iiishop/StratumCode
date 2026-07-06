import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import diff from 'highlight.js/lib/languages/diff'
import css from 'highlight.js/lib/languages/css'
import xml from 'highlight.js/lib/languages/xml'
import markdown from 'highlight.js/lib/languages/markdown'
import yaml from 'highlight.js/lib/languages/yaml'
import 'highlight.js/styles/github.css'

const languages = { python, javascript, typescript, bash, json, diff, css, xml, markdown, yaml }
Object.entries(languages).forEach(([name, grammar]) => hljs.registerLanguage(name, grammar))

const aliases = {
  py: 'python',
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  sh: 'bash',
  html: 'xml',
  vue: 'xml',
  md: 'markdown',
  yml: 'yaml',
}

export function highlightCode(code, languageOrPath = '') {
  const name = languageOrPath.toLowerCase().split('.').pop()
  const language = aliases[name] || name
  return hljs.getLanguage(language)
    ? hljs.highlight(code, { language, ignoreIllegals: true }).value
    : code.replace(/[&<>]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[char])
}
