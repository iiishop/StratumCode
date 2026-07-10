<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  servers: { type: Array, default: () => [] },
  languages: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  busyId: { type: String, default: '' },
  error: { type: String, default: '' },
  onProbe: { type: Function, default: null },
})
const emit = defineEmits(['refresh', 'install', 'uninstall', 'enable', 'configure', 'probe'])

const search = ref('')
const selectedLang = ref('')
const expandedId = ref('')
const probeResults = ref({})

const filtered = computed(() => {
  let items = props.servers
  if (selectedLang.value) {
    items = items.filter(s => s.languages.includes(selectedLang.value))
  }
  if (search.value.trim()) {
    const q = search.value.trim().toLowerCase()
    items = items.filter(s =>
      s.name.toLowerCase().includes(q)
      || s.display_name.toLowerCase().includes(q)
      || s.description.toLowerCase().includes(q)
      || s.languages.some(l => l.includes(q))
    )
  }
  return items
})

const grouped = computed(() => {
  if (selectedLang.value) return null
  const groups = {}
  for (const server of filtered.value) {
    const lang = server.languages[0] || 'other'
    if (!groups[lang]) groups[lang] = []
    groups[lang].push(server)
  }
  return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b))
})

watch(() => props.servers, () => { expandedId.value = ''; probeResults.value = {} })

function toggleExpand(id) {
  expandedId.value = expandedId.value === id ? '' : id
}

async function doProbe(server) {
  if (!props.onProbe) return
  const result = await props.onProbe(server.name)
  probeResults.value[server.name] = result
}

function probeFor(name) {
  return probeResults.value[name]?.probe
}

function actionButton(server) {
  if (props.busyId === server.name) return '...'
  if (server.installed) return 'Uninstall'
  return 'Install'
}

function onAction(server) {
  if (server.installed) {
    emit('uninstall', server.name)
  } else {
    emit('install', server.name)
  }
}

function toggleEnabled(server) {
  emit('enable', server.name, !server.enabled)
}
</script>

