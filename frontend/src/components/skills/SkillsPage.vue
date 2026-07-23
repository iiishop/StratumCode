<script setup>
import { onMounted, reactive, ref } from 'vue'
import SkillList from './SkillList.vue'
import { useSkills } from '../../composables/useSkills'

const store = useSkills()
const query = ref('')
const source = ref('')
const showCreate = ref(false)
const form = reactive({ name: '', description: '', content: '' })

onMounted(store.load)

function doSearch() {
  if (query.value.trim()) store.search(query.value.trim())
}

async function addSource(value = source.value) {
  const text = value.trim()
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
</script>

<template>
  <main class="skills-page">
    <section class="skills-page__head">
      <div>
        <h1>Skills</h1>
        <p>Search, add, and preview skills. Runtime loading is intentionally separate.</p>
      </div>
      <button type="button" @click="showCreate = true">Create skill</button>
    </section>

    <section v-if="!store.runtime.value.available" class="skills-page__runtime">
      <div>
        <strong>Node.js runtime missing</strong>
        <span>Remote skill search and package install need npx. Local SKILL.md import still works.</span>
      </div>
      <button type="button" :disabled="store.busy.value === 'runtime'" @click="store.installRuntime">
        {{ store.busy.value === 'runtime' ? 'Installing' : 'Install Node.js' }}
      </button>
    </section>

    <section class="skills-page__tools">
      <form class="skills-page__search" @submit.prevent="doSearch">
        <input v-model="query" placeholder="Search skills..." />
        <button type="submit" :disabled="store.searching.value || !store.runtime.value.available || !query.trim()">
          {{ store.searching.value ? 'Searching' : 'Search' }}
        </button>
      </form>
      <form class="skills-page__add" @submit.prevent="addSource()">
        <input v-model="source" placeholder="owner/repo@skill, URL, or local SKILL.md path" />
        <button type="submit" :disabled="!!store.busy.value || !source.trim()">
          {{ store.busy.value ? 'Adding' : 'Add' }}
        </button>
      </form>
    </section>

    <p v-if="store.error.value" class="skills-page__error">{{ store.error.value }}</p>

    <section class="skills-page__grid">
      <div class="skills-page__lists">
        <SkillList
          title="Local skills"
          :items="store.local.value"
          :loading="store.loading.value"
          empty="No local skills found."
          @preview="store.show"
        />
        <SkillList
          title="Search results"
          :items="store.results.value"
          :loading="store.searching.value"
          :busy="store.busy.value"
          empty="Search to find skills."
          can-add
          @preview="store.show"
          @add="addSource"
        />
      </div>

      <aside class="skills-page__preview">
        <header>
          <strong>Preview</strong>
          <span v-if="store.preview.value?.truncated">truncated</span>
        </header>
        <pre v-if="store.preview.value?.content">{{ store.preview.value.content }}</pre>
        <p v-else>Select a skill to preview its SKILL.md.</p>
      </aside>
    </section>

    <div v-if="showCreate" class="skills-modal">
      <form class="skills-modal__panel" @submit.prevent="createSkill">
        <header>
          <div>
            <h2>Create skill</h2>
            <p>This creates a local SKILL.md for later wiring.</p>
          </div>
          <button type="button" @click="showCreate = false">Close</button>
        </header>
        <label>
          <span>Name</span>
          <input v-model="form.name" placeholder="my-skill" required />
        </label>
        <label>
          <span>Description</span>
          <input v-model="form.description" placeholder="What this skill helps with" />
        </label>
        <label>
          <span>SKILL.md</span>
          <textarea v-model="form.content" spellcheck="false" placeholder="Leave empty to generate a minimal file."></textarea>
        </label>
        <footer>
          <button type="button" @click="showCreate = false">Cancel</button>
          <button type="submit" :disabled="store.busy.value === 'create'">
            {{ store.busy.value === 'create' ? 'Creating' : 'Create' }}
          </button>
        </footer>
      </form>
    </div>
  </main>
</template>

<style scoped>
.skills-page {
  width: min(1180px, 100%);
  margin: 0 auto;
  padding: 42px 52px 72px;
}

.skills-page__head,
.skills-page__runtime,
.skills-page__tools,
.skills-page__search,
.skills-page__add,
.skills-page__preview header,
.skills-modal__panel header,
.skills-modal__panel footer {
  display: flex;
  align-items: center;
}

.skills-page__head {
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 24px;
}

.skills-page__runtime {
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
  padding: 11px 12px;
  border: 1px solid var(--warn-border, #e7bf37);
  border-radius: var(--radius);
  background: var(--warn-bg);
}

.skills-page__runtime div {
  display: grid;
  gap: 3px;
}

.skills-page__runtime strong {
  color: var(--text-h);
  font-size: 12px;
}

.skills-page__runtime span {
  color: var(--warn);
  font: 10px/1.4 var(--mono);
}

.skills-page h1 {
  margin: 0;
  color: var(--text-h);
  font: 570 34px/1.05 var(--heading);
}

.skills-page p {
  margin: 7px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.55;
}

.skills-page button,
.skills-modal__panel button {
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

.skills-page__head > button,
.skills-page__runtime button,
.skills-page__tools button,
.skills-modal__panel footer button:last-child {
  border-color: var(--accent);
  color: #fff;
  background: var(--accent);
}

.skills-page button:disabled,
.skills-modal__panel button:disabled {
  opacity: .45;
  cursor: default;
}

.skills-page__tools {
  gap: 12px;
  margin-bottom: 18px;
}

.skills-page__search,
.skills-page__add {
  min-width: 0;
  flex: 1;
  gap: 8px;
}

.skills-page input,
.skills-modal__panel input,
.skills-modal__panel textarea {
  width: 100%;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-h);
  background: #fff;
  font: 12px/1.4 var(--mono);
  outline: none;
}

.skills-page input,
.skills-modal__panel input {
  height: 34px;
  padding: 0 10px;
}

.skills-page input:focus,
.skills-modal__panel input:focus,
.skills-modal__panel textarea:focus {
  border-color: var(--accent-border);
  box-shadow: 0 0 0 3px var(--accent-bg);
}

.skills-page__error {
  padding: 9px 10px;
  border: 1px solid var(--err-border);
  border-radius: var(--radius-sm);
  color: var(--err);
  background: var(--err-bg);
}

.skills-page__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, .75fr);
  gap: 22px;
}

