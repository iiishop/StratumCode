<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

const props = defineProps({
  tree: { type: Object, required: true },
  visible: { type: Boolean, default: true },
})
const viewport = ref(null)
const follow = ref(true)
const selectedId = ref('')
const viewportSize = ref({ width: 320, height: 360 })
const idOf = value => String(value || '').split(':').pop()
const activeId = computed(() => idOf(props.tree.active_unknown_id))
const pathIds = computed(() => new Set((props.tree.active_path || []).map(idOf)))
const width = 224
const height = 110
const gap = 24
const row = 154
const layout = computed(() => {
  const resolutions = new Map((props.tree.resolutions || []).map(r => [idOf(r.unknown_id), r]))
  const nodes = (props.tree.nodes || []).map(n => ({
    ...n, id: idOf(n.id), parent_id: idOf(n.parent_id),
    resolution: resolutions.get(idOf(n.id)),
  }))
  const byId = new Map(nodes.map(n => [n.id, n]))
  const children = new Map()
  for (const node of nodes) {
    const parent = byId.has(node.parent_id) ? node.parent_id : ''
    if (!children.has(parent)) children.set(parent, [])
    children.get(parent).push(node)
  }
  const insetX = Math.max(24, (viewportSize.value.width - width) / 2)
  const insetY = Math.max(24, (viewportSize.value.height - height) / 2)
  let cursor = insetX
  const placed = []
  const visited = new Set()
  function place(node, depth) {
    if (visited.has(node.id)) return null
    visited.add(node.id)
    const descendants = (children.get(node.id) || []).map(n => place(n, depth + 1)).filter(Boolean)
    const x = descendants.length
      ? (descendants[0].x + descendants[descendants.length - 1].x) / 2
      : cursor
    if (!descendants.length) cursor += width + gap
    const positioned = { ...node, x, y: insetY + depth * row, depth }
    placed.push(positioned)
    return positioned
  }
  for (const root of children.get('') || []) place(root, 0)
  // Old session data may contain missing parents or cycles; still show every node.
  for (const node of nodes) if (!visited.has(node.id)) place(node, 0)
  const positions = new Map(placed.map(n => [n.id, n]))
  const edges = placed.flatMap(n => {
    const parent = positions.get(n.parent_id)
    if (!parent || parent.depth >= n.depth) return []
    const x1 = parent.x + width / 2
    const y1 = parent.y + height
    const x2 = n.x + width / 2
    const y2 = n.y
    return [{ id: n.id, d: `M${x1},${y1} V${(y1 + y2) / 2} H${x2} V${y2}` }]
  })
  return {
    nodes: placed.sort((a, b) => a.y - b.y || a.x - b.x), edges,
    width: Math.max(viewportSize.value.width, cursor - gap + insetX),
    height: Math.max(viewportSize.value.height, ...placed.map(n => n.y + height + insetY)),
  }
})
const selected = computed(() => layout.value.nodes.find(n => n.id === (selectedId.value || activeId.value)))
const status = n => n.id === activeId.value ? 'Researching' : n.resolution?.status || n.status || 'pending'
const closed = n => ['resolved', 'known'].includes(status(n)) || (status(n) === 'deferred' && !n.blocking)

async function centerActive() {
  await nextTick()
  const el = viewport.value
  const node = layout.value.nodes.find(n => n.id === activeId.value)
    || layout.value.nodes.find(n => !n.parent_id)
  if (!el || !node || !props.visible || !el.clientWidth) return
  el.scrollTo({
    left: node.x + width / 2 - el.clientWidth / 2,
    top: node.y + height / 2 - el.clientHeight / 2,
    behavior: 'instant',
  })
}
function resumeFollow() {
  follow.value = true
  selectedId.value = ''
  centerActive()
}
function inspect(node) {
  selectedId.value = node.id
  follow.value = false
}
watch([layout, activeId, () => props.visible], () => {
  if (follow.value) centerActive()
}, { flush: 'post' })
let observer
onMounted(() => {
  observer = new ResizeObserver(() => {
    const el = viewport.value
    if (el?.clientWidth && el.clientHeight) viewportSize.value = { width: el.clientWidth, height: el.clientHeight }
    if (follow.value) centerActive()
  })
  if (viewport.value) observer.observe(viewport.value)
  centerActive()
})
onUnmounted(() => observer?.disconnect())
</script>

