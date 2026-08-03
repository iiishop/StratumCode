<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useSkills } from '../../composables/useSkills'
import SkillAssignments from './SkillAssignments.vue'
import SkillEditor from './SkillEditor.vue'

const store = useSkills()
const query = ref('')
const source = ref('')
const showCreate = ref(false)
const form = reactive({ name: '', description: '', content: '' })

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

function sourceFor(item) {
  return item.package || item.url || item.path || ''
}
async function deleteSkill(item) {
  if (!window.confirm(`Delete skill "${item.name}"?`)) return
  await store.remove(item)
}
</script>

<template>
  <main class="sp">
    <section class="sp__feed">
      <div class="sp__head">
        <span class="sp__title">Skills</span>
        <div class="sp__head-actions">
          <button type="button" class="sp__btn sp__btn--pri btn-sheen" @click="showCreate = true">New skill</button>
        </div>
      </div>

      <SkillAssignments
        :items="store.local.value"
        :targets="store.targets.value"
        :assignments="store.assignments.value"
        :modes="store.modes.value"
        :preview="store.preview.value"
        :busy="store.busy.value"
        :on-place="store.placeSkill"
        @save="store.configure"
        @select="store.show"
        @delete="deleteSkill"
      />

      <p v-if="store.error.value" class="sp__error">{{ store.error.value }}</p>

      <section class="sp__discover">
        <div class="sp__section-head">
          <strong>Discover or import skills</strong>
          <span>Installed skills appear in the configuration list above.</span>
        </div>
        <div v-if="!store.runtime.value.available" class="sp__runtime">
          <span>npx unavailable — remote search disabled</span>
          <button type="button" class="sp__btn sp__btn--pri btn-sheen" :disabled="store.busy.value === 'runtime'" @click="store.installRuntime">
            {{ store.busy.value === 'runtime' ? 'Installing' : 'Install Node.js' }}
          </button>
        </div>
        <div class="sp__tools">
          <input v-model="query" placeholder="Find remote skills" @keydown.enter="doSearch" />
          <button type="button" class="sp__btn sp__btn--pri btn-sheen" @click="doSearch">Search</button>
          <input v-model="source" placeholder="Add URL, package, or path" @keydown.enter="addSource()" />
        </div>
      </section>

      <div v-if="store.results.value.length" class="sp__list">
        <button
          v-for="item in store.results.value"
          :key="item.id || item.package || item.url"
          type="button"
          class="sp__item"
          @click="store.show(item)"
        >
          <span class="sp__item-name">{{ item.name }}</span>
          <span class="sp__item-desc">{{ item.description || item.package || item.path }}</span>
          <span class="sp__item-src">remote</span>
          <button
            v-if="!item.installed"
            type="button"
            class="sp__btn sp__btn--pri btn-sheen sp__item-add"
            :disabled="store.busy.value === sourceFor(item)"
            @click.stop="addSource(sourceFor(item))"
          >
            {{ store.busy.value === sourceFor(item) ? 'Adding' : 'Add' }}
          </button>
        </button>
      </div>
    </section>

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
          <button type="submit" class="sp__btn sp__btn--pri btn-sheen" :disabled="store.busy.value === 'create'">
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
  width: min(1440px, 100%);
  margin: 0 auto;
  padding: 40px 44px 72px;
}
.sp__feed { min-width: 0; }

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
  margin: 12px 0 0; padding: 9px 12px;
  border: 1px solid var(--warn-border, #e7bf37); border-radius: 7px;
  background: var(--warn-bg); font: 11px var(--mono); color: var(--warn);
}

/* ---- DISCOVERY ---- */
.sp__discover { margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--border); }
.sp__section-head { display: grid; gap: 4px; margin-bottom: 12px; }
.sp__section-head strong { color: var(--text-h); font: 600 11px var(--sans); }
.sp__section-head span { color: var(--text-muted); font: 9px var(--mono); }
.sp__tools { display: flex; gap: 8px; margin-top: 12px; }
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
  margin: 12px 0 0; padding: 7px 10px;
  border: 1px solid var(--err-border); border-radius: 6px;
  color: var(--err); background: var(--err-bg); font-size: 11px;
}

/* ---- LIST ---- */
.sp__list { display: flex; flex-direction: column; margin-top: 10px; }
.sp__item {
  display: flex; align-items: center; gap: 12px; min-height: 48px; padding: 10px 12px;
  border: 0; border-bottom: 1px solid var(--border);
  background: transparent; text-align: left; cursor: pointer;
  transition: background .1s; font: inherit; color: inherit;
}
.sp__item:hover { background: var(--accent-bg); }
.sp__item-name { font: 500 13px/1.2 var(--sans); color: var(--text-h); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.sp__item-desc { flex: 1; min-width: 0; font: 11px/1.4 var(--mono); color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sp__item-src { font: 9px var(--mono); color: var(--text-muted); border: 1px solid var(--border); border-radius: 4px; padding: 2px 6px; white-space: nowrap; flex-shrink: 0; }
.sp__item-add { flex-shrink: 0; height: 26px; font-size: 9px; padding: 0 10px; }

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
  .sp { padding: 28px 18px 52px; }
  .sp__tools { flex-direction: column; }
  .sp__item { flex-wrap: wrap; }
}
</style>
