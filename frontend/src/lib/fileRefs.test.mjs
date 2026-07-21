import assert from 'node:assert/strict'

import { extractInlineFileRefs, tokenizeInlineFileRefs } from './fileRefs.js'

const text = '你看 #src/foo.py 和 #frontend/src/App.vue, 再看 #src/foo.py'

assert.deepEqual(extractInlineFileRefs(text), [
  { path: 'src/foo.py', lang: 'py' },
  { path: 'frontend/src/App.vue', lang: 'vue' },
])

assert.deepEqual(tokenizeInlineFileRefs('plain #tag text'), [
  { type: 'text', text: 'plain #tag text' },
])
