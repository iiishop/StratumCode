<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { gsap } from 'gsap'
import { animate, stagger } from 'animejs'

const props = defineProps({
  tab: { type: String, default: 'evidence' },
  run: { type: Object, required: true },
  todos: { type: Array, required: true },
  tools: { type: Array, required: true },
  workspaces: { type: Array, required: true },
  activeWorkspace: { type: Object, default: null },
  workspaceError: { type: String, default: '' },
})
const emit = defineEmits(['update:tab', 'toggle-todo', 'add-workspace', 'activate-workspace', 'delete-workspace', 'close'])

const root = ref(null)
const confidenceBar = ref(null)
const workspaceName = ref('')
const workspacePath = ref('')
const percent = computed(() => Math.round((props.run.confidence ?? .5) * 100))
const supportCount = computed(() => props.run.evidence.filter(item => item.stance === 'support').length)
const opposeCount = computed(() => props.run.evidence.filter(item => item.stance === 'oppose').length)
const phases = [
  ['support', 'Support'],
  ['oppose', 'Oppose'],
  ['audit', 'Audit'],
  ['evaluate', 'Evaluate'],
]

onMounted(() => {
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    gsap.fromTo(root.value, { x: 24, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: .42, ease: 'power3.out' })
  }
})

watch(percent, (value, previous = 50) => {
  if (!confidenceBar.value) return
  animate(confidenceBar.value, {
    scaleX: [previous / 100, value / 100],
    duration: 680,
    ease: 'outElastic(1, .65)',
  })
})

watch(() => props.run.evidence.length, async () => {
  await nextTick()
  const cards = root.value?.querySelectorAll('.evidence-card')
  const last = cards?.[cards.length - 1]
  if (last) animate(last, { translateY: [12, 0], scale: [.97, 1], opacity: [0, 1], duration: 420, ease: 'outBack' })
})

watch(() => props.tab, async () => {
  await nextTick()
  const children = root.value?.querySelectorAll('.inspector__content > *')
  if (children?.length) animate(children, { translateY: [6, 0], opacity: [0, 1], delay: stagger(24), duration: 260, ease: 'outCubic' })
})

function addWorkspace() {
  if (!workspacePath.value.trim()) return
  emit('add-workspace', workspaceName.value, workspacePath.value)
  workspaceName.value = ''
  workspacePath.value = ''
}
</script>

