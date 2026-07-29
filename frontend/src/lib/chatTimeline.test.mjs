import test from 'node:test'
import assert from 'node:assert/strict'

import { groupChatEvents, isPrimaryChatEvent } from './chatTimeline.js'

const event = (id, type) => ({ id, type, data: {} })

test('groups process events around responses without changing their order', () => {
  const segments = groupChatEvents([
    event('stage-1', 'stage'),
    event('tool-1', 'tool'),
    event('response-1', 'output'),
    event('usage-1', 'usage'),
    event('transition-1', 'state_transition'),
  ])

  assert.deepEqual(segments.map(segment => segment.kind), ['process', 'primary', 'process'])
  assert.deepEqual(segments[0].events.map(item => item.id), ['stage-1', 'tool-1'])
  assert.equal(segments[1].event.id, 'response-1')
  assert.deepEqual(segments[2].events.map(item => item.id), ['usage-1', 'transition-1'])
})

test('keeps actionable questions and safety stops outside process groups', () => {
  assert.equal(isPrimaryChatEvent(event('question-1', 'user_question')), true)
  assert.equal(isPrimaryChatEvent(event('stop-1', 'safety_stop')), true)

  const segments = groupChatEvents([
    event('thinking-1', 'thinking'),
    event('question-1', 'user_question'),
    event('thinking-2', 'thinking'),
  ])

  assert.deepEqual(segments.map(segment => segment.kind), ['process', 'primary', 'process'])
})

test('returns no empty process groups for adjacent responses', () => {
  const segments = groupChatEvents([
    event('response-1', 'output'),
    event('response-2', 'output'),
  ])

  assert.deepEqual(segments.map(segment => segment.event.id), ['response-1', 'response-2'])
})
