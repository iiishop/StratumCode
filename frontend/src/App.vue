<script setup>
import { computed, ref, reactive, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { gsap } from 'gsap'
import { animate, stagger as animeStagger } from 'animejs'
import Sidebar from './components/Sidebar.vue'
import HomePage from './components/HomePage.vue'
import StageModelSettings from './components/providers/StageModelSettings.vue'
import McpPage from './components/mcp/McpPage.vue'
import LspPage from './components/lsp/LspPage.vue'
import SkillsPage from './components/skills/SkillsPage.vue'
import SettingsPage from './components/settings/SettingsPage.vue'
import { useMcp } from './composables/useMcp'
import { useLsp } from './composables/useLsp'
import { useSessions } from './composables/useSessions'
import { useWorkspaces } from './composables/useWorkspaces'

const currentView = ref('home')
const {
  items: workspaces,
  active: activeWorkspace,
  error: workspaceError,
  load: loadWorkspaces,
  add: addWorkspace,
  activate: activateWorkspace,
  remove: deleteWorkspace,
} = useWorkspaces()
const sessionStore = useSessions()
const mcpStore = useMcp()
const lspStore = useLsp()
const activeSession = computed(() => sessionStore.active.value)
const sessionItems = computed(() => sessionStore.items.value)
const mcpServers = computed(() => mcpStore.items.value)
const mcpLoading = computed(() => mcpStore.loading.value)
const mcpError = computed(() => mcpStore.error.value)
const lspServers = computed(() => lspStore.items.value)
const lspLanguages = computed(() => lspStore.languages.value)
const lspLoading = computed(() => lspStore.loading.value)
const lspBusyId = computed(() => lspStore.busyId.value)
const lspError = computed(() => lspStore.error.value)
const lspMason = computed(() => lspStore.mason.value)
const lspBootstrap = computed(() => lspStore.bootstrap.value)
const lspBootstrapSteps = computed(() => lspStore.bootstrapSteps.value)
const currentTitle = computed(() => ({
  home: 'Workspace',
  providers: 'Providers',
  mcp: 'MCP',
  lsp: 'LSP',
  skills: 'Skills',
  settings: 'Settings',
})[currentView.value] || 'Workspace')
const workspaceLabel = computed(() => activeWorkspace.value?.name || 'No workspace')
const sessionLabel = computed(() => currentView.value === 'home' ? activeSession.value?.name : '')
const providers = ref([])
const showForm = ref(false)
const defaultPricingRule = () => ({
  currency: 'CNY',
  start: '00:00',
  end: '05:00',
  input_per_m: 3,
  output_per_m: 1,
  cache_per_m: 0.25,
})
const form = ref({ name: '', base_url: '', api_key: '' })
const providerNotice = ref('')
const codexOAuth = reactive({
  loading: false,
  deviceAuthId: '',
  userCode: '',
  verificationUri: '',
  message: '',
})
const expandedId = ref(null)
const state = reactive({})
const bodyRefs = reactive({})
const cardRefs = reactive({})
const dotRefs = reactive({})
const listRefs = reactive({})
const toastRefs = reactive({})
const appSettings = ref({ output_language: 'zh', languages: [], font_scale: 1.0, round_limits: [], task_limits: [] })
const settingsSaving = ref(false)
const fontScale = computed(() => appSettings.value.font_scale || 1)

function applyFontScale(scale) {
  const root = document.documentElement
  root.style.setProperty('--font-body', `${Math.round(14 * scale)}px`)
  root.style.setProperty('--font-ui', `${Math.round(12 * scale)}px`)
  root.style.setProperty('--font-caption', `${Math.round(11 * scale)}px`)
  root.style.setProperty('--font-code', `${Math.round(12 * scale)}px`)
}

let gsapCtx

const presets = [
  { name: 'OpenAI',       url: 'https://api.openai.com',       color: '#10a37f' },
  { name: 'DeepSeek',     url: 'https://api.deepseek.com',     color: '#4d6bfe' },
  { name: 'Groq',         url: 'https://api.groq.com/openai',  color: '#f36f2c' },
  { name: 'Together',     url: 'https://api.together.xyz',     color: '#6366f1' },
  { name: 'Fireworks',    url: 'https://api.fireworks.ai',     color: '#f43f5e' },
  { name: 'OpenRouter',   url: 'https://openrouter.ai/api',    color: '#8b5cf6' },
  { name: 'SiliconFlow',  url: 'https://api.siliconflow.cn',   color: '#06b6d4' },
  { name: 'Mistral',      url: 'https://api.mistral.ai',       color: '#f59e0b' },
  { name: 'Ollama',       url: 'http://localhost:11434',        color: '#84cc16' },
  {
    name: 'Codex subscription',
    url: 'ChatGPT/Codex OAuth',
    color: '#111827',
    auth: 'codex_oauth',
    note: 'Connect with the same OpenAI device OAuth flow used by Codex-style clients.',
  },
]

async function bootstrap() {
  await Promise.all([loadWorkspaces(), mcpStore.load(), lspStore.load(), loadAppSettings()])
  if (activeWorkspace.value?.id) {
    await sessionStore.load(activeWorkspace.value.id)
    if (sessionStore.items.value[0]) await sessionStore.open(sessionStore.items.value[0].id)
    else await createSession(activeWorkspace.value.id)
  }
}

async function createSession(workspaceId = activeWorkspace.value?.id) {
  if (!workspaceId) return
  const session = await sessionStore.create(workspaceId)
  currentView.value = 'home'
  return session
}

async function openSession(id) {
  await sessionStore.open(id)
  currentView.value = 'home'
}

async function renameSession(id) {
  const current = sessionStore.items.value.find(item => item.id === id) || activeSession.value
  const name = window.prompt('Session name', current?.name || '')
  if (!name) return
  await sessionStore.rename(id, name, activeWorkspace.value?.id)
}

async function removeSession(id) {
  if (!window.confirm('Delete this session?')) return
  await sessionStore.remove(id, activeWorkspace.value?.id)
  if (!sessionStore.active.value && activeWorkspace.value?.id) await createSession(activeWorkspace.value.id)
}

async function createSessionForWorkspace(workspace) {
  await activateWorkspace(workspace.id)
  await sessionStore.load(workspace.id)
  if (sessionStore.items.value[0]) await sessionStore.open(sessionStore.items.value[0].id)
  else await createSession(workspace.id)
  currentView.value = 'home'
}

async function activateWorkspaceAndSession(id) {
  const workspace = workspaces.value.find(item => item.id === id)
  if (!workspace) return
  await createSessionForWorkspace(workspace)
}

async function addWorkspacePrompt() {
  const path = await window.pywebview.api.select_folder()
  if (!path) return
  await addWorkspace('', path)
  await loadWorkspaces()
}

async function addWorkspaceFromPanel(payload) {
  await addWorkspace(payload?.name || '', payload?.path || '')
}

async function removeWorkspace(id) {
  const workspace = workspaces.value.find(item => item.id === id)
  const label = workspace?.name || workspace?.path || 'this workspace'
  if (!window.confirm(`Delete workspace "${label}"? Sessions and files are not deleted.`)) return
  try {
    await deleteWorkspace(id)
    if (activeWorkspace.value?.id) {
      await sessionStore.load(activeWorkspace.value.id)
      if (sessionStore.items.value[0]) await sessionStore.open(sessionStore.items.value[0].id)
      else await createSession(activeWorkspace.value.id)
    }
  } catch (reason) {
    window.alert(reason.message || 'Failed to delete workspace')
  }
}

async function saveActiveSessionState(state) {
  if (!activeSession.value?.id) return
  await sessionStore.saveState(activeSession.value.id, state)
  const item = sessionStore.items.value.find(item => item.id === activeSession.value.id)
  if (item) item.usage = state.usage
  activeSession.value.usage = state.usage
}

function dedupName(name) {
  const existing = providers.value.map(p => p.name)
  if (!existing.includes(name)) return name
  let i = 2
  while (existing.includes(`${name}-${i}`)) i++
  return `${name}-${i}`
}

function selectPreset(p) {
  providerNotice.value = ''
  if (p.auth === 'codex_oauth') {
    providerNotice.value = p.note
    showForm.value = true
    startCodexOAuth()
    return
  }
  form.value.name = dedupName(p.name)
  form.value.base_url = p.url
  showForm.value = true
}

const api = async (path, body) => {
  const opts = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {}
  const r = await fetch('/api' + path, opts)
  return r.json()
}

async function loadAppSettings() {
  const data = await api('/app-settings')
  appSettings.value = data
}

async function saveSetting(field, value) {
  appSettings.value = { ...appSettings.value, [field]: value }
  settingsSaving.value = true
  try {
    const saved = await api('/app-settings/save', { [field]: value })
    appSettings.value = { ...appSettings.value, ...saved }
  } finally {
    settingsSaving.value = false
  }
}

async function startCodexOAuth() {
  codexOAuth.loading = true
  codexOAuth.message = ''
  try {
    const data = await api('/providers/codex-oauth/start', {})
    codexOAuth.deviceAuthId = data.device_auth_id
    codexOAuth.userCode = data.user_code
    codexOAuth.verificationUri = data.verification_uri
    window.open(data.verification_uri, '_blank', 'noopener,noreferrer')
  } catch (reason) {
    codexOAuth.message = reason.message || 'Failed to start Codex OAuth.'
  } finally {
    codexOAuth.loading = false
  }
}

async function copyCodexUserCode() {
  if (!codexOAuth.userCode) return
  try {
    await navigator.clipboard.writeText(codexOAuth.userCode)
    codexOAuth.message = 'Device code copied.'
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = codexOAuth.userCode
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
    codexOAuth.message = 'Device code copied.'
  }
}

async function finishCodexOAuth() {
  if (!codexOAuth.deviceAuthId || !codexOAuth.userCode) return
  codexOAuth.loading = true
  codexOAuth.message = ''
  try {
    const data = await api('/providers/codex-oauth/finish', {
      device_auth_id: codexOAuth.deviceAuthId,
      user_code: codexOAuth.userCode,
    })
    if (data.pending) {
      codexOAuth.message = 'Authorization is still pending.'
      return
    }
    codexOAuth.deviceAuthId = ''
    codexOAuth.userCode = ''
    codexOAuth.verificationUri = ''
    showForm.value = false
    await load()
  } catch (reason) {
    codexOAuth.message = reason.message || 'Failed to finish Codex OAuth.'
  } finally {
    codexOAuth.loading = false
  }
}

function init(id) {
  if (!state[id]) {
    state[id] = reactive({
      testMsg: null,
      models: [],
      modelResults: {},
      pricing: {},
      loading: '',
      filter: '',
      connOk: null,
    })
  }
  return state[id]
}

function setCardRef(id, el) { if (el) cardRefs[id] = el; else delete cardRefs[id] }
function setBodyRef(id, el) { if (el) bodyRefs[id] = el; else delete bodyRefs[id] }
function setDotRef(id, el) { if (el) dotRefs[id] = el; else delete dotRefs[id] }
function setListRef(id, el) { if (el) listRefs[id] = el; else delete listRefs[id] }
function setToastRef(id, el) { if (el) toastRefs[id] = el; else delete toastRefs[id] }

async function load() {
  providers.value = await api('/providers')
  await nextTick()
  animateCardsEntrance()
}

function animateCardsEntrance() {
  const els = Object.values(cardRefs)
  if (!els.length) return
  gsap.fromTo(els, { autoAlpha: 0, y: 16 }, { autoAlpha: 1, y: 0, duration: 0.45, stagger: 0.06, ease: 'power2.out' })
}

async function save() {
  const { name, base_url, api_key } = form.value
  if (!name || !base_url || !api_key) return
  providerNotice.value = ''
  await api('/providers/save', { name, base_url, api_key })
  form.value = { name: '', base_url: '', api_key: '' }
  showForm.value = false
  expandedId.value = null
  await load()
}

async function remove(id) {
  const el = cardRefs[id]
  if (el) {
    await gsap.to(el, { autoAlpha: 0, x: -20, height: 0, marginBottom: 0, paddingTop: 0, paddingBottom: 0, borderWidth: 0, duration: 0.25, ease: 'power2.in' }).then()
  }
  await api('/providers/delete', { id })
  if (expandedId.value === id) expandedId.value = null
  delete state[id]
  delete bodyRefs[id]
  delete cardRefs[id]
  delete dotRefs[id]
  await load()
}

async function toggleExpand(p) {
  if (expandedId.value === p.id) {
    expandedId.value = null
  } else {
    init(p.id)
    expandedId.value = p.id
    await nextTick()
  }
}

async function testConn(p) {
  const s = init(p.id)
  s.loading = 'conn'
  s.testMsg = null
  const r = p.auth_type === 'codex_oauth'
    ? await api('/providers/models', { provider_id: p.id }).then(data => ({
        ok: Array.isArray(data.models) && data.models.length > 0,
        msg: Array.isArray(data.models) && data.models.length > 0
          ? `OK - ${data.models.length} models available`
          : (data.error || 'Model list is empty'),
      }))
    : await api('/providers/test-connection', { base_url: p.base_url, api_key: p.api_key })
  s.testMsg = r
  s.connOk = r.ok
  s.loading = ''
  await nextTick()
  const dotEl = dotRefs[p.id]
  if (dotEl) gsap.fromTo(dotEl, { scale: 0 }, { scale: 1, duration: 0.35, ease: 'back.out(1.7)' })
  const toastEl = toastRefs[p.id]
  if (toastEl) animate(toastEl, { translateY: [-8, 0], opacity: [0, 1], duration: 300, ease: 'outCubic' })
}

async function fetchModels(p) {
  const s = init(p.id)
  s.loading = 'models'
  s.models = []
  s.filter = ''
  const r = p.auth_type === 'codex_oauth'
    ? await api('/providers/models', { provider_id: p.id })
    : await api('/providers/list-models', { base_url: p.base_url, api_key: p.api_key })
  s.models = r.models
  s.loading = ''
  await nextTick()
  const listEl = listRefs[p.id]
  if (listEl) {
    const items = listEl.querySelectorAll('.palette__item')
    animate(items, { translateX: [-12, 0], opacity: [0, 1], delay: animeStagger(18), duration: 280, ease: 'outCubic' })
  }
}

async function testModel(p, model_id) {
  const s = init(p.id)
  s.modelResults[model_id] = null
  const r = p.auth_type === 'codex_oauth'
    ? await api('/providers/test-model', { provider_id: p.id, model_id })
    : await api('/providers/test-model', { base_url: p.base_url, api_key: p.api_key, model_id })
  s.modelResults[model_id] = r
  await nextTick()
  const listEl = listRefs[p.id]
  if (listEl) {
    const item = listEl.querySelector(`[data-model="${CSS.escape(model_id)}"]`)
    if (item) {
      const resultEl = item.querySelector('.palette__result')
      if (resultEl) animate(resultEl, { scale: [0.5, 1], opacity: [0, 1], duration: 250, ease: 'outBack' })
    }
  }
}

function pricingState(p, model) {
  const s = init(p.id)
  if (!s.pricing[model]) {
    s.pricing[model] = reactive({
      loaded: false,
      loading: false,
      saving: false,
      rules: [defaultPricingRule()],
    })
  }
  return s.pricing[model]
}

async function loadModelPricing(p, model) {
  const ps = pricingState(p, model)
  ps.loading = true
  const r = await api('/providers/model-pricing/get', { provider_id: p.id, model_id: model })
  ps.rules = Array.isArray(r.pricing_rules) && r.pricing_rules.length
    ? r.pricing_rules.map(rule => ({ ...defaultPricingRule(), ...rule }))
    : [defaultPricingRule()]
  ps.loaded = true
  ps.loading = false
}

function addPricingRule(p, model) {
  pricingState(p, model).rules.push(defaultPricingRule())
}

function removePricingRule(p, model, index) {
  const ps = pricingState(p, model)
  ps.rules.splice(index, 1)
  if (!ps.rules.length) ps.rules.push(defaultPricingRule())
}

async function saveModelPricing(p, model) {
  const ps = pricingState(p, model)
  ps.saving = true
  await api('/providers/model-pricing/save', {
    provider_id: p.id,
    model_id: model,
    pricing_rules: ps.rules,
  })
  ps.saving = false
}

function filtered(s) {
  const models = s.models.map(modelName).filter(Boolean)
  if (!s.filter) return models
  const q = s.filter.toLowerCase()
  return models.filter(m => m.toLowerCase().includes(q))
}

function modelName(model) {
  if (typeof model === 'string') return model === 'None' ? '' : model
  if (!model || typeof model !== 'object') return ''
  return model.id || model.name || model.model || model.model_slug || model.api?.id || ''
}

function statusColor(s) {
  if (s?.connOk === true) return 'var(--ok)'
  if (s?.connOk === false) return 'var(--err)'
  return 'transparent'
}

function statusLabel(s) {
  if (s?.connOk === true) return 'connected'
  if (s?.connOk === false) return 'failed'
  return ''
}

onMounted(() => {
  bootstrap()
  gsapCtx = gsap.context(() => {}, undefined)
  applyFontScale(fontScale.value)
})
onUnmounted(() => { gsapCtx?.revert() })

watch(fontScale, applyFontScale)
watch(currentView, (v) => {
  if (v === 'providers' && !providers.value.length) load()
  if (v === 'mcp') mcpStore.load()
  if (v === 'lsp') lspStore.load()
})
</script>

<template>
  <div class="layout">
    <Sidebar
      :active="currentView"
      :workspaces="workspaces"
      :active-workspace="activeWorkspace"
      :sessions="sessionItems"
      :active-session="activeSession"
      :workspace-error="workspaceError"
      @navigate="currentView = $event"
      @add-workspace="addWorkspacePrompt"
      @create-session="createSession"
      @workspace-session="createSessionForWorkspace"
      @open-session="openSession"
      @rename-session="renameSession"
      @delete-session="removeSession"
      @delete-workspace="removeWorkspace"
    />

    <section class="shell">
      <header class="shell__bar">
        <div class="shell__crumbs">
          <span>StratumCode</span>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m9 18 6-6-6-6"/></svg>
          <strong>{{ workspaceLabel }}</strong>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m9 18 6-6-6-6"/></svg>
          <strong>{{ currentTitle }}</strong>
          <template v-if="sessionLabel">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m9 18 6-6-6-6"/></svg>
            <span class="shell__session">{{ sessionLabel }}</span>
          </template>
        </div>
        <div class="shell__runtime"><span></span>Local runtime</div>
      </header>

      <main class="main" :class="{ 'main--home': currentView === 'home' }">
      <!-- Providers view -->
      <div v-if="currentView === 'providers'" class="pm">
        <div class="pm__top">
          <div>
            <h1 class="pm__heading">Model providers</h1>
            <p class="pm__intro">Connect endpoints, inspect available models, and verify requests.</p>
          </div>
          <button v-if="!showForm" class="pm__add-btn" @click="showForm = true">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Add provider
          </button>
        </div>

        <!-- add form -->
        <Transition name="form-slide">
          <div v-if="showForm" class="pm__add">
            <div class="pm__add-head">
              <div>
                <h2>New connection</h2>
                <p>Start with a preset or enter a compatible endpoint.</p>
              </div>
              <button type="button" @click="showForm = false" aria-label="Close form">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m6 6 12 12M18 6 6 18"/></svg>
              </button>
            </div>
            <p class="pm__preset-label">Provider presets</p>
            <div class="pm__presets">
              <button
                v-for="p in presets"
                :key="p.name"
                class="pm__preset"
                :class="{ 'pm__preset--oauth': p.auth }"
                @click="selectPreset(p)"
              >
                <span class="pm__preset-avatar" :style="{ background: p.color }">{{ p.name[0] }}</span>
                <span class="pm__preset-body">
                  <span class="pm__preset-name">{{ p.name }}</span>
                  <span class="pm__preset-url">{{ p.url }}</span>
                </span>
              </button>
            </div>
            <p v-if="providerNotice" class="pm__provider-note">{{ providerNotice }}</p>
            <div v-if="codexOAuth.userCode" class="pm__oauth-box">
              <div>
                <span class="pm__oauth-label">OpenAI device code</span>
                <strong>{{ codexOAuth.userCode }}</strong>
                <a :href="codexOAuth.verificationUri" target="_blank" rel="noreferrer">{{ codexOAuth.verificationUri }}</a>
                <p v-if="codexOAuth.message">{{ codexOAuth.message }}</p>
              </div>
              <div class="pm__oauth-actions">
                <button type="button" class="pm__copy-btn" @click="copyCodexUserCode">Copy code</button>
                <button type="button" class="pm__save-btn" :disabled="codexOAuth.loading" @click="finishCodexOAuth">
                  {{ codexOAuth.loading ? 'Checking...' : 'Finish login' }}
                </button>
              </div>
            </div>

            <form class="pm__form" @submit.prevent="save">
              <label class="pm__field">
                <span>Name</span>
                <input v-model="form.name" placeholder="OpenAI" class="pm__input" />
              </label>
              <label class="pm__field pm__field--wide">
                <span>Base URL</span>
                <input v-model="form.base_url" placeholder="https://api.openai.com" class="pm__input pm__input--url" />
              </label>
              <label class="pm__field pm__field--key">
                <span>API key</span>
                <input v-model="form.api_key" placeholder="sk-..." type="password" class="pm__input pm__input--key" />
              </label>
              <div class="pm__key-row">
                <button class="pm__save-btn">Save provider</button>
              </div>
            </form>
          </div>
        </Transition>

        <StageModelSettings v-if="providers.length" :providers="providers" />

        <!-- empty -->
        <div class="pm__empty" v-if="!providers.length && !showForm">
          <span class="pm__empty-mark">+</span>
          <div>
            <h2>No providers connected</h2>
            <p>Add an API endpoint before starting a model-backed session.</p>
          </div>
        </div>

        <!-- provider rows -->
        <div v-if="providers.length" class="pm__table-head">
          <span>Provider</span>
          <span>Status</span>
          <span>Actions</span>
        </div>
        <TransitionGroup name="row" tag="div" class="pm__rows" v-if="providers.length">
          <div
            v-for="p in providers" :key="p.id"
            :ref="(el) => setCardRef(p.id, el)"
            class="pm__row"
            :class="{ 'is-open': expandedId === p.id }"
          >
            <div class="pm__row-main">
              <span class="pm__provider-mark">{{ p.name.slice(0, 2).toUpperCase() }}</span>
              <div class="pm__row-info" @click="toggleExpand(p)">
                <span class="pm__row-name">{{ p.name }}</span>
                <span class="pm__row-url">{{ p.base_url }}</span>
              </div>
              <div class="pm__row-status">
                <span :ref="(el) => setDotRef(p.id, el)" class="pm__row-dot" :style="{ background: statusColor(state[p.id]) }"></span>
                {{ statusLabel(state[p.id]) || 'Not tested' }}
              </div>
              <div class="pm__row-actions">
                <button class="pm__act" @click.stop="testConn(p)" :disabled="state[p.id]?.loading === 'conn'" title="Test connection">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
                </button>
                <button class="pm__act" @click.stop="fetchModels(p)" :disabled="state[p.id]?.loading === 'models'" title="List models">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                </button>
                <button class="pm__act pm__act--danger" @click.stop="remove(p.id)" title="Delete">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
                <button class="pm__act pm__act--chevron" @click.stop="toggleExpand(p)" :title="expandedId === p.id ? 'Collapse' : 'Expand'">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
                </button>
              </div>
            </div>

            <!-- toast -->
            <div :ref="(el) => setToastRef(p.id, el)" class="pm__toast" v-if="state[p.id]?.testMsg" :class="state[p.id].testMsg.ok ? 'pm__toast--ok' : 'pm__toast--err'">
              {{ state[p.id].testMsg.msg }}
            </div>

            <!-- model list -->
            <Transition name="slide">
              <div :ref="(el) => setBodyRef(p.id, el)" class="pm__models" v-if="expandedId === p.id">
                <div class="pm__models-head">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                  <input v-model="state[p.id].filter" placeholder="Filter models…" class="pm__models-search" />
                  <span class="pm__models-count" v-if="state[p.id].filter">{{ filtered(state[p.id]).length }}/{{ state[p.id].models.length }}</span>
                </div>
                <ul :ref="(el) => setListRef(p.id, el)" class="pm__models-list">
                  <li v-for="m in filtered(state[p.id])" :key="m" :data-model="m" class="pm__models-item">
                    <div class="pm__models-line" @click="testModel(p, m)">
                      <span class="pm__models-name">{{ m }}</span>
                      <span class="pm__models-badge" v-if="state[p.id].modelResults[m]" :class="state[p.id].modelResults[m].ok ? 'pm__models-badge--ok' : 'pm__models-badge--fail'">
                        {{ state[p.id].modelResults[m].ok ? 'OK' : 'FAIL' }}
                      </span>
                      <span class="pm__models-reply" v-if="state[p.id].modelResults[m]?.ok">{{ state[p.id].modelResults[m].msg }}</span>
                      <button class="pm__price-toggle" type="button" @click.stop="loadModelPricing(p, m)">
                        {{ state[p.id].pricing[m]?.loaded ? 'Pricing' : 'Set pricing' }}
                      </button>
                    </div>
                    <div v-if="state[p.id].pricing[m]?.loaded" class="pm__pricing" @click.stop>
                      <div class="pm__pricing-help">Prices are per 1M tokens. Times are UTC.</div>
                      <div v-for="(rule, index) in state[p.id].pricing[m].rules" :key="index" class="pm__pricing-rule">
                        <label>
                          <span>Currency</span>
                          <select v-model="rule.currency" class="pm__input" title="Billing currency">
                            <option>CNY</option>
                            <option>USD</option>
                            <option>GBP</option>
                          </select>
                        </label>
                        <label>
                          <span>UTC start</span>
                          <input v-model="rule.start" type="time" class="pm__input" title="UTC start time for this rate" />
                        </label>
                        <label>
                          <span>UTC end</span>
                          <input v-model="rule.end" type="time" class="pm__input" title="UTC end time for this rate" />
                        </label>
                        <label>
                          <span>Input / 1M</span>
                          <input v-model.number="rule.input_per_m" type="number" step="0.0001" class="pm__input" title="Prompt/input token price per 1M tokens" />
                        </label>
                        <label>
                          <span>Output / 1M</span>
                          <input v-model.number="rule.output_per_m" type="number" step="0.0001" class="pm__input" title="Completion/output token price per 1M tokens" />
                        </label>
                        <label>
                          <span>Cache hit / 1M</span>
                          <input v-model.number="rule.cache_per_m" type="number" step="0.0001" class="pm__input" title="Cached input token price per 1M tokens" />
                        </label>
                        <button class="pm__rule-remove" type="button" @click="removePricingRule(p, m, index)">×</button>
                      </div>
                      <div class="pm__pricing-actions">
                        <button type="button" class="pm__price-toggle" @click="addPricingRule(p, m)">Add rule</button>
                        <button type="button" class="pm__save-btn" :disabled="state[p.id].pricing[m].saving" @click="saveModelPricing(p, m)">
                          {{ state[p.id].pricing[m].saving ? 'Saving…' : 'Save pricing' }}
                        </button>
                      </div>
                    </div>
                  </li>
                </ul>
              </div>
            </Transition>
          </div>
        </TransitionGroup>
      </div>

      <!-- Home view -->
      <KeepAlive>
        <HomePage
          v-if="currentView === 'home'"
          :session="activeSession"
          :mcp-servers="mcpServers"
          :workspaces="workspaces"
          :active-workspace="activeWorkspace"
          :workspace-error="workspaceError"
          :sessions="sessionItems"
          @save-session-state="saveActiveSessionState"
          @add-workspace="addWorkspaceFromPanel"
          @activate-workspace="activateWorkspaceAndSession"
          @delete-workspace="removeWorkspace"
          @create-session="createSession"
          @open-session="openSession"
          @rename-session="renameSession"
          @delete-session="removeSession"
        />
      </KeepAlive>
      <McpPage
        v-if="currentView === 'mcp'"
        :servers="mcpServers"
        :loading="mcpLoading"
        :error="mcpError"
        @refresh="mcpStore.load"
        @start="mcpStore.start"
        @delete="mcpStore.remove"
        @configure="mcpStore.configure"
      />
      <LspPage
        v-if="currentView === 'lsp'"
        :servers="lspServers"
        :languages="lspLanguages"
        :loading="lspLoading"
        :busy-id="lspBusyId"
        :error="lspError"
        :mason="lspMason"
        :bootstrap="lspBootstrap"
        :bootstrap-steps="lspBootstrapSteps"
        :on-probe="lspStore.probe"
        @refresh="lspStore.load"
        @install-mason="lspStore.bootstrapMason"
        @install="lspStore.install"
        @uninstall="lspStore.uninstall"
        @enable="lspStore.enable"
        @configure="lspStore.configure"
      />
      <SkillsPage v-if="currentView === 'skills'" />
      <SettingsPage
        v-if="currentView === 'settings'"
        :settings="appSettings"
        :saving="settingsSaving"
        @save="saveSetting"
      />
      </main>
    </section>
  </div>
</template>

<style scoped>
.layout { display: flex; height: 100svh; overflow: hidden; }
.main { flex: 1; overflow-y: auto; background: var(--bg); }

/* ---- providers ---- */
.pm { max-width: 760px; margin: 0 auto; padding: 40px 48px; }

.pm__top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
.pm__heading { margin: 0; font-size: 18px; font-weight: 600; color: var(--text-h); letter-spacing: -0.01em; }

.pm__add-btn {
  display: inline-flex; align-items: center; gap: 6px;
  height: 32px; padding: 0 14px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--bg-raised); color: var(--text-h);
  font-size: 12px; font-weight: 500; font-family: var(--sans); cursor: pointer;
  transition: border-color 0.12s, background 0.12s;
}
.pm__add-btn:hover { border-color: var(--accent-border); background: var(--accent-bg); }

