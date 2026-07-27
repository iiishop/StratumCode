<script setup>
import { computed, reactive, ref, watch } from 'vue'
import SkillEditor from './SkillEditor.vue'
import SkillMatrix from './SkillMatrix.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  targets: { type: Array, default: () => [] },
  assignments: { type: Object, default: () => ({}) },
  modes: { type: Object, default: () => ({}) },
  preview: { type: Object, default: null },
  busy: { type: String, default: '' },
})
const emit = defineEmits(['save', 'select', 'delete'])

const activeId = ref('global')
const focusedId = ref('')
const targetQuery = ref('')
const skillQuery = ref('')
const filter = ref('all')
const view = ref('configure')
const collapsed = reactive(new Set())
const dirty = reactive(new Set())
const draftAssignments = reactive({})
const draftModes = reactive({})

const active = computed(() => props.targets.find(item => item.id === activeId.value) || props.targets[0])
const focused = computed(() => props.items.find(item => item.id === focusedId.value) || null)
const explicitIds = computed(() => draftAssignments[active.value?.id] || [])
const activeMode = computed(() => draftModes[active.value?.id] || 'merge')
const inheritedIds = computed(() => {
  if (!active.value || active.value.kind === 'global' || activeMode.value === 'replace') return []
  const explicit = new Set(explicitIds.value)
  return (draftAssignments.global || []).filter(id => !explicit.has(id))
})
const effectiveIdSet = computed(() => new Set([...explicitIds.value, ...inheritedIds.value]))
const issueCount = computed(() => props.items.filter(hasIssue).length)
const activeDirty = computed(() => Boolean(active.value && dirty.has(active.value.id)))
const saving = computed(() => props.busy === `target:${active.value?.id}`)

const groups = computed(() => {
  const labels = { global: 'Global', state: 'Stages', subagent: 'Subagents' }
  const order = ['global', 'state', 'subagent']
  const query = targetQuery.value.trim().toLocaleLowerCase()
  return order.map(kind => ({
    kind,
    label: labels[kind],
    items: props.targets.filter(target => (
      target.kind === kind
      && (!query || `${target.label} ${target.description}`.toLocaleLowerCase().includes(query))
    )),
  })).filter(group => group.items.length)
})

const filteredItems = computed(() => {
  const query = skillQuery.value.trim().toLocaleLowerCase()
  return props.items.filter(item => {
    if (filter.value === 'selected' && !effectiveIdSet.value.has(item.id)) return false
    if (filter.value === 'issues' && !hasIssue(item)) return false
    if (!query) return true
    return `${item.name} ${item.description} ${item.source_label} ${item.path}`
      .toLocaleLowerCase()
      .includes(query)
  })
})

const usedTargets = computed(() => {
  if (!focused.value) return []
  return props.targets.filter(target => effectiveIds(target.id).includes(focused.value.id))
})

const metadataRows = computed(() => {
  const metadata = focused.value?.metadata || {}
  return Object.entries(metadata)
    .filter(([key, value]) => !['name', 'description', 'package'].includes(key) && value !== '')
    .map(([key, value]) => ({ key, value: formatValue(value) }))
})

watch(() => ({
  targets: props.targets,
  assignments: props.assignments,
  modes: props.modes,
}), () => {
  for (const target of props.targets) {
    const ids = props.assignments[target.id] || []
    const mode = props.modes[target.id] || 'merge'
    if (dirty.has(target.id) && sameIds(draftAssignments[target.id] || [], ids) && draftModes[target.id] === mode) {
      dirty.delete(target.id)
    }
    if (!dirty.has(target.id)) {
      draftAssignments[target.id] = [...ids]
      draftModes[target.id] = mode
    }
  }
  if (!props.targets.some(target => target.id === activeId.value)) {
    activeId.value = props.targets[0]?.id || 'global'
  }
}, { deep: true, immediate: true })

watch(() => props.items, items => {
  if (!items.some(item => item.id === focusedId.value)) {
    focus(items[0])
  }
}, { immediate: true })

function targetCount(targetId) {
  return effectiveIds(targetId).length
}

function effectiveIds(targetId) {
  const own = draftAssignments[targetId] || []
  const target = props.targets.find(item => item.id === targetId)
  if (!target || target.kind === 'global' || draftModes[targetId] === 'replace') return own
  return [...new Set([...(draftAssignments.global || []), ...own])]
}

function isExplicit(skillId) {
  return explicitIds.value.includes(skillId)
}

function isInherited(skillId) {
  return inheritedIds.value.includes(skillId)
}

function toggleSkill(skillId) {
  if (!active.value || isInherited(skillId)) return
  const selected = new Set(explicitIds.value)
  selected.has(skillId) ? selected.delete(skillId) : selected.add(skillId)
  draftAssignments[active.value.id] = [...selected]
  dirty.add(active.value.id)
}

