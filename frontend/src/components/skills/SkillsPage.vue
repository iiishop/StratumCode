<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { gsap } from 'gsap'
import { useSkills } from '../../composables/useSkills'
import SkillEditor from './SkillEditor.vue'

const store = useSkills()
const query = ref('')
const source = ref('')
const showCreate = ref(false)
const selectedKey = ref('')
const form = reactive({ name: '', description: '', content: '' })

const feedItems = computed(() => [
  ...store.results.value.map(item => ({ ...item, feed_kind: 'remote' })),
  ...store.local.value.map(item => ({ ...item, feed_kind: 'local' })),
])

const hasPreview = computed(() => Boolean(selectedKey.value))

const previewContent = computed({
  get: () => store.preview.value?.content || '',
  set: value => {
    if (store.preview.value) store.preview.value = { ...store.preview.value, content: value }
  },
})

onMounted(store.load)

function doSearch() {
  const text = query.value.trim()
  if (text) store.search(text)
}

async function addSource(value = source.value) {
  const text = String(value || '').trim()
  if (!text) return
  await store.add(text)
  if (text === source.value.trim()) source.value = ''
}

async function createSkill() {
  await store.create({ ...form })
  form.name = ''
  form.description = ''
  form.content = ''
  showCreate.value = false
}

function itemKey(item) {
  return `${item.feed_kind}-${item.id || item.path || item.package || item.name || ''}`
}

function sourceFor(item) {
  return item.package || item.url || item.path || ''
}
function sourceLabel(item) {
  return String(item.source_label || item.source || (item.installed ? 'local' : 'remote'))
}
function canDelete(item) {
  return item.feed_kind === 'local' && sourceLabel(item) === 'stratumcode'
}
async function deleteSkill(item) {
  if (!window.confirm(`Delete skill "${item.name}"?`)) return
  const key = itemKey(item)
  await store.remove(item)
  if (selectedKey.value === key) selectedKey.value = ''
}

let _switching = false

async function selectSkill(item) {
  const sameKey = selectedKey.value === itemKey(item)
  if (sameKey) return

  // if already previewing another skill: close first
  if (hasPreview.value && !_switching) {
    _switching = true
    await animatePreviewOut()
  }

  // if same skill clicked again, just close (handled by toggle — but we don't toggle here)
  selectedKey.value = itemKey(item)
  await store.show(item)
  await nextTick()
  await animatePreviewIn()
  _switching = false
}

/* ---- GSAP ---- */
async function animatePreviewOut() {
  const preview = document.querySelector('.sp__preview')
  const feed = document.querySelector('.sp__feed')
  if (!preview) return new Promise(r => r())
  return new Promise(resolve => {
    gsap.to(preview, { opacity: 0, x: 24, duration: .16, ease: 'power2.in', onComplete: resolve })
    if (feed) gsap.to(feed, { x: 0, duration: .16, ease: 'power2.in' })
  })
}

async function animatePreviewIn() {
  await nextTick()
  const preview = document.querySelector('.sp__preview')
  const feed = document.querySelector('.sp__feed')
  if (!preview) return
  gsap.fromTo(preview,
    { opacity: 0, x: 24 },
    { opacity: 1, x: 0, duration: .24, ease: 'power2.out' }
  )
  if (feed) {
    gsap.to(feed, { x: -8, duration: .24, ease: 'power2.out' })
  }
}
</script>

