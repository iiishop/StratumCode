<script setup>
import { ref, reactive, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { gsap } from 'gsap'
import { animate, stagger as animeStagger } from 'animejs'
import Sidebar from './components/Sidebar.vue'
import HomePage from './components/HomePage.vue'

const currentView = ref('home')
const providers = ref([])
const showForm = ref(false)
const form = ref({ name: '', base_url: '', api_key: '' })
const expandedId = ref(null)
const state = reactive({})
const bodyRefs = reactive({})
const cardRefs = reactive({})
const dotRefs = reactive({})
const listRefs = reactive({})
const toastRefs = reactive({})

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
]

function dedupName(name) {
  const existing = providers.value.map(p => p.name)
  if (!existing.includes(name)) return name
  let i = 2
  while (existing.includes(`${name}-${i}`)) i++
  return `${name}-${i}`
}

function selectPreset(p) {
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

function init(id) {
  if (!state[id]) {
    state[id] = reactive({ testMsg: null, models: [], modelResults: {}, loading: '', filter: '', connOk: null })
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
  const r = await api('/providers/test-connection', { base_url: p.base_url, api_key: p.api_key })
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
  const r = await api('/providers/list-models', { base_url: p.base_url, api_key: p.api_key })
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
  const r = await api('/providers/test-model', { base_url: p.base_url, api_key: p.api_key, model_id })
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

function filtered(s) {
  if (!s.filter) return s.models
  const q = s.filter.toLowerCase()
  return s.models.filter(m => m.toLowerCase().includes(q))
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

onMounted(() => { gsapCtx = gsap.context(() => {}, undefined) })
onUnmounted(() => { gsapCtx?.revert() })

watch(currentView, (v) => { if (v === 'providers' && !providers.value.length) load() })
</script>

<template>
  <div class="layout">
    <Sidebar :active="currentView" @navigate="currentView = $event" />

    <main class="main">
      <!-- Providers view -->
      <div v-if="currentView === 'providers'" class="pm">
        <div class="pm__top">
          <h2 class="pm__heading">Providers</h2>
          <button v-if="!showForm" class="pm__add-btn" @click="showForm = true">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Add
          </button>
        </div>

        <!-- add form -->
        <Transition name="form-slide">
          <div v-if="showForm" class="pm__add">
            <p class="pm__preset-label">Quick setup</p>
            <div class="pm__presets">
              <button v-for="p in presets" :key="p.name" class="pm__preset" @click="selectPreset(p)">
                <span class="pm__preset-avatar" :style="{ background: p.color }">{{ p.name[0] }}</span>
                <span class="pm__preset-body">
                  <span class="pm__preset-name">{{ p.name }}</span>
                  <span class="pm__preset-url">{{ p.url }}</span>
                </span>
              </button>
            </div>

            <form class="pm__form" @submit.prevent="save">
              <input v-model="form.name" placeholder="Provider name" class="pm__input" />
              <input v-model="form.base_url" placeholder="https://api.openai.com" class="pm__input pm__input--url" />
              <div class="pm__key-row">
                <input v-model="form.api_key" placeholder="API Key" type="password" class="pm__input pm__input--key" />
                <button class="pm__save-btn">Save</button>
                <button type="button" class="pm__cancel-btn" @click="showForm = false">Cancel</button>
              </div>
            </form>
          </div>
        </Transition>

        <!-- empty -->
        <div class="pm__empty" v-if="!providers.length && !showForm">
          <p>No providers yet. Click "Add" to connect your first API endpoint.</p>
        </div>

        <!-- provider rows -->
        <TransitionGroup name="row" tag="div" class="pm__rows" v-if="providers.length">
          <div
            v-for="p in providers" :key="p.id"
            :ref="(el) => setCardRef(p.id, el)"
            class="pm__row"
            :class="{ 'is-open': expandedId === p.id }"
          >
            <div class="pm__row-main">
              <span :ref="(el) => setDotRef(p.id, el)" class="pm__row-dot" :style="{ background: statusColor(state[p.id]) }"></span>
              <div class="pm__row-info" @click="toggleExpand(p)">
                <span class="pm__row-name">{{ p.name }}</span>
                <span class="pm__row-url">{{ p.base_url }}</span>
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
                  <li v-for="m in filtered(state[p.id])" :key="m" :data-model="m" class="pm__models-item" @click="testModel(p, m)">
                    <span class="pm__models-name">{{ m }}</span>
                    <span class="pm__models-badge" v-if="state[p.id].modelResults[m]" :class="state[p.id].modelResults[m].ok ? 'pm__models-badge--ok' : 'pm__models-badge--fail'">
                      {{ state[p.id].modelResults[m].ok ? 'OK' : 'FAIL' }}
                    </span>
                    <span class="pm__models-reply" v-if="state[p.id].modelResults[m]?.ok">{{ state[p.id].modelResults[m].msg }}</span>
                  </li>
                </ul>
              </div>
            </Transition>
          </div>
        </TransitionGroup>
      </div>

      <!-- Home view -->
      <HomePage v-if="currentView === 'home'" />
    </main>
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
  display: flex; align-items: center; gap: 10px;
  padding: 6px 10px; cursor: pointer;
  font-family: var(--mono); font-size: 11px; color: var(--text-h);
  transition: background 0.08s;
  border-bottom: 1px solid var(--border);
}
.pm__models-item:last-child { border-bottom: none; }
.pm__models-item:hover { background: var(--code-bg-hover); }

.pm__models-name  { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pm__models-badge { flex-shrink: 0; font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 3px; }
.pm__models-badge--ok   { background: var(--ok-bg);  color: var(--ok); }
.pm__models-badge--fail { background: var(--err-bg); color: var(--err); }
.pm__models-reply { flex-shrink: 0; font-size: 10px; color: var(--text-muted); max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

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
</style>
