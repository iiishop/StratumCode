<script setup>
import { computed, onMounted, ref } from 'vue'
import { gsap } from 'gsap'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })
const rootRef = ref(null)
const summary = computed(() => props.event.intent?.summary || 'Task analyzed')
const intentType = computed(() => props.event.intent?.type || 'other')
const constraints = computed(() => props.event.constraints || [])
const acceptance = computed(() => props.event.acceptance_criteria || [])
const hypotheses = computed(() => props.event.hypotheses || [])
const clues = computed(() => (props.event.clues || []).filter(clue => !isWorkspaceSnapshotClue(clue)))
const unknowns = computed(() => props.event.unknowns || [])
const analyzerErrors = computed(() => props.event.analyzer_errors || [])
const hasAnalyzerDiagnostics = computed(() => props.event.analyzer_error || analyzerErrors.value.length || props.event.recovered_from_partial_analyzer_output)
const scopeRows = computed(() => {
  const scope = props.event.scope || {}
  return [
    ...(scope.in || []).map(text => ({ label: 'In scope', text, kind: 'in' })),
    ...(scope.out || []).map(text => ({ label: 'Out of scope', text, kind: 'out' })),
    ...(scope.undecided || []).map(text => ({ label: 'Undecided', text, kind: 'undecided' })),
  ]
})
const behaviorRows = computed(() => {
  const behavior = props.event.behavior_contract || {}
  return [
    ...(behavior.inputs || []).map(text => ({ label: 'Input', text, kind: 'input' })),
    ...(behavior.outputs || []).map(text => ({ label: 'Output', text, kind: 'output' })),
    ...(behavior.success_behaviors || []).map(text => ({ label: 'Success', text, kind: 'success' })),
    ...(behavior.failure_behaviors || []).map(text => ({ label: 'Failure', text, kind: 'failure' })),
    ...(behavior.boundaries || []).map(text => ({ label: 'Boundary', text, kind: 'boundary' })),
  ]
})

const showBehavior = ref(false)
const showScope = ref(false)

function isWorkspaceSnapshotClue(clue) {
  const raw = String(clue?.path || clue?.value || '')
  const text = raw.trim()
  if (text === 'Workspace snapshot:') return true
  if (text.startsWith('- root:') || text.startsWith('- visible files:') || text.startsWith('- visible directories:') || text.startsWith('- files:')) return true
  return /^-\s+.+\s+\(\d+ bytes(?: \/ empty)?\)$/.test(text)
}

function certaintyLabel(c) {
  return { certain: 'Certain', uncertain: 'Unsure', guess: 'Guess' }[c] || c
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  const el = rootRef.value
  if (!el) return
  gsap.fromTo(el.querySelectorAll('.ta__card'), { y: 8, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.35, stagger: 0.06, ease: 'power2.out' })
})

function onExpandEnter(el, done) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { done(); return }
  gsap.fromTo(el, { height: 0, autoAlpha: 0 }, { height: el.scrollHeight, autoAlpha: 1, duration: 0.3, ease: 'power2.out', onComplete: () => { gsap.set(el, { height: 'auto' }); done() } })
}

function onExpandLeave(el, done) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { done(); return }
  gsap.to(el, { height: 0, autoAlpha: 0, duration: 0.22, ease: 'power2.in', onComplete: done })
}
</script>

