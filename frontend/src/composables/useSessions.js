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
  const scopeId = ref(null)
  const loading = ref(false)
  const error = ref('')
  let loadVersion = 0

  async function load(workspaceId) {
    const version = ++loadVersion
    if (!workspaceId) {
      items.value = []
      active.value = null
      scopeId.value = null
      return
    }
    if (scopeId.value !== workspaceId) {
      active.value = null
      items.value = []
    }
    scopeId.value = workspaceId
    loading.value = true
    error.value = ''
    try {
      const data = await request('/sessions/list', { workspace_id: workspaceId })
      if (scopeId.value !== workspaceId || version !== loadVersion) return
      items.value = data.items || []
      if (active.value && !items.value.some(item => item.id === active.value.id)) active.value = null
    } catch (reason) {
      if (scopeId.value === workspaceId && version === loadVersion) error.value = reason.message
    } finally {
      if (scopeId.value === workspaceId && version === loadVersion) loading.value = false
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
    if (scopeId.value && data.session.workspace_id !== scopeId.value) {
      throw new Error('Session does not belong to the active workspace')
    }
    active.value = data.session
    return data.session
  }

  async function rename(id, name, workspaceId) {
    await request('/sessions/rename', { id, name })
    await load(workspaceId)
    if (active.value?.id === id) active.value = await open(id)
  }

  async function remove(id) {
    const wasActive = active.value?.id === id
    await request('/sessions/delete', { id })
    loadVersion += 1
    loading.value = false
    items.value = items.value.filter(item => item.id !== id)
    if (wasActive) active.value = null
  }

  function clear() {
    active.value = null
  }

  async function saveState(id, state) {
    if (!id) return
    await request('/sessions/save-state', { id, state })
  }

  return { items, active, loading, error, load, create, open, rename, remove, saveState, clear }
}
