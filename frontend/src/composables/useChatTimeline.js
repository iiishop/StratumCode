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
    showStage: (message, payload) => appendEvent(message, 'stage', payload),
    showHypothesis: (message, payload) => appendEvent(message, 'hypothesis', payload),
    showEvidence: (message, payload) => appendEvent(message, 'evidence', payload),
    showEvidenceRelation: (message, payload) => appendEvent(message, 'evidence_relation', payload),
    showVerdict: (message, payload) => appendEvent(message, 'verdict', payload),
    showUsage: (message, payload) => appendEvent(message, 'usage', payload),
  }
}