/* ---- add area ---- */
.pm__add {
  margin-bottom: 24px;
  padding: 20px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-raised);
  box-shadow: var(--shadow-sm);
}

.pm__preset-label { margin: 0 0 10px; font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; }

.pm__presets {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  margin-bottom: 16px;
}

.pm__preset {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--bg); cursor: pointer;
  transition: border-color 0.12s, box-shadow 0.12s;
  text-align: left;
}
.pm__preset:hover { border-color: var(--accent-border); box-shadow: var(--shadow-sm); }

.pm__preset-avatar {
  flex-shrink: 0;
  width: 28px; height: 28px; border-radius: 6px;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 12px; font-weight: 700; font-family: var(--sans);
}

.pm__preset-body { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.pm__preset-name { font-size: 12px; font-weight: 600; color: var(--text-h); line-height: 1.2; }
.pm__preset-url  { font-size: 10px; font-family: var(--mono); color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 160px; }

/* ---- form ---- */
.pm__form { display: flex; flex-direction: column; gap: 8px; }
.pm__input {
  height: 34px; padding: 0 12px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--bg); color: var(--text-h);
  font-size: 13px; font-family: var(--sans);
  transition: border-color 0.12s;
}
.pm__input::placeholder { color: var(--text-muted); }
.pm__input:focus { border-color: var(--accent-border); outline: none; box-shadow: 0 0 0 3px var(--accent-bg); }
.pm__input--url { font-family: var(--mono); font-size: 12px; }

