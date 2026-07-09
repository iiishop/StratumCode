import { ref } from 'vue'

async function request(path, body) {
  const response = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `Session request failed (${response.status})`)
  return data
}

export function useSessions() {
  const items = ref([])
  const active = ref(null)
  const loading = ref(false)
  const error = ref('')

  async function load(workspaceId) {
    if (!workspaceId) {
      items.value = []
      active.value = null
      return
    }
    loading.value = true
    error.value = ''
    try {
      const data = await request('/sessions/list', { workspace_id: workspaceId })
      items.value = data.items || []
      if (active.value && !items.value.some(item => item.id === active.value.id)) active.value = null
    } catch (reason) {
      error.value = reason.message
    } finally {
      loading.value = false
    }
  }

  async function create(workspaceId) {
    const data = await request('/sessions/create', { workspace_id: workspaceId })
    active.value = data.session
    await load(workspaceId)
    active.value = data.session
    return data.session
  }

  async function open(id) {
    const data = await request('/sessions/get', { id })
    active.value = data.session
    return data.session
  }

  async function rename(id, name, workspaceId) {
    await request('/sessions/rename', { id, name })
    await load(workspaceId)
    if (active.value?.id === id) active.value = await open(id)
  }

  async function remove(id, workspaceId) {
    await request('/sessions/delete', { id })
    await load(workspaceId)
    if (active.value?.id === id) active.value = items.value[0] ? await open(items.value[0].id) : null
  }

  async function saveState(id, state) {
    if (!id) return
    await request('/sessions/save-state', { id, state })
  }

  return { items, active, loading, error, load, create, open, rename, remove, saveState }
}
