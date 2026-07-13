<script setup>
import { computed, inject, onMounted, onUnmounted, ref } from 'vue'
import { animate, createTimeline } from 'animejs'
import { gsap } from 'gsap'
import { highlightCode } from '../../lib/highlight'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })
const emit = defineEmits(['rollback'])

const messageEvents = inject('messageEvents', computed(() => []))

const matchingStep = computed(() => {
  const sid = props.event.metadata?.step_id || null
  if (!sid) {
    try { const p = JSON.parse(props.event.output || '{}'); if (p.step_id) return { id: p.step_id } } catch {}
    return null
  }
  for (const ev of messageEvents.value) {
    if (ev.type !== 'patch_plan' || !ev.data?.implementation_steps) continue
    const step = ev.data.implementation_steps.find(s => s.id === sid)
    if (step) return step
  }
  return { id: sid }
})

const rootRef = ref(null)
const rollbackConfirm = ref(false)
const rolling = ref(false)
let rollbackTimeline

const payload = computed(() => {
  try {
    return JSON.parse(props.event.output || '{}')
  } catch {
    return {}
  }
})

const files = computed(() => payload.value.files || payload.value.items?.[0]?.files || [])
const diff = computed(() => payload.value.diff || '')
const patchId = computed(() => payload.value.patch_id || props.event.metadata?.patch_id || '')
const rollbackId = computed(() => payload.value.rollback_id || props.event.metadata?.rollback_id || '')
const status = computed(() => props.event.status || payload.value.status || 'running')
const frameState = computed(() => status.value === 'error' ? 'error' : status.value === 'running' ? 'running' : 'done')
const frameLabel = computed(() => status.value === 'error' ? 'Patch failed' : status.value === 'running' ? 'Applying patch' : 'Patch applied')
const stepId = computed(() => props.event.metadata?.step_id || payload.value.step_id || '')
const authId = computed(() => props.event.metadata?.authorization_id || payload.value.authorization_id || '')
const attemptId = computed(() => props.event.metadata?.attempt_id || payload.value.attempt_id || '')
const purpose = computed(() => props.event.metadata?.purpose || payload.value.purpose || matchingStep.value?.purpose || '')
const operationSummary = computed(() => props.event.metadata?.operation_summary || payload.value.operation_summary || '')
const isState = computed(() => props.event.metadata?.is_state || payload.value.is_state || '')

const detail = computed(() => {
  const bits = [patchId.value, stepId.value, attemptId.value, isState.value, files.value.length ? `${files.value.length} files` : ''].filter(Boolean)
  return bits.join(' · ') || props.event.name
})

const addCount = computed(() => {
  if (!diff.value) return 0
  return diff.value.split('\n').filter(line => line.startsWith('+') && !line.startsWith('+++')).length
})

const removeCount = computed(() => {
  if (!diff.value) return 0
  return diff.value.split('\n').filter(line => line.startsWith('-') && !line.startsWith('---')).length
})

const hasLspDiagnostics = computed(() => files.value.some(f => f.lsp?.diagnostics?.length))

const diffLines = computed(() => {
  if (!diff.value) return []
  const lines = diff.value.split('\n')
  const fileExt = files.value[0]?.path || 'diff'
  return lines.map(line => {
    let prefix, content, type
    if (line.startsWith('@@')) {
      prefix = ''; content = line; type = 'hunk'
    } else if (line.startsWith('--- ')) {
      prefix = ''; content = line; type = 'from-file'
    } else if (line.startsWith('+++ ')) {
      prefix = ''; content = line; type = 'to-file'
    } else if (line.startsWith('+')) {
      prefix = '+'; content = line.slice(1); type = 'add'
    } else if (line.startsWith('-')) {
      prefix = '-'; content = line.slice(1); type = 'remove'
    } else {
      prefix = ' '; content = /^ /.test(line) ? line.slice(1) : line; type = 'keep'
    }
    const html = (type === 'add' || type === 'remove' || type === 'keep')
      ? highlightCode(content, fileExt)
      : content.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c])
    return { type, prefix, html }
  })
})

function rollback() {
  if (rollbackConfirm.value) {
    rolling.value = true
    emit('rollback', { rollback_id: rollbackId.value, patch_id: patchId.value, step_id: stepId.value })
    return
  }
  rollbackConfirm.value = true
  setTimeout(() => { if (rollbackConfirm.value) rollbackConfirm.value = false }, 4000)
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  const el = rootRef.value
  if (!el) return
  gsap.fromTo(el.querySelectorAll('.pe__section'), { y: 8, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.32, stagger: 0.05, ease: 'power2.out' })
})