<template>
  <EventFrame
    kind="task-analysis"
    symbol="A"
    label="Task analyzer"
    :detail="summary"
    status="done"
    state="done"
    open
  >
    <div ref="rootRef" class="ta">
      <div class="ta__card ta__card--intent">
        <div class="ta__card-head">
          <span class="ta__badge ta__badge--intent">{{ intentType }}</span>
        </div>
        <strong>{{ summary }}</strong>
        <div class="ta__meta" v-if="event.provider && event.model">
          {{ event.provider }} / {{ event.model }}
        </div>
      </div>

      <div v-if="hasAnalyzerDiagnostics" class="ta__card ta__card--diagnostics">
        <div class="ta__card-head">
          <span class="ta__chip ta__chip--diagnostics">Analyzer diagnostics</span>
          <span v-if="event.analyzer_attempts" class="ta__count">{{ event.analyzer_attempts }} attempts</span>
        </div>
        <p v-if="event.analyzer_error" class="ta__diagnostic">{{ event.analyzer_error }}</p>
        <ol v-if="analyzerErrors.length" class="ta__errors">
          <li v-for="(error, index) in analyzerErrors" :key="`${index}-${error}`">{{ error }}</li>
        </ol>
      </div>

      <div v-if="constraints.length" class="ta__card">
        <div class="ta__card-head">
          <span class="ta__chip ta__chip--constraint">Constraints</span>
          <span class="ta__count">{{ constraints.length }}</span>
        </div>
        <ul class="ta__list">
          <li v-for="c in constraints" :key="c">{{ c }}</li>
        </ul>
      </div>

      <div v-if="clues.length" class="ta__card">
        <div class="ta__card-head">
          <span class="ta__chip ta__chip--clue">Clues</span>
          <span class="ta__count">{{ clues.length }}</span>
        </div>
        <div class="ta__clues">
          <span v-for="c in clues" :key="c.value + c.path" class="ta__clue">
            <span>{{ c.path || c.value }}</span>
            <em v-if="c.line">:{{ c.line }}</em>
          </span>
        </div>
      </div>

      <div v-if="hypotheses.length" class="ta__card">
        <div class="ta__card-head">
          <span class="ta__chip ta__chip--hypothesis">Hypotheses</span>
          <span class="ta__count">{{ hypotheses.length }}</span>
        </div>
        <div class="ta__hypotheses">
          <div v-for="h in hypotheses" :key="h.text" class="ta__hypo" :class="`is-${h.certainty}`">
            <span class="ta__hypo-certainty">{{ certaintyLabel(h.certainty) }}</span>
            <p>{{ h.text }}</p>
          </div>
        </div>
      </div>

      <div v-if="unknowns.length" class="ta__card">
        <div class="ta__card-head">
          <span class="ta__chip ta__chip--unknown">Unknowns</span>
          <span class="ta__count">{{ unknowns.length }}</span>
        </div>
        <ul class="ta__unknowns">
          <li v-for="u in unknowns" :key="u.id || u" :class="{ 'is-blocking': u.blocking !== false }">
            <b v-if="u.id">{{ u.id }}</b>
            <span>{{ typeof u === 'string' ? u : u.question || u.text }}</span>
            <em v-if="u.blocking === false">deferred</em>
          </li>
        </ul>
      </div>

      <div v-if="acceptance.length" class="ta__card">
        <div class="ta__card-head">
          <span class="ta__chip ta__chip--acceptance">Acceptance</span>
          <span class="ta__count">{{ acceptance.length }}</span>
        </div>
        <ul class="ta__list">
          <li v-for="a in acceptance" :key="a.id || a.text">
            {{ a.text }}
          </li>
        </ul>
      </div>

      <div v-if="behaviorRows.length" class="ta__card">
        <button class="ta__card-head ta__toggle" @click="showBehavior = !showBehavior">
          <span class="ta__chip ta__chip--behavior">Behavior contract</span>
          <span class="ta__count">{{ behaviorRows.length }}</span>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :style="{ transform: showBehavior ? 'rotate(180deg)' : '' }"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        <Transition name="ta-expand" @enter="onExpandEnter" @leave="onExpandLeave">
          <div v-if="showBehavior" class="ta__behaviors">
            <div v-for="b in behaviorRows" :key="b.label + b.text" class="ta__behavior" :class="`is-${b.kind}`">
              <span>{{ b.label }}</span>
              <p>{{ b.text }}</p>
            </div>
          </div>
        </Transition>
      </div>

      <div v-if="scopeRows.length" class="ta__card">
        <button class="ta__card-head ta__toggle" @click="showScope = !showScope">
          <span class="ta__chip ta__chip--scope">Scope</span>
          <span class="ta__count">{{ scopeRows.length }}</span>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :style="{ transform: showScope ? 'rotate(180deg)' : '' }"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        <Transition name="ta-expand" @enter="onExpandEnter" @leave="onExpandLeave">
          <div v-if="showScope" class="ta__scopes">
            <div v-for="s in scopeRows" :key="s.label + s.text" class="ta__scope" :class="`is-${s.kind}`">
              <span>{{ s.label }}</span>
              <p>{{ s.text }}</p>
            </div>
          </div>
        </Transition>
      </div>
    </div>
  </EventFrame>
</template>

<style scoped>
.ta {
  display: grid;
  gap: 8px;
}

