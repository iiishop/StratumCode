import { ref } from 'vue'

const DEFAULT_STATUS = {
  current_version: '0.0.0',
  short_commit: '',
  latest_version: '',
  latest_release: {},
  stable_available: false,
  remote_short_commit: '',
  commits_behind: 0,
  dev_available: false,
}

async function request(path, body) {
  const response = await fetch(`/api${path}`, body ? {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  } : {})
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `Update request failed (${response.status})`)
  return data
}

export function useUpdates() {
  const status = ref({ ...DEFAULT_STATUS })
  const loading = ref(false)
  const error = ref('')

  async function refresh() {
    loading.value = true
    error.value = ''
    try {
      status.value = { ...DEFAULT_STATUS, ...(await request('/updates/status')) }
    } catch (reason) {
      error.value = reason.message
    } finally {
      loading.value = false
    }
  }

  async function apply(channel, onEvent) {
    const response = await fetch('/api/updates/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel }),
    })
    if (!response.ok || !response.body) throw new Error(`Update failed (${response.status})`)
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.trim()) onEvent(JSON.parse(line))
      }
    }
    if (buffer.trim()) onEvent(JSON.parse(buffer))
  }

  async function restart() {
    await request('/updates/restart', {})
  }

  return { status, loading, error, refresh, apply, restart }
}
