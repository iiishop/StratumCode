import { ref } from 'vue'

async function request(path, body) {
  const options = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {}
  const response = await fetch(`/api${path}`, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `MCP request failed (${response.status})`)
  return data
}

export function useMcp() {
  const items = ref([])
  const loading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const data = await request('/mcp')
      items.value = data.items || []
    } catch (reason) {
      error.value = reason.message
    } finally {
      loading.value = false
    }
  }

  async function start(id) {
    try {
      await request('/mcp/start', { id })
      await load()
    } catch (reason) {
      error.value = reason.message
    }
  }

  async function remove(id) {
    try {
      await request('/mcp/delete', { id })
      await load()
    } catch (reason) {
      error.value = reason.message
    }
  }

  async function configure(id, env) {
    try {
      await request('/mcp/configure', { id, env })
      await load()
    } catch (reason) {
      error.value = reason.message
    }
  }

  return { items, loading, error, load, start, remove, configure }
}
