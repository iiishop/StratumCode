<script setup>
import { computed, onMounted, ref } from 'vue'
import { gsap } from 'gsap'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const open = ref(true)
const rootRef = ref(null)
const expandedReasons = ref({})

function toggleReason(key) {
  expandedReasons.value[key] = !expandedReasons.value[key]
}

const changes = computed(() => {
  if (Array.isArray(props.event.changes) && props.event.changes.length) return props.event.changes
  return (props.event.items || []).map(item => ({ action: 'update', item }))
})

const counts = computed(() => changes.value.reduce((acc, change) => {
  acc[change.action] = (acc[change.action] || 0) + 1
  return acc
}, {}))

const detail = computed(() => {
  const parts = [
    counts.value.add && `${counts.value.add} added`,
    counts.value.status && `${counts.value.status} transition`,
    counts.value.update && `${counts.value.update} updated`,
  ].filter(Boolean)
  return parts.join(' · ') || `${props.event.items?.length || 0} task items`
})

function actionLabel(change) {
  if (change.action === 'status') {
    return `${change.before?.status || '?'} → ${change.item?.status || '?'}`
  }
  return change.item?.status || (change.action === 'add' ? 'new' : 'updated')
}

function changeIcon(action) {
  if (action === 'add') return 'add'
  if (action === 'status') return 'status'
  return 'update'
}

function kindLabel(kind) {
  return { goal: 'Goal', acceptance: 'Acceptance', behavior: 'Behavior', boundary: 'Boundary', constraint: 'Constraint', unknown: 'Unknown' }[kind] || kind
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  const el = rootRef.value
  if (!el) return
  gsap.fromTo(el.querySelectorAll('.tu__item'), { y: 8, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.3, stagger: 0.05, ease: 'power2.out' })
})
</script>

<template>
  <EventFrame
    kind="task-update"
    symbol="T"
    label="Task update"
    :detail="detail"
    status="updated"
    state="done"
    :open="open"
    collapsible
    @toggle="open = !open"
  >
    <div ref="rootRef" class="tu">
      <!-- summary card -->
      <div class="tu__summary">
        <div class="tu__counts">
          <span v-if="counts.add" class="tu__count tu__count--add">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            {{ counts.add }} added
          </span>
          <span v-if="counts.status" class="tu__count tu__count--status">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="14 9 19 4"/><path d="M21 3l-8 12-5-3-5 7"/></svg>
            {{ counts.status }} transition
          </span>
          <span v-if="counts.update" class="tu__count tu__count--update">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 102.13-9.36L1 10"/></svg>
            {{ counts.update }} updated
          </span>
        </div>
      </div>

      <!-- items -->
      <div class="tu__items">
        <div
          v-for="change in changes"
          :key="`${change.action}:${change.item?.id || change.item?.text}`"
          class="tu__item"
          :class="`tu__item--${change.action}`"
        >
          <div class="tu__item-head">
            <!-- icon -->
            <span class="tu__icon" :class="`tu__icon--${changeIcon(change.action)}`">
              <svg v-if="changeIcon(change.action) === 'add'" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              <svg v-else-if="changeIcon(change.action) === 'status'" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><polyline points="14 9 19 4"/><path d="M21 3l-8 12-5-3-5 7"/></svg>
              <svg v-else width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 102.13-9.36L1 10"/></svg>
            </span>

            <div class="tu__item-meta">
              <!-- status transition for status changes -->
              <span v-if="change.action === 'status' && change.before" class="tu__transition">
                <span class="tu__from">{{ change.before.status }}</span>
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14M13 5l7 7-7 7"/></svg>
                <span class="tu__to">{{ change.item?.status }}</span>
              </span>
              <span v-else class="tu__action-badge" :class="`tu__action-badge--${change.action}`">
                {{ actionLabel(change) }}
              </span>

              <span v-if="change.item?.kind" class="tu__kind-tag">{{ kindLabel(change.item.kind) }}</span>
              <span v-if="change.item?.id" class="tu__id-tag">{{ change.item.id }}</span>
            </div>
          </div>

          <p class="tu__text">{{ change.item?.text }}</p>

          <div v-if="change.item?.reason" class="tu__reason-wrap">
            <button class="tu__reason-toggle" @click="toggleReason(change.item?.id || change.item?.text)">
              reason
              <span :class="{ 'is-open': expandedReasons[change.item?.id || change.item?.text] }">&#9660;</span>
            </button>
            <p v-if="expandedReasons[change.item?.id || change.item?.text]" class="tu__reason">{{ change.item.reason }}</p>
          </div>

          <div v-if="change.item?.trace?.length" class="tu__trace">
            <span v-for="(t, i) in change.item.trace" :key="i" class="tu__trace-step">
              {{ t }}
              <span v-if="i < change.item.trace.length - 1" class="tu__trace-arrow">&#8594;</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  </EventFrame>