<template>
  <main class="sp" :class="{ 'sp--preview': hasPreview }">
    <section class="sp__feed">
      <div class="sp__head">
        <span class="sp__title">Skills</span>
        <div class="sp__head-actions">
          <button type="button" class="sp__btn sp__btn--pri" @click="showCreate = true">New skill</button>
        </div>
      </div>

      <div v-if="!store.runtime.value.available" class="sp__runtime">
        <span>npx unavailable — remote search disabled</span>
        <button type="button" class="sp__btn sp__btn--pri" :disabled="store.busy.value === 'runtime'" @click="store.installRuntime">
          {{ store.busy.value === 'runtime' ? 'Installing' : 'Install Node.js' }}
        </button>
      </div>

      <div class="sp__tools">
        <input v-model="query" placeholder="Search skills..." @keydown.enter="doSearch" />
        <button
          type="button"
          class="sp__btn sp__btn--pri"
          @click="doSearch"
        >Search</button>
        <input v-model="source" placeholder="Add URL, package, or path..." @keydown.enter="addSource()" />
      </div>

      <p v-if="store.error.value" class="sp__error">{{ store.error.value }}</p>

      <div class="sp__list">
        <button
          v-for="item in feedItems"
          :key="itemKey(item)"
          type="button"
          class="sp__item"
          :class="{ 'is-selected': selectedKey === itemKey(item) }"
          @click="selectSkill(item)"
        >
          <span class="sp__item-name">{{ item.name }}</span>
          <span class="sp__item-desc">{{ item.description || item.package || item.path }}</span>
          <span class="sp__item-src">{{ sourceLabel(item) }}</span>
          <span class="sp__item-kind">{{ item.feed_kind === 'remote' ? 'remote' : '' }}</span>
          <button
            v-if="item.feed_kind === 'remote' && !item.installed"
            type="button"
            class="sp__btn sp__btn--pri sp__item-add"
            :disabled="store.busy.value === sourceFor(item)"
            @click.stop="addSource(sourceFor(item))"
          >
            {{ store.busy.value === sourceFor(item) ? 'Adding' : 'Add' }}
          </button>
          <button
            v-if="canDelete(item)"
            type="button"
            class="sp__btn sp__btn--danger sp__item-add"
            :disabled="store.busy.value === item.path"
            @click.stop="deleteSkill(item)"
          >
            {{ store.busy.value === item.path ? 'Deleting' : 'Delete' }}
          </button>
        </button>
        <p v-if="!feedItems.length && !store.loading.value && !store.searching.value" class="sp__empty">
          No skills. Search the registry or create one.
        </p>
      </div>
    </section>

    <!-- preview: prose mirror editor -->
    <aside v-if="hasPreview" class="sp__preview">
      <div class="sp__preview-head">
        <span>Preview</span>
        <span v-if="store.preview.value?.truncated">truncated</span>
      </div>
      <SkillEditor :key="selectedKey" v-model="previewContent" />
    </aside>

    <!-- create modal -->
    <div v-if="showCreate" class="sp__modal" @click.self="showCreate = false">
      <form class="sp__modal-panel" @submit.prevent="createSkill">
        <div class="sp__modal-head">
          <span>Create skill</span>
          <button type="button" class="sp__btn" @click="showCreate = false">Cancel</button>
        </div>
        <label class="sp__field">
          <span>Name</span>
          <input v-model="form.name" placeholder="my-skill" required />
        </label>
        <label class="sp__field">
          <span>Description</span>
          <input v-model="form.description" placeholder="What this skill helps with" />
        </label>
        <label class="sp__field">
          <span>Content</span>
          <SkillEditor v-model="form.content" class="sp__create-editor" />
        </label>
        <div class="sp__modal-actions">
          <button type="button" class="sp__btn" @click="showCreate = false">Cancel</button>
          <button type="submit" class="sp__btn sp__btn--pri" :disabled="store.busy.value === 'create'">
            {{ store.busy.value === 'create' ? 'Creating' : 'Create' }}
          </button>
        </div>
      </form>
    </div>
  </main>
</template>

<style scoped>
/* ---- PAGE ---- */
.sp {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 0;
  gap: 0;
  width: min(1440px, 100%);
  margin: 0 auto;
  padding: 40px 44px 72px;
  transition: grid-template-columns 280ms ease;
}

.sp--preview {
  grid-template-columns: minmax(220px, 1fr) minmax(480px, 2fr);
}

/* ---- HEAD ---- */
.sp__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 24px;
}
.sp__title {
  font: 500 15px var(--mono);
  color: var(--text-muted);
  letter-spacing: .04em;
  text-transform: uppercase;
}
.sp__head-actions { display: flex; gap: 8px; }