<template>
  <main class="lsp-page">
    <section class="lsp-page__head">
      <div>
        <h1>LSP</h1>
        <p>Language servers from the Mason registry. Install to enable intelligent code features.</p>
      </div>
      <button type="button" :disabled="loading" @click="emit('refresh')">
        {{ loading ? 'Loading' : 'Refresh' }}
      </button>
    </section>

    <div class="lsp-page__controls">
      <div class="lsp-page__search">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input v-model="search" placeholder="Search servers or languages..." />
      </div>
      <div class="lsp-page__lang-tabs">
        <button :class="{ active: !selectedLang }" @click="selectedLang = ''">All</button>
        <button
          v-for="lang in languages"
          :key="lang"
          :class="{ active: selectedLang === lang }"
          @click="selectedLang = lang"
        >{{ lang }}</button>
      </div>
    </div>

    <p v-if="error" class="lsp-page__error">{{ error }}</p>

    <template v-if="grouped && !selectedLang">
      <section v-for="[lang, items] in grouped" :key="lang" class="lsp-group">
        <h3 class="lsp-group__title">{{ lang }}</h3>
        <article v-for="server in items" :key="server.name" class="lsp-server" :class="{ 'is-installed': server.installed, 'is-expanded': expandedId === server.name }">
          <div class="lsp-server__main" @click="toggleExpand(server.name)">
            <span class="lsp-server__lang">{{ server.languages.slice(0, 2).join(', ') }}</span>
            <div class="lsp-server__info">
              <strong>{{ server.display_name }}</strong>
              <small>{{ server.name }}</small>
            </div>
            <div class="lsp-server__status">
              <span v-if="server.installed" class="lsp-server__dot is-ok"></span>
              <span v-else class="lsp-server__dot"></span>
              <span class="lsp-server__state">{{ server.installed ? server.install_version || 'installed' : 'not installed' }}</span>
            </div>
            <div class="lsp-server__actions">
              <button
                type="button"
                class="lsp-server__action"
                :class="{ 'is-danger': server.installed }"
                :disabled="!!busyId"
                @click.stop="onAction(server)"
              >{{ actionButton(server) }}</button>
              <button
                v-if="server.installed"
                type="button"
                class="lsp-server__toggle"
                :class="{ 'is-on': server.enabled }"
                :disabled="!!busyId"
                @click.stop="toggleEnabled(server)"
                :title="server.enabled ? 'Disable' : 'Enable'"
              >{{ server.enabled ? 'ON' : 'OFF' }}</button>
            </div>
          </div>

          <div v-if="expandedId === server.name" class="lsp-server__body">
            <p class="lsp-server__desc">{{ server.description }}</p>
            <div class="lsp-server__langs">
              <span v-for="l in server.languages" :key="l">{{ l }}</span>
            </div>
            <a v-if="server.homepage" :href="server.homepage" target="_blank" class="lsp-server__link">Homepage</a>

            <div v-if="server.installed" class="lsp-server__actions-row">
              <button type="button" @click.stop="doProbe(server)" :disabled="busyId === server.name">
                {{ probeFor(server.name) ? (probeFor(server.name).available ? 'OK' : 'Retry') : 'Probe' }}
              </button>
            </div>
            <div v-if="probeFor(server.name)" class="lsp-server__probe" :class="{ 'is-ok': probeFor(server.name).available }">
              <span v-if="probeFor(server.name).available">Ready &mdash; {{ probeFor(server.name).version }}</span>
              <span v-else>{{ probeFor(server.name).error || 'Unavailable' }}</span>
            </div>
            <div v-if="server.install_error" class="lsp-server__probe is-err">
              <span>{{ server.install_error }}</span>
            </div>
          </div>
        </article>
      </section>
    </template>

    <template v-else>
      <article v-for="server in filtered" :key="server.name" class="lsp-server" :class="{ 'is-installed': server.installed, 'is-expanded': expandedId === server.name }">
        <div class="lsp-server__main" @click="toggleExpand(server.name)">
          <span class="lsp-server__lang">{{ server.languages.slice(0, 2).join(', ') }}</span>
          <div class="lsp-server__info">
            <strong>{{ server.display_name }}</strong>
            <small>{{ server.name }}</small>
          </div>
          <div class="lsp-server__status">
            <span v-if="server.installed" class="lsp-server__dot is-ok"></span>
            <span v-else class="lsp-server__dot"></span>
            <span class="lsp-server__state">{{ server.installed ? server.install_version || 'installed' : 'not installed' }}</span>
          </div>
          <div class="lsp-server__actions">
            <button
              type="button"
              class="lsp-server__action"
              :class="{ 'is-danger': server.installed }"
              :disabled="!!busyId"
              @click.stop="onAction(server)"
            >{{ actionButton(server) }}</button>
            <button
              v-if="server.installed"
              type="button"
              class="lsp-server__toggle"
              :class="{ 'is-on': server.enabled }"
              :disabled="!!busyId"
              @click.stop="toggleEnabled(server)"
              :title="server.enabled ? 'Disable' : 'Enable'"
            >{{ server.enabled ? 'ON' : 'OFF' }}</button>
          </div>
        </div>

        <div v-if="expandedId === server.name" class="lsp-server__body">
          <p class="lsp-server__desc">{{ server.description }}</p>
          <div class="lsp-server__langs">
            <span v-for="l in server.languages" :key="l">{{ l }}</span>
          </div>
          <a v-if="server.homepage" :href="server.homepage" target="_blank" class="lsp-server__link">Homepage</a>

          <div v-if="server.installed" class="lsp-server__actions-row">
            <button type="button" @click.stop="doProbe(server)" :disabled="busyId === server.name">
              {{ probeFor(server.name) ? (probeFor(server.name).available ? 'OK' : 'Retry') : 'Probe' }}
            </button>
          </div>
          <div v-if="probeFor(server.name)" class="lsp-server__probe" :class="{ 'is-ok': probeFor(server.name).available }">
            <span v-if="probeFor(server.name).available">Ready &mdash; {{ probeFor(server.name).version }}</span>
            <span v-else>{{ probeFor(server.name).error || 'Unavailable' }}</span>
          </div>
          <div v-if="server.install_error" class="lsp-server__probe is-err">
            <span>{{ server.install_error }}</span>
          </div>
        </div>
      </article>
    </template>

    <p v-if="!filtered.length && !loading" class="lsp-page__empty">No LSP servers match your criteria.</p>
  </main>
</template>

<style scoped>
.lsp-page {
  width: min(1100px, 100%);
  margin: 0 auto;
  padding: 42px 52px 72px;
}

.lsp-page__head {
  display: flex;
  align-items: flex-end;
  gap: 20px;
  justify-content: space-between;
  margin-bottom: 28px;
}

.lsp-page h1 {
  margin: 0;
  color: var(--text-h);
  font: 570 34px/1.05 var(--heading);
}

.lsp-page p {
  margin: 7px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.55;
  max-width: 480px;
}

.lsp-page button {
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-h);
  background: #fff;
  font: 650 10px/1 var(--mono);
  cursor: pointer;
  white-space: nowrap;
}

.lsp-page button:hover { border-color: var(--accent-border); background: var(--accent-bg); }
.lsp-page button:disabled { opacity: .45; cursor: default; }

.lsp-page__controls {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 24px;
}

.lsp-page__search {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: #fff;
  color: var(--text-muted);
}

.lsp-page__search input {
  width: 100%;
  border: 0;
  outline: none;
  color: var(--text-h);
  font-size: 12px;
  background: transparent;
}

