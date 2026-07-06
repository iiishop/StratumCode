<script setup>
import { ref, reactive, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { gsap } from 'gsap'
import { animate, stagger as animeStagger } from 'animejs'

const providers = ref([])
const form = ref({ name: '', base_url: '', api_key: '' })
const expandedId = ref(null)
const state = reactive({})
const bodyRefs = reactive({})
const cardRefs = reactive({})
const dotRefs = reactive({})
const listRefs = reactive({})
const toastRefs = reactive({})

let gsapCtx

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

function setCardRef(id, el) {
  if (el) cardRefs[id] = el
  else delete cardRefs[id]
}

function setBodyRef(id, el) {
  if (el) bodyRefs[id] = el
  else delete bodyRefs[id]
}

function setDotRef(id, el) {
  if (el) dotRefs[id] = el
  else delete dotRefs[id]
}

function setListRef(id, el) {
  if (el) listRefs[id] = el
  else delete listRefs[id]
}

function setToastRef(id, el) {
  if (el) toastRefs[id] = el
  else delete toastRefs[id]
}

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
  const wasOpen = expandedId.value === p.id
  const id = p.id

  if (wasOpen) {
    const bodyEl = bodyRefs[id]
    if (bodyEl) {
      await gsap.to(bodyEl, { height: 0, autoAlpha: 0, paddingTop: 0, paddingBottom: 0, duration: 0.2, ease: 'power2.in' }).then()
    }
    expandedId.value = null
  } else {
    init(id)
    expandedId.value = id
    await nextTick()
    const bodyEl = bodyRefs[id]
    if (bodyEl) {
      gsap.fromTo(bodyEl, { height: 0, autoAlpha: 0 }, { height: 'auto', autoAlpha: 1, duration: 0.3, ease: 'power2.out' })
    }
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
  if (dotEl) {
    gsap.fromTo(dotEl, { scale: 0 }, { scale: 1, duration: 0.35, ease: 'back.out(1.7)' })
  }
  const toastEl = toastRefs[p.id]
  if (toastEl) {
    animate(toastEl, { translateY: [-8, 0], opacity: [0, 1], duration: 300, ease: 'outCubic' })
  }
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
    animate(items, {
      translateX: [-12, 0],
      opacity: [0, 1],
      delay: animeStagger(18),
      duration: 280,
      ease: 'outCubic',
    })
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
      if (resultEl) {
        animate(resultEl, { scale: [0.5, 1], opacity: [0, 1], duration: 250, ease: 'outBack' })
      }
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

onMounted(() => {
  gsapCtx = gsap.context(() => {}, undefined)
  load()
})

onUnmounted(() => {
  gsapCtx?.revert()
})
</script>

<template>
  <div class="app-shell">

    <!-- header -->
    <header class="shell-header">
      <div class="shell-brand">
        <span class="shell-brand__icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
            <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
            <line x1="12" y1="22.08" x2="12" y2="12"/>
          </svg>
        </span>
        <span class="shell-brand__text">Providers</span>
        <span class="shell-brand__count" v-if="providers.length">{{ providers.length }}</span>
      </div>
    </header>

    <!-- toolbar -->
    <form class="toolbar" @submit.prevent="save">
      <div class="toolbar__fields">
        <input
          v-model="form.name"
          placeholder="Provider 名称"
          class="toolbar__input"
          aria-label="Provider name"
        />
        <input
          v-model="form.base_url"
          placeholder="https://api.openai.com"
          class="toolbar__input toolbar__input--url"
          aria-label="Base URL"
        />
        <input
          v-model="form.api_key"
          placeholder="API Key"
          type="password"
          class="toolbar__input"
          aria-label="API key"
        />
      </div>
      <button class="toolbar__btn">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        添加
      </button>
    </form>

    <!-- empty -->
    <div class="empty-state" v-if="!providers.length">
      <div class="empty-state__icon">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" stroke-width="1.2" stroke-linecap="round">
          <rect x="3" y="3" width="18" height="18" rx="3"/>
          <line x1="9" y1="9" x2="15" y2="9"/>
          <line x1="9" y1="13" x2="13" y2="13"/>
          <line x1="9" y1="17" x2="11" y2="17"/>
        </svg>
      </div>
      <p class="empty-state__title">尚未添加 Provider</p>
      <p class="empty-state__hint">输入 Provider 名称、API 地址和 Key，点击添加即可连接</p>
    </div>

    <!-- list -->
    <TransitionGroup name="list" tag="div" class="list" v-else>
      <div
        v-for="p in providers" :key="p.id"
        :ref="(el) => setCardRef(p.id, el)"
        class="card"
        :class="{
          'is-open': expandedId === p.id,
          'is-ok': state[p.id]?.connOk === true,
          'is-err': state[p.id]?.connOk === false,
        }"
      >
        <div class="card__head" @click="toggleExpand(p)">
          <span
            :ref="(el) => setDotRef(p.id, el)"
            class="card__status"
            :style="{ background: statusColor(state[p.id]) }"
            :title="statusLabel(state[p.id])"
          ></span>
          <div class="card__info">
            <span class="card__name">{{ p.name }}</span>
            <span class="card__url">{{ p.base_url }}</span>
          </div>
          <span class="card__chevron">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
          </span>
        </div>

        <div
          :ref="(el) => setBodyRef(p.id, el)"
          class="card__body"
          :style="{ display: expandedId === p.id ? 'block' : 'none' }"
        >
          <div v-if="expandedId === p.id">
            <div class="card__actions">
              <button
                class="btn"
                @click.stop="testConn(p)"
                :disabled="state[p.id].loading === 'conn'"
              >
                <span v-if="state[p.id].loading === 'conn'" class="btn__spinner"></span>
                {{ state[p.id].loading === 'conn' ? '测试中…' : '测试连接' }}
              </button>
              <button
                class="btn"
                @click.stop="fetchModels(p)"
                :disabled="state[p.id].loading === 'models'"
              >
                <span v-if="state[p.id].loading === 'models'" class="btn__spinner"></span>
                {{ state[p.id].loading === 'models' ? '加载中…' : '加载模型列表' }}
              </button>
              <button class="btn btn--danger" @click.stop="remove(p.id)">删除</button>
            </div>

            <div
              :ref="(el) => setToastRef(p.id, el)"
              class="toast"
              v-if="state[p.id].testMsg"
              :class="{
                'toast--ok': state[p.id].testMsg.ok,
                'toast--err': !state[p.id].testMsg.ok,
              }"
            >
              <span class="toast__dot" :class="state[p.id].testMsg.ok ? 'toast__dot--ok' : 'toast__dot--err'"></span>
              {{ state[p.id].testMsg.msg }}
            </div>

            <div class="palette" v-if="state[p.id].models.length">
              <div class="palette__search">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <input
                  v-model="state[p.id].filter"
                  placeholder="搜索模型…"
                  class="palette__input"
                />
                <span class="palette__search-count" v-if="state[p.id].filter">
                  {{ filtered(state[p.id]).length }}/{{ state[p.id].models.length }}
                </span>
              </div>
              <ul :ref="(el) => setListRef(p.id, el)" class="palette__list">
                <li
                  v-for="m in filtered(state[p.id])" :key="m"
                  :data-model="m"
                  class="palette__item"
                  @click="testModel(p, m)"
                >
                  <span class="palette__model">{{ m }}</span>
                  <span
                    class="palette__result"
                    v-if="state[p.id].modelResults[m]"
                    :class="{
                      'palette__result--ok': state[p.id].modelResults[m].ok,
                      'palette__result--fail': !state[p.id].modelResults[m].ok
                    }"
                  >
                    {{ state[p.id].modelResults[m].ok ? 'OK' : 'FAIL' }}
                  </span>
                  <span class="palette__reply" v-if="state[p.id].modelResults[m]?.ok">
                    {{ state[p.id].modelResults[m].msg }}
                  </span>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </TransitionGroup>

  </div>