/* ---- BUTTONS ---- */
.sp__btn {
  height: 30px; padding: 0 11px;
  border: 1px solid var(--border); border-radius: 6px;
  color: var(--text-h); background: var(--bg-raised);
  font: 600 10px/1 var(--mono); cursor: pointer; white-space: nowrap;
  transition: border-color .12s, background .12s;
}
.sp__btn:hover { border-color: var(--accent-border); }
.sp__btn--pri { border-color: var(--accent); color: #fff; background: var(--accent); }
.sp__btn--pri:hover { background: var(--accent-hover); }
.sp__btn--danger { border-color: var(--err-border); color: var(--err); background: var(--err-bg); }
.sp__btn--danger:hover { border-color: var(--err); }
.sp__btn:disabled { opacity: .4; cursor: default; }

/* ---- RUNTIME ---- */
.sp__runtime {
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  margin-bottom: 14px; padding: 9px 12px;
  border: 1px solid var(--warn-border, #e7bf37); border-radius: 7px;
  background: var(--warn-bg); font: 11px var(--mono); color: var(--warn);
}

/* ---- TOOLS ---- */
.sp__tools { display: flex; gap: 8px; margin-bottom: 16px; }
.sp__tools input {
  flex: 1; min-width: 0; height: 34px;
  border: 1px solid var(--border); border-radius: 6px; padding: 0 10px;
  color: var(--text-h); background: var(--bg-raised);
  font: 12px/1.4 var(--mono); outline: none; transition: border-color .12s;
}
.sp__tools input:focus { border-color: var(--accent-border); }
.sp__tools input::placeholder { color: var(--text-muted); }

/* ---- ERROR ---- */
.sp__error {
  margin: 0 0 10px; padding: 7px 10px;
  border: 1px solid var(--err-border); border-radius: 6px;
  color: var(--err); background: var(--err-bg); font-size: 11px;
}

/* ---- LIST ---- */
.sp__list { display: flex; flex-direction: column; }
.sp__item {
  display: flex; align-items: center; gap: 12px; min-height: 48px; padding: 10px 12px;
  border: 0; border-bottom: 1px solid var(--border);
  background: transparent; text-align: left; cursor: pointer;
  transition: background .1s; font: inherit; color: inherit;
}
.sp__item:hover { background: var(--accent-bg); }
.sp__item.is-selected { background: var(--accent-bg); border-bottom-color: var(--accent-border); }
.sp__item-name { font: 500 13px/1.2 var(--sans); color: var(--text-h); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.sp__item-desc { flex: 1; min-width: 0; font: 11px/1.4 var(--mono); color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sp__item-src { font: 9px var(--mono); color: var(--text-muted); border: 1px solid var(--border); border-radius: 4px; padding: 2px 6px; white-space: nowrap; flex-shrink: 0; }
.sp__item-kind { font: 9px var(--mono); color: var(--warn); flex-shrink: 0; }
.sp__item-add { flex-shrink: 0; height: 26px; font-size: 9px; padding: 0 10px; }
.sp__empty { padding: 32px 12px; color: var(--text-muted); font: 12px var(--mono); }

/* ---- PREVIEW ---- */
.sp__preview {
  position: sticky; top: 24px; align-self: start;
  padding: 0 0 0 12px; min-width: 0;
}
.sp__preview-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 10px; font: 10px var(--mono); color: var(--text-muted);
}
.sp__preview-head span:first-child { font-weight: 600; color: var(--text-h); }

/* create modal editor */
.sp__create-editor {
  max-height: 320px;
}

/* ---- MODAL ---- */
.sp__modal { position: fixed; inset: 0; display: grid; place-items: center; padding: 20px; background: rgba(15,23,42,.28); z-index: 30; }
.sp__modal-panel { display: flex; flex-direction: column; gap: 14px; width: min(600px,100%); padding: 22px; border: 1px solid var(--border-strong); border-radius: 10px; background: var(--bg-raised); box-shadow: var(--shadow-md); }
.sp__modal-head { display: flex; align-items: center; justify-content: space-between; font: 500 16px/1.2 var(--sans); color: var(--text-h); }
.sp__modal-actions { display: flex; gap: 8px; justify-content: flex-end; }
.sp__field { display: flex; flex-direction: column; gap: 5px; }
.sp__field span { font: 10px var(--mono); color: var(--text-muted); }
.sp__field input, .sp__field textarea { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; color: var(--text-h); background: var(--bg); font: 12px/1.4 var(--mono); outline: none; }
.sp__field input:focus, .sp__field textarea:focus { border-color: var(--accent-border); }
.sp__field textarea { min-height: 180px; resize: vertical; }

@media (max-width: 860px) {
  .sp { grid-template-columns: 1fr; padding: 28px 18px 52px; }
  .sp--preview { grid-template-columns: 1fr; gap: 20px; }
  .sp__preview { position: static; padding-left: 0; }
  .sp__tools { flex-direction: column; }
  .sp__item { flex-wrap: wrap; }
}
</style>
