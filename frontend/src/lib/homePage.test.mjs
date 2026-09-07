import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { parse, babelParse } from '@vue/compiler-sfc'

const source = parse(readFileSync(new URL('../components/HomePage.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content
const functions = babelParse(source, { sourceType: 'module' }).program.body
  .filter(node => node.type === 'FunctionDeclaration')
  .map(node => source.slice(node.start, node.end)).join('\n')

function harness(extra = {}) {
  const context = vm.createContext({
    messages: [], savedEventCounts: new Map(), savedMessageCount: 0,
    evidenceRuns: [], taskAnalyses: [], subagentRuns: [], fileContext: [],
    activeRunId: { value: '' }, sessionUsage: {}, ...extra,
  })
  vm.runInContext(functions, context)
  return context
}

test('saves ordinary user messages without events', () => {
  const c = harness({ messages: [{ id: 1, role: 'user', content: 'hello' }] })
  c.commitIncrement(c.buildStateIncrement())
  assert.equal(c.savedMessageCount, 1)
  assert.equal(c.buildStateIncrement().newMessages.length, 0)
})

test('persists active output and reasoning until their final update', () => {
  for (const [type, active, complete] of [
    ['output', { content: 'a', streaming: true }, { content: 'abc', streaming: false }],
    ['thinking', { text: 'a', done: false }, { text: 'abc', done: true }],
    ['tool', { status: 'running' }, { status: 'done' }],
  ]) {
    const event = { id: 'event', type, data: active }
    const c = harness({ messages: [{ id: 1, events: [event] }] })
    c.commitIncrement(c.buildStateIncrement())
    const pending = c.buildStateIncrement()
    assert.equal(pending.appends[0].events[0].id, 'event')
    c.commitIncrement(pending)
    assert.equal(c.savedEventCounts.get(1), 0)
    Object.assign(event.data, complete)
    const final = c.buildStateIncrement()
    assert.equal(final.appends[0].events[0].data.status ?? final.appends[0].events[0].data.streaming ?? final.appends[0].events[0].data.done, complete.status ?? complete.streaming ?? complete.done)
    c.commitIncrement(final)
    assert.equal(c.buildStateIncrement().appends.length, 0)
  }
})

test('save acknowledgement does not skip messages arriving during the request', () => {
  const c = harness({ messages: [{ id: 1, role: 'user' }] })
  const inFlight = c.buildStateIncrement()
  c.messages.push({ id: 2, events: [] })
  c.commitIncrement(inFlight)
  assert.equal(c.buildStateIncrement().newMessages[0].id, 2)
})

test('a failed save can resend the same uncommitted increment', () => {
  const c = harness({ messages: [{ id: 1, events: [] }] })
  assert.equal(JSON.stringify(c.buildStateIncrement()), JSON.stringify(c.buildStateIncrement()))
  assert.equal(c.savedMessageCount, 0)
})

test('coalesces a burst of scroll requests and preserves manual scroll cancellation', () => {
  let callback, queued = 0, cancelled = 0, animations = 0
  const c = harness({
    messageScrollFrame: null, isAtMessageBottom: { value: true },
    requestAnimationFrame(fn) { callback = fn; return ++queued },
    cancelAnimationFrame() { cancelled++ },
  })
  c.animSmoothScroll = () => { animations++ }
  for (let i = 0; i < 1000; i++) c.scrollForNewContent()
  assert.equal(queued, 1)
  callback()
  assert.equal(animations, 1)
  c.scrollForNewContent()
  c.isAtMessageBottom.value = false
  callback()
  assert.equal(animations, 1)
  c.isAtMessageBottom.value = true
  c.scrollForNewContent()
  c.cancelMessageScroll()
  assert.equal(cancelled, 1)
  assert.equal(c.messageScrollFrame, null)
})

test('title success and failure finish without undefined variable errors', async () => {
  for (const fails of [false, true]) {
    const target = { id: 1, name: 'old', titleGenerating: true }
    const c = harness({
      props: { sessions: [target], session: target },
      fetch: async () => {
        if (fails) throw new Error('offline')
        return { json: async () => ({ title: 'new' }) }
      },
    })
    await c.generateSessionTitle(1, 'hello', { events: [{ type: 'output', data: { content: 'answer' } }] })
    assert.equal(target.name, fails ? 'old' : 'new')
    assert.equal(target.titleGenerating, false)
  }
})