.pm__key-row { display: flex; gap: 8px; }
.pm__input--key { flex: 1; }

.pm__save-btn {
  height: 34px; padding: 0 18px;
  border: none; border-radius: var(--radius-sm);
  background: var(--accent); color: #fff;
  font-size: 13px; font-weight: 500; font-family: var(--sans); cursor: pointer;
  transition: background 0.12s;
}
.pm__save-btn:hover { background: var(--accent-text); }

.pm__cancel-btn {
  height: 34px; padding: 0 14px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--bg); color: var(--text);
  font-size: 13px; font-family: var(--sans); cursor: pointer;
  transition: border-color 0.12s;
}
.pm__cancel-btn:hover { border-color: var(--text-muted); }

/* ---- empty ---- */
.pm__empty { padding: 40px 0; text-align: center; font-size: 13px; color: var(--text-muted); }

/* ---- rows ---- */
.pm__rows { display: flex; flex-direction: column; }

.pm__row {
  border-bottom: 1px solid var(--border);
  transition: background 0.1s;
}
.pm__row:hover { background: var(--accent-bg); }

.pm__row-main {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 4px;
}

.pm__row-dot   { flex-shrink: 0; width: 6px; height: 6px; border-radius: 50%; }
.pm__row-info  { flex: 1; min-width: 0; cursor: pointer; display: flex; flex-direction: column; gap: 1px; }
.pm__row-name  { font-size: 13px; font-weight: 600; color: var(--text-h); line-height: 1.3; }
.pm__row-url   { font-size: 11px; font-family: var(--mono); color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.pm__row-actions { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }

.pm__act {
  display: flex; align-items: center; justify-content: center;
  width: 30px; height: 28px;
  border: none; border-radius: var(--radius-sm);
  background: transparent; color: var(--text-muted); cursor: pointer;
  transition: background 0.1s, color 0.1s;
}
.pm__act:hover { background: var(--code-bg); color: var(--text-h); }
.pm__act:disabled { opacity: 0.35; cursor: default; }
.pm__act--danger:hover { background: var(--err-bg); color: var(--err); }
.pm__act--chevron svg { transition: transform 0.2s ease; }
.is-open .pm__act--chevron svg { transform: rotate(180deg); }

/* ---- toast ---- */
.pm__toast {
  margin: 0 4px 8px; padding: 8px 12px;
  border-radius: var(--radius-sm); font-family: var(--mono); font-size: 11px; line-height: 1.45;
  border: 1px solid;
}
.pm__toast--ok  { background: var(--ok-bg);  border-color: var(--ok-border);  color: var(--ok); }
.pm__toast--err { background: var(--err-bg); border-color: var(--err-border); color: var(--err); }

/* ---- model list ---- */
.pm__models {
  margin: 0 4px 8px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  overflow: hidden;
}

.pm__models-head {
  display: flex; align-items: center; gap: 8px;
  padding: 0 10px; height: 32px;
  border-bottom: 1px solid var(--border);
  background: var(--code-bg);
}
.pm__models-search {
  flex: 1; border: none; background: transparent;
  color: var(--text-h); font-family: var(--mono); font-size: 11px; outline: none;
}
.pm__models-search::placeholder { color: var(--text-muted); }
.pm__models-count { font-family: var(--mono); font-size: 10px; color: var(--text-muted); flex-shrink: 0; }

.pm__models-list { list-style: none; margin: 0; padding: 0; max-height: 200px; overflow-y: auto; }
.pm__models-item {
  display: flex; flex-direction: column; gap: 8px;
  padding: 6px 10px; cursor: pointer;
  font-family: var(--mono); font-size: 11px; color: var(--text-h);
  transition: background 0.08s;
  border-bottom: 1px solid var(--border);
}
.pm__models-item:last-child { border-bottom: none; }
.pm__models-item:hover { background: var(--code-bg-hover); }

.pm__models-line {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
}
.pm__models-name  { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pm__models-badge { flex-shrink: 0; font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 3px; }
.pm__models-badge--ok   { background: var(--ok-bg);  color: var(--ok); }
.pm__models-badge--fail { background: var(--err-bg); color: var(--err); }
.pm__models-reply { flex-shrink: 0; font-size: 10px; color: var(--text-muted); max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.pm__price-toggle {
  height: 24px;
  padding: 0 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--accent-text);
  background: #ffffff;
  font: 10px/1 var(--mono);
  cursor: pointer;
}

.pm__price-toggle:hover {
  border-color: var(--accent-border);
  background: var(--accent-bg);
}

.pm__pricing {
  width: 100%;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.74);
  cursor: default;
}

.pm__pricing-help {
  margin-bottom: 8px;
  color: var(--text-muted);
  font: 10px/1.4 var(--mono);
}

.pm__pricing-rule {
  display: grid;
  grid-template-columns: 76px 96px 96px repeat(3, minmax(96px, 1fr)) 28px;
  gap: 7px;
  align-items: end;
  margin-bottom: 7px;
}

.pm__pricing-rule label {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.pm__pricing-rule label > span {
  color: var(--text-muted);
  font: 9px/1 var(--mono);
}

.pm__pricing-rule .pm__input {
  height: 30px;
  min-width: 0;
  font-size: 10px;
}

.pm__rule-remove {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--err);
  background: var(--err-bg);
  cursor: pointer;
}

.pm__pricing-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.pm__pricing-actions .pm__save-btn {
  height: 24px;
  padding: 0 10px;
  font: 10px/1 var(--mono);
}

/* ---- transitions ---- */
.form-slide-enter-active { transition: all 0.25s ease; overflow: hidden; }
.form-slide-leave-active { transition: all 0.15s ease; overflow: hidden; }
.form-slide-enter-from, .form-slide-leave-to { opacity: 0; max-height: 0; margin-bottom: 0; }
.form-slide-enter-to, .form-slide-leave-from { max-height: 500px; }

.row-enter-active { transition: all 0.3s ease; }
.row-leave-active { transition: all 0.2s ease; position: absolute; }
.row-enter-from { opacity: 0; transform: translateY(-8px); }
.row-leave-to   { opacity: 0; transform: translateX(-12px); }
.row-move { transition: transform 0.25s ease; }

.slide-enter-active { transition: all 0.2s ease; overflow: hidden; }
.slide-leave-active { transition: all 0.12s ease; overflow: hidden; }
.slide-enter-from, .slide-leave-to { opacity: 0; max-height: 0; }
.slide-enter-to, .slide-leave-from { max-height: 400px; }

/* visual system override */
.layout {
  display: flex;
  height: 100svh;
  overflow: hidden;
  background: var(--bg);
}

.shell {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.shell__bar {
  display: flex;
  min-height: 48px;
  flex: 0 0 48px;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.94);
}

.shell__crumbs,
.shell__runtime {
  display: flex;
  align-items: center;
}

.shell__crumbs {
  gap: 7px;
  min-width: 0;
  overflow: hidden;
  color: var(--text-muted);
  font-size: 11px;
}

.shell__crumbs strong {
  min-width: 0;
  overflow: hidden;
  color: var(--text-h);
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shell__session {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shell__runtime {
  gap: 7px;
  color: var(--text-muted);
  font: 10px/1 var(--mono);
}

.shell__runtime span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
}

.main {
  min-height: 0;
  flex: 1;
  overflow: auto;
  background:
    linear-gradient(rgba(23, 86, 209, 0.035) 1px, transparent 1px),
    var(--bg);
  background-size: 100% 48px;
}

.main--home {
  display: flex;
  overflow: hidden;
}

.pm {
  width: min(1120px, 100%);
  max-width: none;
  margin: 0 auto;
  padding: 48px 56px 80px;
}

.pm__top {
  align-items: flex-end;
  margin-bottom: 38px;
}

.pm__heading {
  margin: 0;
  color: var(--text-h);
  font: 570 clamp(26px, 3vw, 34px)/1.05 var(--heading);
  letter-spacing: -0.035em;
}

.pm__intro {
  max-width: 520px;
  margin: 9px 0 0;
  color: var(--text-muted);
  font-size: 12px;
}

.pm__add-btn {
  height: 36px;
  padding: 0 15px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  color: #ffffff;
  background: var(--accent);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.13);
  font-size: 11px;
  font-weight: 600;
}

.pm__add-btn:hover {
  border-color: var(--accent-hover);
  background: var(--accent-hover);
}

.pm__add {
  margin-bottom: 40px;
  padding: 24px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  background: var(--bg-raised);
  box-shadow: var(--shadow);
}

.pm__add-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 24px;
}

