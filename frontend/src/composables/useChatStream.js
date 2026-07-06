import { useChatTimeline } from './useChatTimeline'

export function useChatStream(onEvent) {
  const timeline = useChatTimeline(onEvent)
  let controller = null

  function startEvent(message, packet, active) {
    const handlers = {
      thinking: timeline.showThinking,
      tool: timeline.showTool,
      subagent: timeline.showSubagent,
      diff: timeline.showDiff,
      output: timeline.showOutput,
    }
    const handler = handlers[packet.event]
    if (!handler) return
    active.set(packet.id, handler(message, packet.data))
  }

  function applyPacket(message, packet, active) {
    if (packet.op === 'start') {
      startEvent(message, packet, active)
      return
    }
    if (packet.op === 'error') throw new Error(packet.message || 'Chat stream failed')
    const target = active.get(packet.id)
    if (!target) return
    if (packet.op === 'delta') target[packet.field] = `${target[packet.field] || ''}${packet.value || ''}`
    if (packet.op === 'update') Object.assign(target, packet.patch)
    onEvent?.()
  }

  async function stream(message, request) {
    controller?.abort()
    controller = new AbortController()
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal: controller.signal,
    })
    if (!response.ok) {
      const error = await response.json().catch(() => ({}))
      throw new Error(error.error || `Chat request failed (${response.status})`)
    }
    if (!response.body) throw new Error('Streaming response body is unavailable')

    const active = new Map()
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.trim()) applyPacket(message, JSON.parse(line), active)
      }
      if (done) break
    }
    if (buffer.trim()) applyPacket(message, JSON.parse(buffer), active)
  }

  function abort() {
    controller?.abort()
    controller = null
  }

  return { stream, abort }
}
