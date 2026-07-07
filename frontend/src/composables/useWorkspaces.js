import { computed, ref } from 'vue'

export function useWorkspaces() {
  const items = ref([])
  const active = ref(null)
  const loading = ref(false)
  const error = ref('')

  async function request(path, body) {
    const response = await fetch(`/api${path}`, body ? {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    } : {})
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data.error || `Workspace request failed (${response.status})`)
    return data
  }

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const data = await request('/workspaces')
      items.value = data.items
      active.value = data.active
    } catch (reason) {
      error.value = reason.message
    } finally {
      loading.value = false
    }
  }

  async function add(name, path) {
    await request('/workspaces/save', { name, path })
    await load()
  }

  async function activate(id) {
    await request('/workspaces/activate', { id })
    await load()
  }

  async function remove(id) {
    await request('/workspaces/delete', { id })
    await load()
  }

  return {
    items,
    active,
    loading,
    error,
    activePath: computed(() => active.value?.path || ''),
    load,
    add,
    activate,
    remove,
  }
}