.skills-page__lists {
  display: grid;
  gap: 26px;
  min-width: 0;
}

.skills-page__preview {
  min-width: 0;
  border-top: 1px solid var(--border-strong);
  padding-top: 14px;
}

.skills-page__preview header {
  justify-content: space-between;
  margin-bottom: 10px;
}

.skills-page__preview strong {
  color: var(--text-h);
  font-size: 13px;
}

.skills-page__preview span {
  color: var(--warn);
  font: 9px/1 var(--mono);
}

.skills-page__preview pre {
  max-height: min(68vh, 780px);
  margin: 0;
  overflow: auto;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-h);
  background: #fff;
  font: 11px/1.55 var(--mono);
  white-space: pre-wrap;
}

.skills-modal {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(15, 23, 42, .24);
  z-index: 20;
}

.skills-modal__panel {
  display: grid;
  gap: 14px;
  width: min(720px, 100%);
  padding: 18px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  background: #fff;
  box-shadow: var(--shadow-md);
}

.skills-modal__panel header,
.skills-modal__panel footer {
  justify-content: space-between;
  gap: 12px;
}

.skills-modal__panel h2 {
  margin: 0;
  color: var(--text-h);
  font: 560 18px/1.2 var(--heading);
}

.skills-modal__panel label {
  display: grid;
  gap: 6px;
}

.skills-modal__panel label span {
  color: var(--text-muted);
  font-size: 10px;
  font-weight: 550;
}

.skills-modal__panel textarea {
  min-height: 220px;
  padding: 10px;
  resize: vertical;
}

@media (max-width: 900px) {
  .skills-page {
    padding: 30px 18px 52px;
  }

  .skills-page__tools,
  .skills-page__runtime,
  .skills-page__head {
    align-items: stretch;
    flex-direction: column;
  }

  .skills-page__grid {
    grid-template-columns: 1fr;
  }
}
</style>