</template>

<style scoped>
.tu {
  display: grid;
  gap: 8px;
}

/* ---- summary ---- */
.tu__summary {
  padding: 8px 10px;
  border-radius: 7px;
  background: var(--code-bg, #f1f4f8);
}

.tu__counts {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.tu__count {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font: 700 10px/1 var(--mono, monospace);
}

.tu__count--add    { color: #11866f; }
.tu__count--status { color: #c48b00; }
.tu__count--update { color: #1756d1; }

/* ---- items ---- */
.tu__items {
  display: grid;
  gap: 5px;
}

.tu__item {
  display: grid;
  gap: 5px;
  padding: 8px 10px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 7px;
  background: var(--bg-raised, #ffffff);
}

.tu__item--add    { border-left: 3px solid #11866f; background: rgba(17,134,111,.03); }
.tu__item--status { border-left: 3px solid #c48b00; background: rgba(196,139,0,.03); }
.tu__item--update { border-left: 3px solid #1756d1; background: rgba(23,86,209,.025); }

.tu__item-head {
  display: flex;
  align-items: flex-start;
  gap: 7px;
}

.tu__icon {
  display: grid;
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  place-items: center;
  border-radius: 5px;
}

.tu__icon--add    { color: #11866f; background: rgba(17,134,111,.1); }
.tu__icon--status { color: #c48b00; background: rgba(196,139,0,.12); }
.tu__icon--update { color: #1756d1; background: rgba(23,86,209,.1); }

.tu__item-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  min-width: 0;
}

.tu__action-badge {
  padding: 1px 6px;
  border-radius: 3px;
  font: 700 8px/1.3 var(--mono, monospace);
  text-transform: uppercase;
  white-space: nowrap;
}

.tu__action-badge--add    { color: #11866f; background: rgba(17,134,111,.1); }
.tu__action-badge--status { color: #8a5b00; background: rgba(196,139,0,.12); }
.tu__action-badge--update { color: #1748a3; background: rgba(23,86,209,.08); }

.tu__transition {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--text-muted);
}

.tu__from {
  padding: 1px 5px;
  border-radius: 3px;
  color: var(--text-muted);
  background: rgba(113,128,156,.08);
  font: 700 8px/1.2 var(--mono, monospace);
  text-transform: uppercase;
  text-decoration: line-through;
}

.tu__to {
  padding: 1px 5px;
  border-radius: 3px;
  color: #8a5b00;
  background: rgba(196,139,0,.1);
  font: 700 8px/1.2 var(--mono, monospace);
  text-transform: uppercase;
}

.tu__kind-tag {
  padding: 1px 5px;
  border-radius: 3px;
  color: var(--text-muted);
  background: rgba(113,128,156,.06);
  font: 600 8px/1.2 var(--mono, monospace);
}

.tu__id-tag {
  padding: 1px 4px;
  border-radius: 3px;
  color: var(--text-muted);
  background: transparent;
  font: 8px/1.2 var(--mono, monospace);
  border: 1px solid var(--border, #d9e3f5);
}

.tu__text {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

/* ---- reason ---- */
.tu__reason-wrap {
  display: grid;
  gap: 4px;
}

.tu__reason-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  width: fit-content;
  padding: 2px 6px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 4px;
  color: var(--text-muted);
  background: transparent;
  cursor: pointer;
  font: 700 7.5px/1 var(--mono, monospace);
  text-transform: uppercase;
}

.tu__reason-toggle span {
  font-size: 6px;
  transition: transform .2s ease;
}

.tu__reason-toggle span.is-open {
  transform: rotate(180deg);
}

.tu__reason {
  margin: 0;
  padding: 5px 7px;
  border-radius: 4px;
  color: var(--text-muted);
  background: rgba(0,0,0,.025);
  font: 9px/1.4 var(--mono, monospace);
  overflow-wrap: anywhere;
}

/* ---- trace ---- */
.tu__trace {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 3px;
}

.tu__trace-step {
  padding: 1px 5px;
  border-radius: 3px;
  color: var(--accent-text, #1748a3);
  background: var(--accent-bg, rgba(23,86,209,.06));
  font: 8px/1.35 var(--mono, monospace);
}

.tu__trace-arrow {
  color: var(--text-muted);
  font-size: 7px;
  margin: 0 1px;
}
</style>
