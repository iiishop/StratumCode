<script setup>
import { computed, ref } from 'vue'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const verdict = computed(() => props.event.verdict || 'inconclusive')
const issues = computed(() => props.event.issues || [])
const summary = computed(() => props.event.summary || '')
const expanded = ref(false)

const verdictStyle = computed(() => {
  if (verdict.value === 'passed') return { symbol: '', label: 'Passed', cls: 'is-passed' }
  if (verdict.value === 'inconclusive') return { symbol: '', label: 'Inconclusive', cls: 'is-inconclusive' }
  return { symbol: '', label: verdict.value, cls: 'is-failed' }
})

function severityClass(sev) {
  const s = (sev || '').toLowerCase()
  if (s === 'high' || s === 'critical') return 'severity--high'
  if (s === 'medium') return 'severity--medium'
  return 'severity--low'
}
</script>

<template>
  <EventFrame
    kind="stage"
    symbol="V"
    :label="`Validation ${verdictStyle.label}`"
    :detail="issues.length ? `${issues.length} issue${issues.length > 1 ? 's' : ''}` : ''"
    :state="verdict === 'passed' ? 'done' : verdict === 'inconclusive' ? 'running' : 'error'"
    open
  >
    <p v-if="summary" class="vr__summary">{{ summary }}</p>

    <div v-if="issues.length" class="vr__issues">
      <button class="vr__toggle" type="button" @click="expanded = !expanded">
        <span>{{ expanded ? 'Collapse' : 'Show' }} {{ issues.length }} issue{{ issues.length > 1 ? 's' : '' }}</span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" :class="{ 'is-open': expanded }">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      <Transition name="issues-slide">
        <div v-if="expanded" class="vr__issues-list">
          <div
            v-for="(issue, idx) in issues"
            :key="idx"
            class="vr__issue"
            :class="severityClass(issue.severity)"
          >
            <div class="vr__issue-head">
              <span class="vr__issue-severity">{{ issue.severity || 'issue' }}</span>
              <span v-if="issue.step_id" class="vr__issue-patch">{{ issue.step_id }}{{ issue.patch_id ? ` / ${issue.patch_id}` : '' }}</span>
              <span v-if="issue.file" class="vr__issue-loc">{{ issue.file }}<template v-if="issue.line">:{{ issue.line }}</template></span>
            </div>
            <p class="vr__issue-body">{{ issue.summary }}</p>
            <ul v-if="issue.evidence?.length" class="vr__issue-evidence">
              <li v-for="(ev, ei) in issue.evidence" :key="ei" class="vr__evidence-item">{{ ev }}</li>
            </ul>
          </div>
        </div>
      </Transition>
    </div>

    <div v-else class="vr__clean">
      No issues reported by validator.
    </div>
  </EventFrame>
</template>

<style scoped>
.vr__summary {
  margin: 0 0 10px;
  padding: 10px 12px;
  border-left: 3px solid var(--event, #1756d1);
  border-radius: 0 8px 8px 0;
  background: color-mix(in srgb, var(--event, #1756d1) 4%, #f9fbfe);
  color: var(--text, #3f5274);
  font-size: 12px;
  line-height: 1.6;
}

.vr__clean {
  color: var(--text-muted, #71809c);
  font-size: 11px;
  font-style: italic;
  padding: 6px 0;
}

.vr__issues {
  margin-top: 4px;
}

.vr__toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid #d4dbe8;
  border-radius: 6px;
  color: var(--text-muted, #71809c);
  background: #f9fbfe;
  font: 10px/1 var(--mono, monospace);
  cursor: pointer;
  transition: border-color 120ms, color 120ms;
}

.vr__toggle:hover {
  border-color: #b9c7dc;
  color: var(--text-h, #102a5c);
}

.vr__toggle svg {
  transition: transform 180ms;
}

.vr__toggle svg.is-open {
  transform: rotate(180deg);
}

.vr__issues-list {
  display: grid;
  gap: 8px;
  margin-top: 8px;
}

.vr__issue {
  padding: 10px 12px;
  border: 1px solid #e4ecf8;
  border-radius: 8px;
  background: #fafcff;
}

.severity--high {
  border-left: 3px solid #c44747;
}

.severity--medium {
  border-left: 3px solid #c48b00;
}

.severity--low {
  border-left: 3px solid #71809c;
}

.vr__issue-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.vr__issue-severity {
  padding: 1px 6px;
  border-radius: 4px;
  color: #fff;
  font: 650 8.5px/1 var(--mono, monospace);
  text-transform: uppercase;
  background: #c44747;
}

.severity--medium .vr__issue-severity { background: #c48b00; }
.severity--low .vr__issue-severity { background: #71809c; }

.vr__issue-patch {
  color: var(--text-muted, #71809c);
  font: 9px/1 var(--mono, monospace);
}

.vr__issue-loc {
  margin-left: auto;
  color: var(--text-muted, #71809c);
  font: 9px/1 var(--mono, monospace);
}

.vr__issue-body {
  margin: 0;
  color: var(--text-h, #102a5c);
  font-size: 11.5px;
  line-height: 1.5;
}

.vr__issue-evidence {
  display: grid;
  gap: 3px;
  margin: 6px 0 0;
  padding: 0;
  list-style: none;
}

.vr__evidence-item {
  padding: 4px 8px;
  border-radius: 4px;
  background: rgba(248, 250, 253, 0.8);
  color: var(--text-muted, #71809c);
  font: 9.5px/1.4 var(--mono, monospace);
}

.vr__evidence-item::before {
  content: "";
  display: inline-block;
  width: 5px;
  height: 5px;
  margin-right: 6px;
  border-radius: 50%;
  background: #b9c7dc;
  vertical-align: middle;
}

/* transition */
.issues-slide-enter-active,
.issues-slide-leave-active {
  transition: all 200ms ease;
}

.issues-slide-enter-from,
.issues-slide-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}
</style>
