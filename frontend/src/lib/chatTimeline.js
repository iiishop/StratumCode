const PRIMARY_EVENT_TYPES = new Set(['output', 'user_question', 'safety_stop'])

export function isPrimaryChatEvent(event) {
  return PRIMARY_EVENT_TYPES.has(event?.type)
}

export function groupChatEvents(events = []) {
  const segments = []
  let processEvents = []
  let processStart = -1

  function flushProcess(endIndex) {
    if (!processEvents.length) return
    segments.push({
      kind: 'process',
      key: `process-${processEvents[0].id}`,
      events: processEvents,
      startIndex: processStart,
      endIndex,
    })
    processEvents = []
    processStart = -1
  }

  events.forEach((event, index) => {
    if (isPrimaryChatEvent(event)) {
      flushProcess(index - 1)
      segments.push({
        kind: 'primary',
        key: `primary-${event.id}`,
        event,
        startIndex: index,
        endIndex: index,
      })
      return
    }

    if (!processEvents.length) processStart = index
    processEvents.push(event)
  })

  flushProcess(events.length - 1)
  return segments
}