function setMode(mode) {
  if (!active.value || active.value.kind === 'global') return
  draftModes[active.value.id] = mode
  dirty.add(active.value.id)
}

function focus(item) {
  if (!item || focusedId.value === item.id) return
  focusedId.value = item.id
  emit('select', item)
}

function reset() {
  if (!active.value) return
  draftAssignments[active.value.id] = [...(props.assignments[active.value.id] || [])]
  draftModes[active.value.id] = props.modes[active.value.id] || 'merge'
  dirty.delete(active.value.id)
}

function apply() {
  if (!active.value || !activeDirty.value) return
  emit('save', active.value.id, [...explicitIds.value], activeMode.value)
}

function selectMatrixTarget(targetId) {
  activeId.value = targetId
  view.value = 'configure'
}

function hasIssue(item) {
  return !item.description || !item.skill_file
}

function sameIds(left, right) {
  return left.length === right.length && left.every(id => right.includes(id))
}

function formatValue(value) {
  if (Array.isArray(value)) return value.join(', ')
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function toggleGroup(kind) {
  collapsed.has(kind) ? collapsed.delete(kind) : collapsed.add(kind)
}
</script>

<template>
  <section class="sc" aria-label="Skills configuration">
    <header class="sc__head">
      <div>
        <strong>Skills Configuration</strong>
        <span>{{ targets.length }} targets · {{ items.length }} installed skills</span>
      </div>
      <div class="sc__head-actions">
        <div class="sc__view-tabs" aria-label="Configuration view">
          <button type="button" :class="{ 'is-active': view === 'configure' }" @click="view = 'configure'">Configure</button>
          <button type="button" :class="{ 'is-active': view === 'matrix' }" @click="view = 'matrix'">Matrix</button>
        </div>
        <button type="button" class="sc__save" :disabled="!activeDirty || saving" @click="apply">
          {{ saving ? 'Saving' : 'Save' }}
        </button>
      </div>
    </header>

    <div v-if="view === 'configure'" class="sc__workspace">
      <aside class="sc__targets">
        <label class="sc__search">
          <span>Configuration targets</span>
          <input v-model="targetQuery" placeholder="Search targets" />
        </label>
        <section v-for="group in groups" :key="group.kind" class="sc__target-group">
          <button type="button" class="sc__group-toggle" @click="toggleGroup(group.kind)">
            <span>{{ collapsed.has(group.kind) ? '›' : '⌄' }} {{ group.label }}</span>
            <small>{{ group.items.length }}</small>
          </button>
          <div v-if="!collapsed.has(group.kind)">
            <button
              v-for="target in group.items"
              :key="target.id"
              type="button"
              class="sc__target"
              :class="{ 'is-active': active?.id === target.id, 'is-dirty': dirty.has(target.id) }"
              @click="activeId = target.id"
            >
              <span>{{ target.label }}</span>
              <small>{{ targetCount(target.id) }}</small>
            </button>
          </div>
        </section>
      </aside>

      <section class="sc__skills">
        <label class="sc__search">
          <span>Skills</span>
          <input v-model="skillQuery" placeholder="Search installed skills" />
        </label>
        <div class="sc__filters" aria-label="Skill filters">
          <button type="button" :class="{ 'is-active': filter === 'all' }" @click="filter = 'all'">All {{ items.length }}</button>
          <button type="button" :class="{ 'is-active': filter === 'selected' }" @click="filter = 'selected'">Selected {{ effectiveIdSet.size }}</button>
          <button type="button" :class="{ 'is-active': filter === 'issues' }" @click="filter = 'issues'">Issues {{ issueCount }}</button>
        </div>
        <div class="sc__skill-list">
          <div
            v-for="item in filteredItems"
            :key="item.id"
            class="sc__skill"
            :class="{ 'is-focused': focused?.id === item.id, 'has-issue': hasIssue(item) }"
          >
            <input
              type="checkbox"
              :checked="isExplicit(item.id) || isInherited(item.id)"
              :disabled="isInherited(item.id)"
              :aria-label="`Use ${item.name} for ${active?.label}`"
              @change="toggleSkill(item.id)"
            />
            <button type="button" class="sc__skill-info" @click="focus(item)">
              <strong>{{ item.name }}</strong>
              <span>{{ item.description || 'Missing description' }}</span>
              <small>{{ item.source_label }}<template v-if="isInherited(item.id)"> · inherited</template><template v-else-if="isExplicit(item.id)"> · explicit</template></small>
            </button>
          </div>
          <p v-if="!filteredItems.length">No matching installed skills.</p>
        </div>
        <footer class="sc__selection-count">Selected {{ effectiveIdSet.size }} skills</footer>
      </section>

      <aside class="sc__detail">
        <template v-if="focused">
          <div class="sc__detail-head">
            <div>
              <small>{{ focused.source_label }}</small>
              <h2>{{ focused.name }}</h2>
            </div>
            <button
              v-if="focused.source_label === 'stratumcode'"
              type="button"
              class="sc__delete"
              title="Delete skill"
              @click="emit('delete', focused)"
            >Delete</button>
          </div>
          <p class="sc__description">{{ focused.description || 'No description provided.' }}</p>
          <dl v-if="metadataRows.length" class="sc__meta">
            <template v-for="row in metadataRows" :key="row.key">
              <dt>{{ row.key }}</dt>
              <dd>{{ row.value }}</dd>
            </template>
          </dl>
          <div class="sc__usage">
            <strong>Used by</strong>
            <div>
              <button v-for="target in usedTargets" :key="target.id" type="button" @click="activeId = target.id">
                {{ target.label }}
              </button>
              <span v-if="!usedTargets.length">No targets</span>
            </div>
          </div>
          <div class="sc__instructions">
            <strong>Instructions</strong>
            <SkillEditor v-if="preview?.content" :model-value="preview.content" readonly />
            <span v-else>Loading skill details…</span>
          </div>
        </template>
        <p v-else class="sc__empty-detail">Select a skill to inspect it.</p>
      </aside>
    </div>

    <SkillMatrix
      v-else
      :items="items"
      :targets="targets"
      :assignments="draftAssignments"
      :modes="draftModes"
      @select="selectMatrixTarget"
    />

    <footer v-if="active" class="sc__footer">
      <div>
        <strong>Current target: {{ active.label }}</strong>
        <span>{{ inheritedIds.length }} inherited + {{ explicitIds.length }} explicit</span>
      </div>
      <div v-if="active.kind !== 'global'" class="sc__mode" aria-label="Global skill inheritance">
        <button type="button" :class="{ 'is-active': activeMode === 'merge' }" @click="setMode('merge')">Global + own</button>
        <button type="button" :class="{ 'is-active': activeMode === 'replace' }" @click="setMode('replace')">Own only</button>
      </div>
      <div class="sc__footer-actions">
        <button type="button" :disabled="!activeDirty || saving" @click="reset">Reset</button>
        <button type="button" class="is-primary" :disabled="!activeDirty || saving" @click="apply">Apply</button>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.sc { container-type: inline-size; border: 1px solid var(--border); background: var(--bg-raised); }
.sc button, .sc input { font: inherit; }
.sc__head, .sc__footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 56px; padding: 10px 14px; }
.sc__head { border-bottom: 1px solid var(--border); }
.sc__head > div:first-child, .sc__footer > div:first-child { display: grid; gap: 4px; min-width: 0; }
.sc__head strong, .sc__footer strong { color: var(--text-h); font: 600 12px var(--sans); }
.sc__head span, .sc__footer span { color: var(--text-muted); font: 9px var(--mono); }
.sc__head-actions, .sc__footer-actions, .sc__view-tabs, .sc__mode { display: flex; align-items: center; gap: 4px; }
.sc__view-tabs, .sc__mode { padding: 2px; border: 1px solid var(--border); border-radius: 6px; }
.sc__view-tabs button, .sc__mode button, .sc__footer-actions button, .sc__save { height: 28px; padding: 0 9px; border: 0; border-radius: 4px; color: var(--text-muted); background: transparent; font: 9px var(--mono); cursor: pointer; }
.sc__view-tabs button.is-active, .sc__mode button.is-active { color: var(--text-h); background: var(--accent-bg); }
.sc__save, .sc__footer-actions .is-primary { color: #fff; background: var(--accent); }
.sc button:disabled { opacity: .35; cursor: default; }
.sc__workspace { display: grid; grid-template-columns: minmax(160px, .72fr) minmax(260px, 1.25fr) minmax(260px, 1fr); height: 450px; }
.sc__targets, .sc__skills, .sc__detail { min-width: 0; overflow: auto; }
.sc__targets, .sc__skills { border-right: 1px solid var(--border); }
.sc__search { display: grid; gap: 7px; padding: 13px 12px; border-bottom: 1px solid var(--border); color: var(--text-muted); font: 9px var(--mono); }
.sc__search input { width: 100%; height: 31px; padding: 0 9px; border: 1px solid var(--border); border-radius: 5px; outline: 0; color: var(--text-h); background: var(--bg); font: 10px var(--mono); }
.sc__search input:focus { border-color: var(--accent-border); }
.sc__target-group { padding: 7px; border-bottom: 1px solid var(--border); }
.sc__group-toggle, .sc__target { display: grid; grid-template-columns: minmax(0, 1fr) auto; width: 100%; border: 0; text-align: left; cursor: pointer; }
.sc__group-toggle { padding: 6px; color: var(--text-muted); background: transparent; font: 700 8px var(--mono); text-transform: uppercase; }
.sc__target { padding: 8px; border-radius: 4px; color: var(--text); background: transparent; font: 10px var(--sans); }
.sc__target:hover, .sc__target.is-active { color: var(--text-h); background: var(--accent-bg); }
.sc__target.is-dirty span::after { content: ' •'; color: var(--accent); }
.sc__target small, .sc__group-toggle small { color: var(--text-muted); font: 9px var(--mono); }
.sc__skills { display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto; }
.sc__filters { display: flex; gap: 5px; padding: 8px 11px; border-bottom: 1px solid var(--border); }
.sc__filters button { height: 25px; padding: 0 8px; border: 1px solid transparent; border-radius: 4px; color: var(--text-muted); background: transparent; font: 9px var(--mono); cursor: pointer; }
.sc__filters button.is-active { border-color: var(--border); color: var(--text-h); background: var(--bg); }
.sc__skill-list { overflow: auto; }
.sc__skill { display: grid; grid-template-columns: 18px minmax(0, 1fr); align-items: center; gap: 8px; min-height: 58px; padding: 7px 11px; border-bottom: 1px solid var(--border); }
.sc__skill:hover, .sc__skill.is-focused { background: var(--accent-bg); }
.sc__skill.has-issue { box-shadow: inset 2px 0 var(--warn); }
.sc__skill input { accent-color: var(--accent); }
.sc__skill-info { display: grid; gap: 3px; min-width: 0; padding: 0; border: 0; color: inherit; background: transparent; text-align: left; cursor: pointer; }
.sc__skill-info strong { overflow: hidden; color: var(--text-h); font: 500 11px var(--sans); text-overflow: ellipsis; white-space: nowrap; }
.sc__skill-info span { overflow: hidden; color: var(--text-muted); font: 9px var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.sc__skill-info small { color: var(--accent); font: 8px var(--mono); }
.sc__skill-list > p, .sc__empty-detail { margin: 24px 12px; color: var(--text-muted); font: 10px var(--mono); }
.sc__selection-count { padding: 9px 12px; border-top: 1px solid var(--border); color: var(--text-muted); font: 9px var(--mono); }
.sc__detail { padding: 15px; }
.sc__detail-head { display: flex; align-items: start; justify-content: space-between; gap: 12px; }
.sc__detail-head h2 { margin: 3px 0 0; overflow-wrap: anywhere; color: var(--text-h); font: 600 15px var(--sans); }
.sc__detail-head small { color: var(--accent); font: 8px var(--mono); text-transform: uppercase; }
.sc__delete { border: 0; color: var(--err); background: transparent; font: 9px var(--mono); cursor: pointer; }
.sc__description { margin: 12px 0 16px; color: var(--text); font: 10px/1.5 var(--mono); }
.sc__meta { display: grid; grid-template-columns: minmax(70px, .65fr) minmax(0, 1fr); gap: 6px 10px; margin: 0 0 18px; font: 9px/1.45 var(--mono); }
.sc__meta dt { color: var(--text-muted); }
.sc__meta dd { margin: 0; overflow-wrap: anywhere; color: var(--text-h); }
.sc__usage > strong, .sc__instructions > strong { display: block; margin-bottom: 8px; color: var(--text-muted); font: 700 8px var(--mono); text-transform: uppercase; }
.sc__usage > div { display: flex; flex-wrap: wrap; gap: 5px; }
.sc__usage button { padding: 3px 6px; border: 1px solid var(--border); border-radius: 4px; color: var(--text); background: var(--bg); font: 8px var(--mono); cursor: pointer; }
.sc__usage span, .sc__instructions > span { color: var(--text-muted); font: 9px var(--mono); }
.sc__instructions { margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--border); }
.sc__instructions :deep(.se) { font-size: 10px; }
.sc__footer { border-top: 1px solid var(--border); }
@container (max-width: 850px) {
  .sc__workspace { grid-template-columns: minmax(150px, .7fr) minmax(260px, 1.3fr); height: auto; }
  .sc__detail { grid-column: 1 / -1; min-height: 260px; border-top: 1px solid var(--border); }
}
@container (max-width: 560px) {
  .sc__head, .sc__footer { align-items: stretch; flex-direction: column; }
  .sc__head-actions, .sc__footer-actions { flex-wrap: wrap; justify-content: space-between; }
  .sc__workspace { grid-template-columns: 1fr; }
  .sc__targets, .sc__skills { border-right: 0; border-bottom: 1px solid var(--border); }
  .sc__targets { max-height: 240px; }
  .sc__skills { min-height: 420px; }
}
</style>