onUnmounted(() => rollbackTimeline?.revert())
</script>

<template>
  <EventFrame
    kind="diff"
    :symbol="status === 'running' ? '◌' : status === 'error' ? '!' : '✓'"
    :label="frameLabel"
    :detail="detail"
    :status="status"
    :state="frameState"
    :open="event.open"
    collapsible
    @toggle="event.open = !event.open"
  >
    <div ref="rootRef" class="pe">
      <!-- rollback button -->
      <div v-if="rollbackId && status !== 'running'" class="pe__rollback-bar">
        <button
          class="pe__rollback-btn"
          :class="{ 'is-confirming': rollbackConfirm, 'is-rolling': rolling }"
          :disabled="rolling"
          @click="rollback"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 102.13-9.36L1 10"/></svg>
          <span v-if="!rollbackConfirm && !rolling">Rollback patch</span>
          <span v-else-if="rollbackConfirm && !rolling">Click again to confirm rollback</span>
          <span v-else>Rolling back...</span>
        </button>
      </div>

      <!-- step context -->
      <div v-if="matchingStep" class="pe__section pe__step-context">
        <div class="pe__section-head">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          <span>From step</span>
          <span class="pe__step-id-badge">{{ matchingStep.id }}</span>
        </div>
        <p v-if="purpose" class="pe__step-purpose">{{ purpose }}</p>
        <p v-if="operationSummary" class="pe__step-operation">{{ operationSummary }}</p>
        <p v-if="matchingStep.action" class="pe__step-action">{{ matchingStep.action }}</p>
        <div v-if="matchingStep.file || matchingStep.target" class="pe__step-tags">
          <code v-if="matchingStep.file" class="pe__step-tag pe__step-tag--file">{{ matchingStep.file }}</code>
          <b v-if="matchingStep.target" class="pe__step-tag pe__step-tag--target">{{ matchingStep.target }}</b>
          <span v-if="attemptId" class="pe__step-tag">{{ attemptId }}</span>
          <span v-if="isState" class="pe__step-tag">{{ isState }}</span>
        </div>
      </div>

      <!-- diff stats -->
      <div v-if="diff" class="pe__section pe__stats">
        <span class="pe__stat pe__stat--add">+{{ addCount }}</span>
        <span class="pe__stat pe__stat--remove">−{{ removeCount }}</span>
        <span v-if="files.length" class="pe__stat pe__stat--files">{{ files.length }} files</span>
      </div>

      <!-- files -->
      <section v-if="files.length" class="pe__section">
        <div class="pe__section-head">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          <span>Files changed</span>
          <span class="pe__count">{{ files.length }}</span>
        </div>
        <div class="pe__files">
          <div v-for="file in files" :key="file.path || file.rollback_id" class="pe__file">
            <div class="pe__file-main">
              <code class="pe__file-path">{{ file.path || file.rollback_id }}</code>
              <span v-if="file.mode" class="pe__file-mode">{{ file.mode }}</span>
              <span v-if="file.operations_applied" class="pe__file-ops">{{ file.operations_applied }} ops</span>
              <span v-if="file.status" class="pe__file-status" :class="{ 'is-error': file.status === 'error' }">{{ file.status }}</span>
              <span v-if="file.lsp" class="pe__file-lsp" :class="{ 'has-errors': file.lsp.diagnostic_count }">
                LSP {{ file.lsp.diagnostic_count || 0 }}
              </span>
            </div>
            <small v-if="file.before_hash && file.after_hash" class="pe__file-hash">
              <code>{{ file.before_hash.slice(0, 12) }}</code>
              <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M5 12h14M13 5l7 7-7 7"/></svg>
              <code>{{ file.after_hash.slice(0, 12) }}</code>
            </small>
          </div>
        </div>
      </section>

      <!-- LSP diagnostics -->
      <section v-if="hasLspDiagnostics" class="pe__section pe__section--diagnostics">
        <div class="pe__section-head">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          <span>LSP diagnostics</span>
        </div>
        <div v-for="file in files.filter(f => f.lsp?.diagnostics?.length)" :key="`${file.path}:lsp`" class="pe__diag-file">
          <b>{{ file.path }}</b>
          <ul>
            <li v-for="(d, i) in file.lsp.diagnostics" :key="i">
              <span class="pe__diag-line">L{{ (d.range?.start?.line ?? 0) + 1 }}</span>
              {{ d.message }}
            </li>
          </ul>
        </div>
      </section>

      <!-- diff -->
      <section v-if="diff" class="pe__section pe__section--diff">
        <div class="pe__section-head">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>
          <span>Diff</span>
        </div>
        <div class="pe__diff">
          <div
            v-for="(line, i) in diffLines"
            :key="i"
            class="pe__diff-line"
            :class="`pe__diff-line--${line.type}`"
          ><span class="pe__diff-marker">{{ line.prefix }}</span><span v-html="line.html"></span></div>
        </div>
      </section>

      <!-- no diff fallback -->
      <section v-else-if="event.output" class="pe__section">
        <div class="pe__section-head">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg>
          <span>Output</span>
        </div>
        <pre class="pe__raw"><code>{{ event.output }}</code></pre>
      </section>
    </div>
  </EventFrame>