.ta__card {
  display: grid;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 8px;
  background: var(--bg-raised, #ffffff);
}

.ta__card--intent {
  border-color: color-mix(in srgb, var(--event, #6658c7) 30%, #d9e3f5);
  background: linear-gradient(135deg, color-mix(in srgb, var(--event, #6658c7) 4%, #fff), #fff);
}

.ta__card-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ta__card--intent strong {
  color: var(--text-h, #102a5c);
  font-size: 13px;
  line-height: 1.45;
}

.ta__meta {
  color: var(--text-muted, #71809c);
  font: 9px/1 var(--mono);
}

.ta__badge {
  padding: 3px 8px;
  border-radius: 5px;
  font: 700 8px/1 var(--mono);
  text-transform: uppercase;
}

.ta__badge--intent {
  color: #fff;
  background: var(--event, #6658c7);
}

.ta__chip {
  padding: 2px 7px;
  border-radius: 4px;
  font: 700 8px/1 var(--mono);
  text-transform: uppercase;
}

.ta__chip--constraint { color: #b77d00; background: rgba(183,125,0,.1); }
.ta__chip--clue { color: var(--accent-text, #1748a3); background: var(--accent-bg, rgba(23,86,209,.08)); }
.ta__chip--hypothesis { color: #7b2d8e; background: rgba(123,45,142,.08); }
.ta__chip--unknown { color: var(--warn, #c48b00); background: rgba(196,139,0,.08); }
.ta__chip--acceptance { color: var(--ok, #11866f); background: var(--ok-bg, rgba(17,134,111,.08)); }
.ta__chip--behavior { color: #3b5998; background: rgba(59,89,152,.08); }
.ta__chip--scope { color: #4a6d7c; background: rgba(74,109,124,.08); }
.ta__chip--diagnostics { color: var(--err, #c44747); background: rgba(196,71,71,.08); }

.ta__count {
  color: var(--text-muted, #71809c);
  font: 9px/1 var(--mono);
  margin-left: auto;
}

.ta__toggle {
  cursor: pointer;
  border: 0;
  background: transparent;
  padding: 0;
  width: 100%;
}

.ta__toggle svg {
  color: var(--text-muted);
  transition: transform .2s ease;
}

.ta__list {
  margin: 0;
  padding: 0 0 0 12px;
  list-style: none;
  display: grid;
  gap: 4px;
}

.ta__list li {
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
  position: relative;
}

.ta__list li::before {
  content: '';
  position: absolute;
  left: -10px;
  top: 6px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--text-muted);
}

.ta__card--diagnostics {
  border-color: rgba(196,71,71,.22);
  background: rgba(196,71,71,.035);
}

.ta__diagnostic {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 10.5px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.ta__errors {
  display: grid;
  gap: 4px;
  margin: 0;
  padding-left: 16px;
  color: var(--text-muted, #71809c);
  font: 9.5px/1.45 var(--mono, monospace);
  overflow-wrap: anywhere;
}

.ta__clues {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.ta__clue {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 5px;
  background: var(--code-bg, #f1f4f8);
  font-size: 10px;
  line-height: 1.4;
  color: var(--text, #3f5274);
}

.ta__clue b { font-size: 10px; flex-shrink: 0; }
.ta__clue em { color: var(--text-muted); font: 9px/1 var(--mono); font-style: normal; }

.ta__hypotheses {
  display: grid;
  gap: 5px;
}

.ta__hypo {
  display: grid;
  gap: 3px;
  padding: 7px 9px;
  border-radius: 6px;
  border-left: 3px solid var(--text-muted);
  background: var(--code-bg, #f1f4f8);
}

.ta__hypo.is-certain { border-color: var(--ok, #11866f); background: rgba(17,134,111,.04); }
.ta__hypo.is-uncertain { border-color: var(--warn, #c48b00); background: rgba(196,139,0,.04); }
.ta__hypo.is-guess { border-color: var(--text-muted); }

.ta__hypo-certainty {
  color: var(--text-muted);
  font: 700 8px/1 var(--mono);
  text-transform: uppercase;
}

.ta__hypo.is-certain .ta__hypo-certainty { color: var(--ok); }
.ta__hypo.is-uncertain .ta__hypo-certainty { color: var(--warn); }

.ta__hypo p {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
}

.ta__unknowns {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 5px;
}

.ta__unknowns li {
  display: flex;
  align-items: baseline;
  gap: 5px;
  font-size: 11px;
  line-height: 1.45;
  color: var(--text, #3f5274);
  padding: 5px 8px;
  border-radius: 5px;
  background: var(--code-bg, #f1f4f8);
}

.ta__unknowns li.is-blocking { background: rgba(196,139,0,.06); border-left: 2px solid var(--warn); }

.ta__unknowns li b { color: var(--warn); font: 700 8px/1 var(--mono); flex-shrink: 0; }
.ta__unknowns li em {
  margin-left: auto;
  color: var(--text-muted);
  font: 8px/1 var(--mono);
  font-style: normal;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(0,0,0,.04);
}

.ta__behaviors,
.ta__scopes {
  display: grid;
  gap: 5px;
  padding-top: 2px;
}

.ta__behavior,
.ta__scope {
  display: grid;
  gap: 2px;
  padding: 6px 8px;
  border-radius: 5px;
  border-left: 3px solid var(--border);
  background: var(--code-bg, #f1f4f8);
}

.ta__behavior.is-input { border-color: #3b5998; }
.ta__behavior.is-output { border-color: var(--ok); }
.ta__behavior.is-success { border-color: var(--ok); }
.ta__behavior.is-failure { border-color: var(--err); }
.ta__behavior.is-boundary { border-color: var(--warn); }

.ta__scope.is-in { border-color: var(--ok); }
.ta__scope.is-out { border-color: var(--err); }
.ta__scope.is-undecided { border-color: var(--warn); }

.ta__behavior span,
.ta__scope span {
  color: var(--text-muted);
  font: 700 8px/1 var(--mono);
  text-transform: uppercase;
}

.ta__behavior p,
.ta__scope p {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 10.5px;
  line-height: 1.45;
}

.ta-expand-enter-active,
.ta-expand-leave-active {
  overflow: hidden;
}
</style>