</template>

<style scoped>
/* ---- shell ---- */
.app-shell {
  max-width: 720px;
  margin: 0 auto;
}

.shell-header {
  margin-bottom: 24px;
}

.shell-brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.shell-brand__icon {
  color: var(--accent);
  display: flex;
}

.shell-brand__text {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-h);
  letter-spacing: -0.01em;
}

.shell-brand__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 20px;
  min-width: 20px;
  padding: 0 6px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-text);
  background: var(--accent-bg);
  font-family: var(--mono);
}

/* ---- toolbar ---- */
.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.toolbar__fields {
  display: flex;
  flex: 1;
  gap: 8px;
}

.toolbar__input {
  flex: 1 1 0;
  min-width: 0;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-raised);
  color: var(--text-h);
  font-size: 13px;
  font-family: var(--sans);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
  box-shadow: var(--shadow-sm);
}

.toolbar__input::placeholder {
  color: var(--text-muted);
}

.toolbar__input:focus {
  border-color: var(--border-focus);
  box-shadow: 0 0 0 3px var(--accent-bg);
  outline: none;
}

.toolbar__input--url {
  flex: 2 2 0;
  font-family: var(--mono);
  font-size: 12px;
}

.toolbar__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 16px;
  border: none;
  border-radius: var(--radius);
  background: var(--accent);
  color: #fff;
  font-size: 13px;
  font-weight: 500;
  font-family: var(--sans);
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--transition-fast), transform var(--transition-fast);
  box-shadow: var(--shadow-sm);
}