.pm__add-head h2,
.pm__empty h2 {
  margin: 0;
  color: var(--text-h);
  font: 560 18px/1.2 var(--heading);
  letter-spacing: -0.02em;
}

.pm__add-head p,
.pm__empty p {
  margin: 5px 0 0;
  color: var(--text-muted);
  font-size: 11px;
}

.pm__add-head > button {
  display: grid;
  width: 30px;
  height: 30px;
  padding: 0;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  background: transparent;
  cursor: pointer;
}

.pm__add-head > button:hover {
  color: var(--text-h);
  border-color: var(--border-strong);
  background: var(--code-bg-hover);
}

.pm__preset-label {
  margin: 0 0 10px;
  color: var(--text-muted);
  font: 550 10px/1 var(--mono);
  letter-spacing: 0;
  text-transform: none;
}

.pm__presets {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
  margin-bottom: 22px;
}

.pm__preset {
  min-width: 0;
  padding: 9px;
  border-color: var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg);
}

.pm__preset:hover {
  border-color: var(--accent-border);
  background: var(--accent-bg);
  box-shadow: none;
}

.pm__preset-avatar {
  width: 25px;
  height: 25px;
  border-radius: 7px;
  filter: saturate(0.68);
}

.pm__preset-name {
  color: var(--text-h);
  font-size: 11px;
  font-weight: 600;
}

