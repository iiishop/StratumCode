import { ref } from 'vue'

async function request(path, body) {
  const options = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {}
  const response = await fetch(`/api${path}`, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `Skill request failed (${response.status})`)
  return data
}

export function useSkills() {
  const local = ref([])
  const results = ref([])
  const roots = ref([])
  const preview = ref(null)
  const runtime = ref({ available: false, npx: { available: false, command: '' } })
  const loading = ref(false)
  const searching = ref(false)
  const busy = ref('')
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const data = await request('/skills')
      local.value = data.items || []
      roots.value = data.roots || []
      runtime.value = data.runtime || runtime.value
    } catch (reason) {
      error.value = reason.message
    } finally {
      loading.value = false
    }
  }

  async function search(query) {
    if (!runtime.value.available) {
      error.value = 'npx is required to search remote skills.'
      return
    }
    searching.value = true
    error.value = ''
    try {
      const data = await request('/skills/search', { query })
      results.value = data.items || []
    } catch (reason) {
      error.value = reason.message
    } finally {
      searching.value = false
    }
  }

  async function add(source) {
    busy.value = source
    error.value = ''
    try {
      const data = await request('/skills/add', { source })
      local.value = data.items || []
      results.value = results.value.map(item => sourceFor(item) === source ? { ...item, installed: true } : item)
    } catch (reason) {
      error.value = reason.message
    } finally {
      busy.value = ''
    }
  }

  async function create(payload) {
    busy.value = 'create'
    error.value = ''
    try {
      await request('/skills/create', payload)
      await load()
    } catch (reason) {
      error.value = reason.message
    } finally {
      busy.value = ''
    }
  }

  async function remove(item) {
    const path = item?.path || ''
    busy.value = path
    error.value = ''
    try {
      const data = await request('/skills/delete', { path })
      local.value = data.items || []
      preview.value = null
    } catch (reason) {
      error.value = reason.message
    } finally {
      busy.value = ''
    }
  }

  async function installRuntime() {
    busy.value = 'runtime'
    error.value = ''
    try {
      const data = await request('/skills/runtime/install', {})
      runtime.value = data.runtime || runtime.value
    } catch (reason) {
      error.value = reason.message
    } finally {
      busy.value = ''
    }
  }

  async function show(item) {
    preview.value = null
    error.value = ''
    try {
      preview.value = await request('/skills/preview', {
        path: item.skill_file || item.path || '',
        source: item.package || item.url || '',
      })
    } catch (reason) {
      error.value = reason.message
    }
  }

  return { local, results, roots, preview, runtime, loading, searching, busy, error, load, search, add, create, remove, installRuntime, show }
}

function sourceFor(item) {
  return item.package || item.url || item.path || ''
}