</template>

<style scoped>
.pe {
  display: grid;
  gap: 8px;
}

/* ---- rollback ---- */
.pe__rollback-bar {
  display: flex;
  justify-content: flex-end;
}

.pe__rollback-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border: 1px solid #e0a300;
  border-radius: 7px;
  color: #8a5b00;
  background: rgba(196,139,0,.06);
  cursor: pointer;
  font: 650 9.5px/1 var(--mono, monospace);
  transition: all .18s ease;
}
.pe__rollback-btn:hover {
  border-color: #c48b00;
  background: rgba(196,139,0,.12);
  color: #6b4700;
}
.pe__rollback-btn.is-confirming {
  border-color: #c44747;
  color: #fff;
  background: #c44747;
  animation: pulse-confirm .6s ease-in-out infinite alternate;
}
.pe__rollback-btn.is-rolling {
  border-color: #c48b00;
  color: #8a5b00;
  background: rgba(196,139,0,.18);
  opacity: .7;
  cursor: wait;
}
@keyframes pulse-confirm {
  from { box-shadow: 0 0 0 0 rgba(196,71,71,.3); }
  to   { box-shadow: 0 0 0 8px rgba(196,71,71,0); }
}
@media (prefers-reduced-motion: reduce) {
  .pe__rollback-btn.is-confirming { animation: none; }
}