.pm__preset-url {
  max-width: 190px;
  color: var(--text-muted);
  font-size: 9px;
}

.pm__preset--oauth {
  background: #f8fafc;
}

.pm__preset--oauth .pm__preset-url {
  color: #b45309;
}

.pm__provider-note {
  margin: 10px 0 0;
  padding: 10px 12px;
  border: 1px solid #f3d08a;
  border-radius: var(--radius-sm);
  color: #7a4b00;
  background: #fff7e6;
  font-size: 11px;
  line-height: 1.5;
}

.pm__oauth-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 10px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--code-bg);
}

.pm__oauth-box > div {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.pm__oauth-label {
  color: var(--text-muted);
  font-size: 10px;
}

.pm__oauth-box strong {
  color: var(--text-h);
  font: 700 18px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  letter-spacing: 0.08em;
}

.pm__oauth-box a,
.pm__oauth-box p {
  overflow-wrap: anywhere;
  color: var(--text-muted);
  font-size: 10px;
}

.pm__oauth-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
}

.pm__copy-btn {
  height: 38px;
  padding: 0 13px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-h);
  background: #ffffff;
  white-space: nowrap;
}

.pm__copy-btn:hover {
  background: var(--code-bg-hover);
}

.pm__form {
  display: grid;
  grid-template-columns: 0.75fr 1.5fr 1.25fr auto;
  gap: 10px;
  align-items: end;
  padding-top: 20px;
  border-top: 1px solid var(--border);
}