<template>
  <aside ref="root" class="inspector">
    <header class="inspector__head">
      <div>
        <strong>Session inspector</strong>
        <small>{{ activeWorkspace?.name || 'No workspace' }}</small>
      </div>
      <button type="button" aria-label="Close inspector" @click="emit('close')">×</button>
    </header>

    <nav class="inspector__tabs">
      <button v-for="name in ['evidence', 'tasks', 'tools', 'workspace']" :key="name" :class="{ active: tab === name }" @click="emit('update:tab', name)">
        {{ name }}
      </button>
    </nav>

    <div class="inspector__content">
      <template v-if="tab === 'evidence'">
        <section class="confidence-card" :class="`is-${run.status}`">
          <div class="confidence-card__top">
            <span>Hypothesis confidence</span>
            <strong>{{ percent }}%</strong>
          </div>
          <div class="confidence-card__track"><i ref="confidenceBar"></i></div>
          <p>{{ run.hypothesis || 'Send a hypothesis to begin gathering evidence.' }}</p>
          <div class="confidence-card__counts">
            <span class="support">+{{ supportCount }} support</span>
            <span class="oppose">−{{ opposeCount }} oppose</span>
          </div>
          <div class="phase-strip">
            <span
              v-for="[key, label] in phases"
              :key="key"
              :class="{ active: run.phase === key }"
            >{{ label }}</span>
          </div>
        </section>

        <TransitionGroup name="evidence-list" tag="div" class="evidence-list">
          <article v-for="item in run.evidence" :key="item.id" class="evidence-card" :class="`is-${item.stance}`">
            <span class="evidence-card__sign">{{ item.stance === 'support' ? '+' : '−' }}</span>
            <div>
              <strong>{{ item.claim }}</strong>
              <small>{{ item.source_uri }}</small>
              <p>{{ item.excerpt }}</p>
            </div>
            <b>{{ Math.round(item.strength * 100) }}</b>
          </article>
        </TransitionGroup>

        <section v-if="run.relations.length" class="relation-list">
          <strong>Relationships</strong>
          <div v-for="(edge, index) in run.relations" :key="`${edge.source_id}-${edge.target_id}-${index}`">
            <span>{{ edge.source_id }}</span>
            <i>{{ edge.relation }}</i>
            <span>{{ edge.target_id }}</span>
          </div>
        </section>

        <section v-if="run.verdict" class="verdict-card" :class="`is-${run.verdict.verdict}`">
          <small>Verdict</small>
          <strong>{{ run.verdict.verdict }}</strong>
          <p>{{ run.verdict.summary }}</p>
        </section>
      </template>

      <template v-else-if="tab === 'tasks'">
        <div class="inspector__section-head"><strong>Tasks</strong><span>{{ todos.filter(item => !item.done).length }} remaining</span></div>
        <button v-for="item in todos" :key="item.id" class="task-row" :class="{ done: item.done }" @click="emit('toggle-todo', item.id)">
          <i>{{ item.done ? '✓' : '' }}</i><span>{{ item.content }}</span>
        </button>
      </template>

      <template v-else-if="tab === 'tools'">
        <div class="inspector__section-head"><strong>Built-in tools</strong><span>{{ tools.length }} available</span></div>
        <article v-for="tool in tools" :key="tool.name" class="inspector-tool">
          <b>{{ tool.name.slice(0, 1).toUpperCase() }}</b>
          <div><strong>{{ tool.name }}</strong><small>{{ tool.description }}</small></div>
          <span>{{ Object.keys(tool.parameters?.properties || {}).length }}</span>
        </article>
      </template>

      <template v-else>
        <div class="inspector__section-head"><strong>Workspaces</strong><span>Agent scope</span></div>
        <p class="workspace-note">All local discovery and file previews are constrained to the active root.</p>
        <p v-if="workspaceError" class="workspace-error">{{ workspaceError }}</p>
        <article v-for="workspace in workspaces" :key="workspace.id" class="workspace-row" :class="{ active: workspace.is_active }">
          <button @click="emit('activate-workspace', workspace.id)">
            <i></i>
            <span><strong>{{ workspace.name }}</strong><small>{{ workspace.path }}</small></span>
          </button>
          <button v-if="!workspace.is_active" class="workspace-row__delete" @click="emit('delete-workspace', workspace.id)">×</button>
        </article>
        <form class="workspace-form" @submit.prevent="addWorkspace">
          <input v-model="workspaceName" placeholder="Workspace name (optional)" />
          <input v-model="workspacePath" placeholder="Absolute directory path" />
          <button>Add workspace</button>
        </form>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.inspector { flex: 0 0 342px; width: 342px; min-width: 0; overflow: hidden; border-left: 1px solid #cbd9ec; color: #233d5b; background: rgba(247, 250, 255, .96); box-shadow: -16px 0 42px rgba(31, 67, 119, .08); }
.inspector__head { display: flex; height: 58px; align-items: center; justify-content: space-between; padding: 0 14px 0 17px; border-bottom: 1px solid #d8e3f1; }
.inspector__head div { display: grid; gap: 2px; }.inspector__head strong { font-size: 12px; }.inspector__head small { max-width: 240px; overflow: hidden; color: #8192a8; font: 9.5px/1.2 var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.inspector__head button { width: 27px; height: 27px; border: 0; border-radius: 7px; color: #6d829d; background: transparent; font-size: 18px; cursor: pointer; }.inspector__head button:hover { background: #e6eef9; }
.inspector__tabs { display: grid; grid-template-columns: repeat(4, 1fr); gap: 3px; padding: 7px; border-bottom: 1px solid #dbe5f2; }
.inspector__tabs button { padding: 7px 2px; border: 0; border-radius: 6px; color: #71859f; background: transparent; font: 700 9px/1 var(--mono); text-transform: uppercase; cursor: pointer; }.inspector__tabs button.active { color: #fff; background: #1756d1; box-shadow: 0 4px 12px rgba(23, 86, 209, .22); }
.inspector__content { height: calc(100% - 102px); overflow-y: auto; padding: 12px; scrollbar-gutter: stable; }
.confidence-card { padding: 13px; border: 1px solid #cbdcf2; border-radius: 12px; background: #fff; box-shadow: 0 7px 20px rgba(34, 70, 118, .06); }
.confidence-card__top { display: flex; align-items: baseline; justify-content: space-between; color: #71859e; font: 700 9px/1 var(--mono); text-transform: uppercase; }.confidence-card__top strong { color: #1756d1; font-size: 20px; }
.confidence-card__track { height: 7px; margin: 9px 0 11px; overflow: hidden; border-radius: 9px; background: #e2ebf7; }.confidence-card__track i { display: block; width: 100%; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #1756d1 0 55%, #f5c642); transform: scaleX(.5); transform-origin: left; }
.confidence-card p { margin: 0; color: #344f6f; font-size: 11px; line-height: 1.55; }.confidence-card__counts { display: flex; gap: 12px; margin-top: 10px; font: 700 9px/1 var(--mono); }.support { color: #11866f; }.oppose { color: #b77d00; }
.phase-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin-top: 10px; }.phase-strip span { padding: 5px 3px; border: 1px solid #d7e3f2; border-radius: 6px; color: #7d90a7; background: #f7faff; font: 700 7.5px/1 var(--mono); text-align: center; text-transform: uppercase; }.phase-strip span.active { border-color: #1756d1; color: #fff; background: #1756d1; box-shadow: 0 4px 12px rgba(23,86,209,.18); }
.evidence-list { display: grid; gap: 7px; margin-top: 10px; }.evidence-card { position: relative; display: grid; grid-template-columns: 22px 1fr 25px; gap: 8px; padding: 10px; border: 1px solid #d6e1ef; border-radius: 10px; background: #fff; }.evidence-card.is-support { border-left: 3px solid #11866f; }.evidence-card.is-oppose { border-left: 3px solid #e0a300; }
.evidence-card__sign { display: grid; width: 21px; height: 21px; place-items: center; border-radius: 6px; color: #fff; background: #11866f; font-weight: 900; }.is-oppose .evidence-card__sign { background: #d99b00; }.evidence-card div { min-width: 0; }.evidence-card strong { display: block; color: #294564; font-size: 10.5px; line-height: 1.4; }.evidence-card small { display: block; overflow: hidden; margin-top: 3px; color: #7188a3; font: 8.5px/1.3 var(--mono); text-overflow: ellipsis; white-space: nowrap; }.evidence-card p { max-height: 48px; overflow: hidden; margin: 7px 0 0; color: #617791; font: 9px/1.45 var(--mono); }.evidence-card b { color: #8799ad; font: 800 10px/1 var(--mono); }
.relation-list,.verdict-card { margin-top: 10px; padding: 11px; border: 1px solid #d8e2ef; border-radius: 10px; background: #fff; }.relation-list > strong { font-size: 10px; }.relation-list div { display: flex; align-items: center; gap: 6px; margin-top: 7px; font: 8.5px/1 var(--mono); }.relation-list i { flex: 1; height: 1px; color: #6658c7; background: #c9c3ef; text-align: center; }
.verdict-card { border-top: 3px solid #1756d1; }.verdict-card.is-supported { border-top-color: #11866f; }.verdict-card.is-refuted { border-top-color: #c44747; }.verdict-card small { color: #8798ac; font: 700 8px/1 var(--mono); text-transform: uppercase; }.verdict-card strong { display: block; margin: 4px 0; font-size: 16px; text-transform: capitalize; }.verdict-card p { margin: 0; color: #566d88; font-size: 10px; line-height: 1.5; }
.inspector__section-head { display: flex; align-items: center; justify-content: space-between; margin: 2px 2px 10px; }.inspector__section-head strong { font-size: 12px; }.inspector__section-head span { color: #8294aa; font: 9px/1 var(--mono); }
.task-row { display: flex; width: 100%; align-items: center; gap: 8px; padding: 9px; border: 0; border-radius: 8px; color: #37516f; background: transparent; text-align: left; cursor: pointer; }.task-row:hover { background: #eaf1fb; }.task-row i { display: grid; width: 17px; height: 17px; place-items: center; border: 1px solid #a9bad0; border-radius: 5px; color: #11866f; font-style: normal; }.task-row.done span { color: #91a0b2; text-decoration: line-through; }
.inspector-tool { display: grid; grid-template-columns: 28px 1fr auto; align-items: center; gap: 9px; margin-bottom: 6px; padding: 9px; border: 1px solid #d7e2ef; border-radius: 9px; background: #fff; }.inspector-tool > b { display: grid; width: 27px; height: 27px; place-items: center; border-radius: 7px; color: #1756d1; background: #e8f0ff; font: 800 10px/1 var(--mono); }.inspector-tool div { display: grid; gap: 3px; }.inspector-tool strong { font: 700 10px/1 var(--mono); }.inspector-tool small { color: #778ba4; font-size: 9px; line-height: 1.35; }.inspector-tool > span { color: #8da0b6; font: 800 9px/1 var(--mono); }
.workspace-note { margin: -2px 2px 10px; color: #71869f; font-size: 9.5px; line-height: 1.5; }.workspace-error { padding: 8px; border-radius: 7px; color: #a33; background: #fff0f0; font-size: 9px; }
.workspace-row { display: flex; align-items: center; margin-bottom: 6px; border: 1px solid #d8e2ef; border-radius: 9px; background: #fff; }.workspace-row.active { border-color: #83a9ec; box-shadow: 0 0 0 2px rgba(23, 86, 209, .08); }.workspace-row > button:first-child { display: flex; min-width: 0; flex: 1; align-items: center; gap: 8px; padding: 9px; border: 0; background: transparent; text-align: left; cursor: pointer; }.workspace-row i { width: 8px; height: 8px; border: 2px solid #a5b7cd; border-radius: 50%; }.workspace-row.active i { border-color: #1756d1; background: #1756d1; box-shadow: 0 0 0 3px #dce8ff; }.workspace-row span { display: grid; min-width: 0; gap: 3px; }.workspace-row strong { font-size: 10px; }.workspace-row small { overflow: hidden; color: #7c8fa6; font: 8.5px/1.2 var(--mono); text-overflow: ellipsis; white-space: nowrap; }.workspace-row__delete { margin-right: 6px; border: 0; color: #98a8bb; background: transparent; cursor: pointer; }
.workspace-form { display: grid; gap: 6px; margin-top: 12px; padding-top: 12px; border-top: 1px solid #dbe4f0; }.workspace-form input { height: 32px; padding: 0 9px; border: 1px solid #ccd9e9; border-radius: 7px; color: #29435f; background: #fff; font: 9px/1 var(--mono); outline: none; }.workspace-form input:focus { border-color: #1756d1; }.workspace-form button { height: 31px; border: 0; border-radius: 7px; color: #fff; background: #1756d1; font: 700 10px/1 var(--mono); cursor: pointer; }
.evidence-list-enter-active,.evidence-list-leave-active { transition: opacity .25s, transform .3s cubic-bezier(.22,1,.36,1); }.evidence-list-enter-from,.evidence-list-leave-to { opacity: 0; transform: translateY(10px) scale(.98); }
@media (max-width: 980px) { .inspector { position: absolute; z-index: 20; inset: 0 0 0 auto; height: 100%; } }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>