/* ---- sections ---- */
.pe__section {
  display: grid;
  gap: 6px;
  padding: 9px 11px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 7px;
  background: var(--bg-raised, #ffffff);
}

.pe__section--diagnostics {
  border-color: rgba(196,71,71,.18);
  background: rgba(196,71,71,.025);
}

.pe__section--diff {
  padding: 6px 0 0;
  border: 1px solid rgba(23,86,209,.1);
  background: transparent;
}

.pe__section-head {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0 0 0 2px;
  color: var(--text-muted);
  font: 700 8.5px/1 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .04em;
}
.pe__section--diff .pe__section-head { padding: 0 11px; }

.pe__count {
  margin-left: auto;
  color: var(--text-muted);
  font: 9px/1 var(--mono, monospace);
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
}

/* ---- step context ---- */
.pe__step-context {
  border-color: rgba(102,88,199,.18);
  background: linear-gradient(135deg, rgba(102,88,199,.04), rgba(102,88,199,.01));
}
.pe__step-id-badge {
  margin-left: auto;
  padding: 1px 6px;
  border-radius: 4px;
  color: #fff;
  background: #6658c7;
  font: 700 8px/1.3 var(--mono, monospace);
}
.pe__step-purpose,
.pe__step-operation,
.pe__step-action {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 10.5px;
  line-height: 1.5;
}
.pe__step-purpose {
  font-weight: 650;
}
.pe__step-operation {
  color: var(--muted, #687897);
}
.pe__step-tags {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.pe__step-tag {
  padding: 1px 6px;
  border-radius: 4px;
  font: 8px/1.3 var(--mono, monospace);
}
.pe__step-tag--file {
  color: var(--accent-text, #1748a3);
  background: var(--accent-bg, rgba(23,86,209,.07));
}
.pe__step-tag--target {
  color: var(--ok, #11866f);
  background: rgba(17,134,111,.08);
  text-transform: uppercase;
}

/* ---- stats ---- */
.pe__stats {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding: 6px 9px;
}

.pe__stat {
  font: 700 10px/1 var(--mono, monospace);
}
.pe__stat--add    { color: #00a878; }
.pe__stat--remove { color: #e11d74; }
.pe__stat--files  { color: var(--text-muted); font-weight: 500; }

/* ---- files ---- */
.pe__files {
  display: grid;
  gap: 4px;
}

.pe__file {
  display: grid;
  gap: 3px;
  padding: 7px 9px;
  border-radius: 6px;
  border: 1px solid rgba(23,86,209,.08);
  background: var(--code-bg, #f1f4f8);
}

.pe__file-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.pe__file-path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--text-h, #102a5c);
  font: 10.5px/1.4 var(--mono, monospace);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pe__file-mode {
  padding: 1px 5px;
  border-radius: 3px;
  color: #3b5998;
  background: rgba(59,89,152,.08);
  font: 650 8px/1 var(--mono, monospace);
  text-transform: uppercase;
}

.pe__file-ops {
  color: var(--text-muted);
  font: 8px/1 var(--mono, monospace);
}

.pe__file-status {
  padding: 1px 5px;
  border-radius: 3px;
  color: #11866f;
  background: rgba(17,134,111,.08);
  font: 650 8px/1 var(--mono, monospace);
}
.pe__file-status.is-error { color: #c44747; background: rgba(196,71,71,.08); }

.pe__file-lsp {
  padding: 1px 5px;
  border-radius: 3px;
  color: var(--text-muted);
  background: rgba(113,128,156,.06);
  font: 8px/1 var(--mono, monospace);
}
.pe__file-lsp.has-errors { color: #c44747; background: rgba(196,71,71,.07); }

.pe__file-hash {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--text-muted);
}
.pe__file-hash code {
  padding: 1px 4px;
  border-radius: 3px;
  color: var(--text-muted);
  background: rgba(0,0,0,.04);
  font: 8px/1.35 var(--mono, monospace);
}
.pe__file-hash svg { opacity: .4; }

/* ---- LSP diagnostics ---- */
.pe__diag-file {
  padding: 7px 9px;
  border-radius: 6px;
  background: rgba(196,71,71,.035);
}
.pe__diag-file b {
  display: block;
  margin-bottom: 5px;
  color: #8f2f2f;
  font: 600 9.5px/1.3 var(--mono, monospace);
}
.pe__diag-file ul {
  margin: 0;
  padding: 0 0 0 14px;
  list-style: none;
  display: grid;
  gap: 3px;
}
.pe__diag-file li {
  color: #6b3030;
  font: 9px/1.4 var(--mono, monospace);
  overflow-wrap: anywhere;
}
.pe__diag-line {
  display: inline-block;
  margin-right: 5px;
  padding: 0 4px;
  border-radius: 2px;
  color: #c44747;
  background: rgba(196,71,71,.1);
  font-weight: 700;
}

/* ---- diff ---- */
.pe__diff {
  margin: 0;
  overflow: auto;
  max-height: 420px;
  border-radius: 0 0 7px 7px;
  background: #f8fafd;
  font: 11px/1.55 var(--mono, monospace);
  color: #304863;
}
.pe__diff-line {
  display: block;
  padding: 0 12px;
  white-space: pre-wrap;
}
.pe__diff-line--add {
  background: linear-gradient(90deg, rgba(0,168,120,.1), rgba(0,168,120,.04));
}
.pe__diff-line--remove {
  background: linear-gradient(90deg, rgba(225,29,116,.09), rgba(225,29,116,.03));
}
.pe__diff-line--hunk {
  color: #4c3fc3;
  background: rgba(102,88,199,.06);
  font-weight: 650;
}
.pe__diff-line--from-file {
  margin-top: 6px;
  color: #7c8ba0;
  font-weight: 600;
}
.pe__diff-line--from-file:first-child { margin-top: 0; }
.pe__diff-line--to-file {
  color: #7c8ba0;
  font-weight: 600;
}
.pe__diff-marker {
  display: inline-block;
  width: 16px;
  color: #8291a5;
  user-select: none;
}

/* ---- raw ---- */
.pe__raw {
  margin: 0;
  padding: 9px 11px;
  overflow: auto;
  border-radius: 6px;
  color: var(--text);
  background: var(--code-bg, #f1f4f8);
  font: 10px/1.45 var(--mono, monospace);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 360px;
}
</style>