.pm__field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 7px;
}

.pm__field > span {
  color: #5f7193;
  font-size: 10px;
  font-weight: 550;
}

.pm__input {
  width: 100%;
  height: 38px;
  padding: 0 11px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-h);
  background: var(--code-bg);
}

.pm__input::placeholder { color: #93a0b8; }
.pm__input:focus {
  border-color: var(--accent-border);
  box-shadow: 0 0 0 3px var(--accent-bg);
}

.pm__key-row { display: flex; }

.pm__save-btn {
  height: 38px;
  padding: 0 16px;
  border-radius: var(--radius-sm);
  color: #ffffff;
  background: var(--accent);
  white-space: nowrap;
}

.pm__save-btn:hover { background: var(--accent-hover); }

.pm__empty {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 34px 4px;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  text-align: left;
}

.pm__empty-mark {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  place-items: center;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  color: var(--accent-text);
  background: var(--accent-bg);
  font: 300 24px/1 var(--sans);
}

.pm__table-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 150px 148px;
  gap: 14px;
  padding: 0 4px 9px 58px;
  color: var(--text-muted);
  font: 9px/1 var(--mono);
}

.pm__table-head span:last-child { text-align: right; }

.pm__rows {
  border-top: 1px solid var(--border-strong);
}

.pm__row {
  border-bottom-color: var(--border);
  background: rgba(255, 255, 255, 0.82);
}