<template>
  <section class="unknown-tree" aria-label="Unknown investigation tree">
    <header class="unknown-tree__header">
      <div><strong>Unknown tree</strong><small>{{ tree.phase || 'Investigation' }} · {{ layout.nodes.length }} nodes</small></div>
      <button type="button" :aria-pressed="follow" :class="{ following: follow }" @click="resumeFollow">
        {{ follow ? 'Following current' : 'Center current' }}
      </button>
    </header>
    <p class="unknown-tree__path" aria-live="polite">
      {{ activeId ? (tree.active_path || [activeId]).join(' / ') : tree.phase === 'done' ? 'Investigation complete' : tree.phase === 'incomplete' ? 'Investigation stopped' : tree.phase === 'history' ? 'Saved questions (available dependencies)' : 'Reviewing the goal' }}
    </p>
    <div ref="viewport" class="unknown-tree__viewport" tabindex="0" aria-label="Scrollable investigation tree"
      @wheel.passive="follow = false" @touchstart.passive="follow = false" @keydown="follow = false">
      <div class="unknown-tree__canvas" :style="{ width: layout.width + 'px', height: layout.height + 'px' }">
        <svg class="unknown-tree__edges" :width="layout.width" :height="layout.height" aria-hidden="true">
          <path v-for="edge in layout.edges" :key="edge.id" :d="edge.d" :class="{ active: pathIds.has(edge.id) }" />
        </svg>
        <button v-for="node in layout.nodes" :key="node.id" type="button" class="unknown-tree__node"
          :class="{ current: node.id === activeId, ancestor: pathIds.has(node.id), closed: closed(node), selected: node.id === selectedId }"
          :style="{ left: node.x + 'px', top: node.y + 'px', width: width + 'px', height: height + 'px' }"
          :aria-current="node.id === activeId ? 'step' : undefined"
          :aria-label="`${node.id}: ${node.question}. ${status(node)}`" :title="node.question" @click="inspect(node)">
          <span class="unknown-tree__node-head"><b>{{ node.id }}</b><small>{{ node.domain }}</small></span>
          <span class="unknown-tree__question">{{ node.question }}</span>
          <span class="unknown-tree__status"><i />{{ status(node).replaceAll('_', ' ') }}</span>
        </button>
      </div>
    </div>
    <div v-if="selected" class="unknown-tree__detail">
      <strong>{{ selected.id }} · {{ selected.domain }}</strong>
      <p>{{ selected.question }}</p>
      <p v-if="selected.resolution?.answer" class="unknown-tree__answer">{{ selected.resolution.answer }}</p>
      <small v-else>{{ selected.why || 'Research this question, then return to its parent.' }}</small>
    </div>
  </section>
</template>

<style scoped>
.unknown-tree { --tree-accent: #286ac4; --tree-ink: #24374b; margin: 12px 0 18px; border: 1px solid #d6e0ea; border-radius: 10px; overflow: hidden; color: var(--tree-ink); background: #fafcfe; }
.unknown-tree__header { display: flex; justify-content: space-between; gap: 8px; align-items: center; padding: 12px; }
.unknown-tree__header strong { display: block; font-size: 12px; }
.unknown-tree__header small { display: block; margin-top: 4px; font-size: 10px; color: #6a7a8e; }
.unknown-tree__header button { border: 1px solid #d3deeb; border-radius: 6px; padding: 6px 8px; font: inherit; font-size: 10px; background: white; cursor: pointer; color: var(--tree-ink); }
.unknown-tree__header button.following { background: #e9f2ff; border-color: #adcdf2; color: #245da5; }
.unknown-tree__path { margin: 0; padding: 0 12px 10px; color: #4c6e94; font-size: 10px; overflow-wrap: anywhere; }
.unknown-tree__viewport { height: 360px; max-height: 55vh; overflow: auto; overscroll-behavior: contain; border-block: 1px solid #e0e7ef; background: radial-gradient(circle, #d6e1ed 1px, transparent 1px) 0 0 / 16px 16px, #f3f7fb; }
.unknown-tree__canvas { position: relative; }
.unknown-tree__edges { position: absolute; inset: 0; pointer-events: none; }
.unknown-tree__edges path { fill: none; stroke: #b6c7d9; stroke-width: 1.5; }
.unknown-tree__edges path.active { stroke: var(--tree-accent); stroke-width: 2.5; }
.unknown-tree__node { position: absolute; display: flex; flex-direction: column; gap: 8px; text-align: left; padding: 12px; border: 1px solid #cbd7e3; border-radius: 8px; background: white; color: var(--tree-ink); font: inherit; cursor: pointer; box-shadow: 0 3px 10px #27425c09; }
.unknown-tree__node.ancestor { border-color: #8cb6e9; }
.unknown-tree__node.current { border: 2px solid var(--tree-accent); padding: 11px; background: #edf5ff; box-shadow: 0 0 0 4px #286ac419, 0 5px 16px #286ac414; }
.unknown-tree__node.selected { outline: 2px solid #71839a; outline-offset: 3px; }
.unknown-tree__node:focus-visible, button:focus-visible { outline: 2px solid var(--tree-accent); outline-offset: 3px; }
.unknown-tree__node-head { display: flex; justify-content: space-between; gap: 8px; font-size: 10px; }
.unknown-tree__node-head b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.unknown-tree__node-head small { color: #6c8097; }
.unknown-tree__question { font-size: 12px; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.unknown-tree__status { display: flex; gap: 5px; align-items: center; font-size: 9px; color: #687e95; margin-top: auto; }
.unknown-tree__status i { width: 6px; height: 6px; border-radius: 50%; background: #a4b3c3; }
.current .unknown-tree__status { color: var(--tree-accent); font-weight: 600; }
.current .unknown-tree__status i { background: var(--tree-accent); box-shadow: 0 0 0 3px #286ac420; }
.closed .unknown-tree__status i { background: #328669; }
.unknown-tree__detail { padding: 12px; font-size: 11px; line-height: 1.65; overflow-wrap: anywhere; max-height: 220px; overflow: auto; }
.unknown-tree__detail p { margin: 5px 0; white-space: pre-wrap; }
.unknown-tree__detail small, .unknown-tree__answer { color: #61748a; }
@media (max-width: 480px) { .unknown-tree__viewport { height: 300px; } }
</style>