.lsp-page__search input::placeholder { color: var(--text-muted); }
.lsp-page__search:focus-within { border-color: var(--accent-border); box-shadow: 0 0 0 3px var(--accent-bg); }

.lsp-page__lang-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.lsp-page__lang-tabs button {
  height: 24px;
  padding: 0 8px;
  border-radius: 12px;
  font: 9px/1 var(--mono);
  color: var(--text-muted);
  background: transparent;
  border: 1px solid var(--border);
}

.lsp-page__lang-tabs button:hover { color: var(--text-h); border-color: var(--border-strong); }
.lsp-page__lang-tabs button.active { color: #fff; background: var(--accent); border-color: var(--accent); }

.lsp-page__error {
  padding: 9px 10px;
  border: 1px solid var(--err-border);
  border-radius: var(--radius-sm);
  color: var(--err);
  background: var(--err-bg);
  margin-bottom: 16px;
}

.lsp-group {
  margin-bottom: 24px;
}

.lsp-group__title {
  margin: 0 0 8px;
  color: var(--text-muted);
  font: 800 9px/1 var(--mono);
  text-transform: uppercase;
}

.lsp-server {
  border: 1px solid var(--border);
  border-bottom: 0;
  background: rgba(255, 255, 255, .88);
  transition: background .1s;
}

.lsp-server:last-child { border-bottom: 1px solid var(--border); }
.lsp-server:hover { background: #fff; }
.lsp-server.is-installed { border-left: 3px solid var(--ok); }

.lsp-server__main {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) 130px auto;
  gap: 10px;
  align-items: center;
  min-height: 48px;
  padding: 6px 12px;
  cursor: pointer;
  user-select: none;
}

.lsp-server__lang {
  overflow: hidden;
  color: var(--text-muted);
  font: 9px/1.3 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lsp-server__info {
  display: grid;
  min-width: 0;
  gap: 1px;
}

.lsp-server__info strong {
  color: var(--text-h);
  font-size: 12px;
}

.lsp-server__info small {
  overflow: hidden;
  color: var(--text-muted);
  font: 9px/1.3 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lsp-server__status {
  display: flex;
  align-items: center;
  gap: 6px;
}

.lsp-server__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--border-strong);
}

.lsp-server__dot.is-ok { background: var(--ok); }

.lsp-server__state {
  color: var(--text-muted);
  font: 8.5px/1.2 var(--mono);
}

.lsp-server__actions {
  display: flex;
  gap: 4px;
  justify-content: flex-end;
}

.lsp-server__action {
  height: 26px !important;
  padding: 0 10px !important;
  border: 1px solid var(--accent-border) !important;
  color: var(--accent-text) !important;
  background: var(--accent-bg) !important;
}

.lsp-server__action:hover { background: var(--accent) !important; color: #fff !important; }
.lsp-server__action.is-danger { border-color: var(--err-border) !important; color: var(--err) !important; background: var(--err-bg) !important; }
.lsp-server__action.is-danger:hover { background: var(--err) !important; color: #fff !important; }

.lsp-server__toggle {
  height: 26px !important;
  width: 36px !important;
  padding: 0 !important;
  border: 1px solid var(--border) !important;
  color: var(--text-muted) !important;
  background: #fff !important;
  font: 700 9px/1 var(--mono) !important;
}

.lsp-server__toggle.is-on { border-color: var(--ok-border) !important; color: var(--ok) !important; background: var(--ok-bg) !important; }

.lsp-server__body {
  padding: 0 12px 14px 86px;
  display: grid;
  gap: 8px;
}

.lsp-server__desc {
  margin: 0;
  color: var(--text) !important;
  font-size: 11px !important;
  max-width: none !important;
}

.lsp-server__langs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.lsp-server__langs span {
  padding: 2px 6px;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-muted);
  font: 8.5px/1 var(--mono);
  background: var(--bg);
}

.lsp-server__link {
  color: var(--accent-text);
  font: 700 9px/1 var(--mono);
  text-decoration: none;
}

.lsp-server__actions-row {
  display: flex;
  gap: 6px;
}

.lsp-server__probe {
  padding: 6px 8px;
  border-radius: 5px;
  color: var(--text-muted);
  background: var(--code-bg);
  font: 9px/1.3 var(--mono);
}

.lsp-server__probe.is-ok { color: var(--ok); background: var(--ok-bg); }
.lsp-server__probe.is-err { color: var(--err); background: var(--err-bg); }

.lsp-page__empty {
  padding: 40px 0;
  text-align: center;
  color: var(--text-muted);
}

@media (max-width: 720px) {
  .lsp-page { padding: 30px 18px 52px; }
  .lsp-server__main { grid-template-columns: minmax(0, 1fr) auto; }
  .lsp-server__lang, .lsp-server__status { display: none; }
  .lsp-server__body { padding-left: 12px; }
}
</style>