.pm__row:hover { background: #edf3ff; }

.pm__row-main {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) 150px 148px;
  gap: 14px;
  min-height: 64px;
  padding: 9px 4px;
}

.pm__provider-mark {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--accent-text);
  background: var(--accent-bg);
  font: 600 9px/1 var(--mono);
}

.pm__row-info {
  align-self: center;
  gap: 2px;
}

.pm__row-name {
  color: var(--text-h);
  font-size: 12px;
}

.pm__row-url {
  color: var(--text-muted);
  font-size: 10px;
}

.pm__row-status {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--text-muted);
  font-size: 10px;
}

.pm__row-dot {
  width: 6px;
  height: 6px;
  border: 1px solid var(--border-strong);
  background: transparent;
}

.pm__row-actions {
  justify-content: flex-end;
  gap: 3px;
}

.pm__act {
  width: 31px;
  height: 31px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
}

.pm__act:hover {
  border-color: var(--border);
  color: var(--text-h);
  background: var(--code-bg-hover);
}

.pm__toast {
  margin: 0 4px 10px 58px;
  border-radius: var(--radius-sm);
}

.pm__models {
  margin: 0 4px 12px 58px;
  border-color: var(--border);
  border-radius: var(--radius);
  background: var(--code-bg);
}

.pm__models-head {
  height: 38px;
  background: #eef4ff;
}

