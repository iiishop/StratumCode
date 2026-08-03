import { useChatTimeline } from './useChatTimeline'

export function useChatStream(onRender, onPacket) {
  const timeline = useChatTimeline(onRender)
  let controller = null

  function startEvent(message, packet, active) {
    if (!packet.event) return
    const data = timeline.appendEvent(message, packet.event, packet.data)
    active.set(packet.id, { type: packet.event, data })
    onPacket?.(packet, packet.event, data)
  }

  function applyPacket(message, packet, active) {
    if (packet.op === 'start') {
      startEvent(message, packet, active)
      return
    }
    if (packet.op === 'error') throw new Error(packet.message || 'Chat stream failed')
    const target = active.get(packet.id)
    if (!target) {
      onPacket?.(packet)
      return
    }
    if (packet.op === 'delta') target.data[packet.field] = `${target.data[packet.field] || ''}${packet.value || ''}`
    if (packet.op === 'update') Object.assign(target.data, packet.patch)
    onPacket?.(packet, target.type, target.data)
    onRender?.()
  }

  async function stream(message, request, url = '/api/chat') {
    controller?.abort()
    controller = new AbortController()
    const response = await fetch(url, {
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