.toolbar__btn:hover {
  background: var(--accent-text);
}

.toolbar__btn:active {
  transform: scale(0.97);
}

/* ---- empty state ---- */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 64px 24px;
  text-align: center;
  border: 1px dashed var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-raised);
}

.empty-state__icon {
  margin-bottom: 4px;
  opacity: 0.5;
}

.empty-state__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-h);
  margin: 0;
}

.empty-state__hint {
  font-size: 13px;
  color: var(--text-muted);
  margin: 0;
  max-width: 340px;
}

/* ---- list ---- */
.list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* ---- TransitionGroup list ---- */
.list-enter-active {
  transition: all 0.35s ease;
}
.list-leave-active {
  transition: all 0.25s ease;
  position: absolute;
}
.list-enter-from {
  opacity: 0;
  transform: translateY(12px);
}
.list-leave-to {
  opacity: 0;
  transform: translateX(-12px);
}
.list-move {
  transition: transform 0.3s ease;
}

/* ---- card ---- */
.card {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-raised);
  box-shadow: var(--shadow-sm);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
  overflow: hidden;
}

.card:hover {
  box-shadow: var(--shadow);
}

.card.is-open {
  border-color: var(--border-focus);
  box-shadow: var(--shadow-md), 0 0 0 1px var(--accent-border);
}

.card.is-ok {
  border-left: 3px solid var(--ok);
}

.card.is-err {
  border-left: 3px solid var(--err);
}

.card__head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
}

.card__status {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.card__info {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.card__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-h);
  line-height: 1.3;
}

.card__url {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card__chevron {
  flex-shrink: 0;
  color: var(--text-muted);
  display: flex;
  transition: transform var(--transition-slow);
}

.is-open .card__chevron {
  transform: rotate(180deg);
  color: var(--accent);
}

/* ---- card body ---- */
.card__body {
  padding: 0 16px 16px;
}

.card__actions {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

/* ---- btn ---- */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--text-h);
  font-size: 12px;
  font-family: var(--sans);
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: border-color var(--transition-fast), background var(--transition-fast), color var(--transition-fast);
}

.btn:hover {
  border-color: var(--border-focus);
  background: var(--accent-bg);
  color: var(--accent-text);
}

.btn:active {
  transform: scale(0.97);
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.btn--danger:hover {
  border-color: var(--err);
  background: var(--err-bg);
  color: var(--err);
}

.btn__spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ---- toast ---- */
.toast {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.45;
  margin-bottom: 12px;
  border: 1px solid var(--border);
}

.toast--ok {
  background: var(--ok-bg);
  border-color: var(--ok-border);
  color: var(--ok);
}

.toast--err {
  background: var(--err-bg);
  border-color: var(--err-border);
  color: var(--err);
}

.toast__dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-top: 5px;
}

.toast__dot--ok { background: var(--ok); }
.toast__dot--err { background: var(--err); }

/* ---- palette ---- */
.palette {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.palette__search {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  height: 34px;
  border-bottom: 1px solid var(--border);
  background: var(--code-bg);
}

.palette__input {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--text-h);
  font-family: var(--mono);
  font-size: 12px;
  outline: none;
}

.palette__input::placeholder {
  color: var(--text-muted);
}

.palette__search-count {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-muted);
  flex-shrink: 0;
}

.palette__list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 240px;
  overflow-y: auto;
}

.palette__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  cursor: pointer;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text-h);
  transition: background var(--transition-fast);
  border-bottom: 1px solid var(--border);
}

.palette__item:last-child {
  border-bottom: none;
}

.palette__item:hover {
  background: var(--code-bg-hover);
}

.palette__item:active {
  background: var(--accent-bg);
}

.palette__model {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.palette__result {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 3px;
}

.palette__result--ok {
  background: var(--ok-bg);
  color: var(--ok);
}

.palette__result--fail {
  background: var(--err-bg);
  color: var(--err);
}

.palette__reply {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-muted);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