.pm__models-item {
  padding: 8px 11px;
  border-bottom-color: var(--border);
  color: var(--text);
}

.pm__models-item:hover { background: var(--code-bg-hover); }

@media (max-width: 900px) {
  .pm { padding: 36px 28px 64px; }
  .pm__presets { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .pm__form { grid-template-columns: 1fr 1fr; }
  .pm__field--key { grid-column: 1 / 2; }
  .pm__key-row { grid-column: 2 / 3; }
  .pm__save-btn { width: 100%; }
  .pm__table-head { display: none; }
  .pm__row-main { grid-template-columns: 40px minmax(0, 1fr) auto; }
  .pm__row-status { display: none; }
}

@media (max-width: 620px) {
  .shell__bar { padding-inline: 14px; }
  .shell__runtime { display: none; }
  .pm { padding: 28px 18px 48px; }
  .pm__top { align-items: flex-start; gap: 18px; }
  .pm__add-btn { flex: 0 0 auto; }
  .pm__presets { grid-template-columns: 1fr; }
  .pm__form { grid-template-columns: 1fr; }
  .pm__field,
  .pm__field--key,
  .pm__key-row { grid-column: 1; }
  .pm__row-main { grid-template-columns: 36px minmax(0, 1fr) auto; gap: 8px; }
  .pm__provider-mark { width: 30px; height: 30px; }
  .pm__row-actions .pm__act:not(.pm__act--chevron) { display: none; }
  .pm__toast,
  .pm__models { margin-left: 4px; }
}
</style>
