import { ref } from 'vue'

async function request(path, body) {
  const options = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {}
  const response = await fetch(`/api${path}`, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `LSP request failed (${response.status})`)
  return data
}

export function useLsp() {
  const items = ref([])
  const languages = ref([])
  const loading = ref(false)
  const error = ref('')
  const busyId = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const data = await request('/lsp')
      items.value = data.items || []
      languages.value = data.languages || []
    } catch (reason) {
      error.value = reason.message
    } finally {
      loading.value = false
    }
  }

  async function install(name) {
    busyId.value = name
    error.value = ''
    try {
      await request('/lsp', { action: 'install', name })
      await load()
    } catch (reason) {
      error.value = reason.message
    } finally {
      busyId.value = ''
    }
  }

  async function uninstall(name) {
    busyId.value = name
    error.value = ''
    try {
      await request('/lsp', { action: 'uninstall', name })
      await load()
    } catch (reason) {
      error.value = reason.message
    } finally {
      busyId.value = ''
    }
  }

  async function enable(name, value = true) {
    error.value = ''
    try {
      await request('/lsp', { action: 'enable', name, value })
      await load()
    } catch (reason) {
      error.value = reason.message
    }
  }

  async function configure(name, env) {
    error.value = ''
    try {
      await request('/lsp', { action: 'configure', name, env })
      await load()
    } catch (reason) {
      error.value = reason.message
    }
  }

  async function probe(name) {
    busyId.value = name
    error.value = ''
    try {
      const result = await request('/lsp', { action: 'probe', name })
      return result
    } catch (reason) {
      error.value = reason.message
      return null
    } finally {
      busyId.value = ''
    }
  }

  return { items, languages, loading, error, busyId, load, install, uninstall, enable, configure, probe }
}
