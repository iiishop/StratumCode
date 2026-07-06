import { reactive, nextTick } from 'vue'

export function useChatTimeline(onAppend) {
  let sequence = 0

  function appendEvent(message, type, payload = {}) {
    const data = reactive(payload)
    message.events.push({
      id: `${message.id}-${++sequence}`,
      type,
      data,
      createdAt: Date.now(),
    })
    nextTick(() => onAppend?.())
    return data
  }

  return {
    appendEvent,
    showThinking: (message, payload) => appendEvent(message, 'thinking', payload),
    showTool: (message, payload) => appendEvent(message, 'tool', payload),
    showSubagent: (message, payload) => appendEvent(message, 'subagent', payload),
    showDiff: (message, payload) => appendEvent(message, 'diff', payload),
    showOutput: (message, payload) => appendEvent(message, 'output', payload),
  }
}
