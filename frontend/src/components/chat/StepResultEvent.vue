<script setup>
import { computed } from 'vue'

const props = defineProps({ event: { type: Object, required: true } })

const stepLabel = computed(() => ({
  write_code: 'Ready for design',
  continue_investigation: 'More investigation needed',
  ask_user: 'Needs your input',
  failed: 'Investigation stalled',
})[props.event.next_step] || props.event.next_step)

const stepColor = computed(() => ({
  write_code: 'var(--ok, #11866f)',
  continue_investigation: 'var(--warn, #c48b00)',
  ask_user: 'var(--accent, #1756d1)',
  failed: 'var(--err, #c44747)',
})[props.event.next_step] || 'var(--text-muted)')

const beliefs = computed(() => props.event.beliefs || [])
const patchFacts = computed(() => props.event.patch_planning_context || [])
const resolutions = computed(() => props.event.resolutions || [])
const unknowns = computed(() => (props.event.unknowns || []).filter(u => u.blocking))
const hasBeliefs = computed(() => beliefs.value.length > 0)
const hasPatchContext = computed(() => patchFacts.value.length > 0)
const hasResolutions = computed(() => resolutions.value.length > 0)
const hasBlockers = computed(() => unknowns.value.length > 0)

function beliefLabel(status) {
  return {
    unverified: 'Idea',
    plausible: 'Plausible',
    supported: 'Supported',
    strongly_supported: 'Confirmed',
    runtime_confirmed: 'Runtime verified',
    contradicted: 'Contradicted',
    invalidated: 'Invalidated',
  }[status] || status
}

function beliefColor(status) {
  return {
    unverified: 'var(--text-muted)',
    plausible: 'var(--accent-text)',
    supported: 'var(--ok)',
    strongly_supported: 'var(--ok)',
    runtime_confirmed: 'var(--ok)',
    contradicted: 'var(--warn)',
    invalidated: 'var(--err)',
  }[status] || 'var(--text-muted)'
}
</script>

<template>
  <div class="sr" :style="{ '--sr-accent': stepColor }">
    <div class="sr__banner">
      <span class="sr__banner-icon">
        <template v-if="event.next_step === 'write_code'">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 6 9 17l-5-5"/></svg>
        </template>
        <template v-else-if="event.next_step === 'continue_investigation'">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        </template>
        <template v-else-if="event.next_step === 'ask_user'">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        </template>
        <template v-else>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        </template>
      </span>
      <div class="sr__banner-body">
        <strong>{{ stepLabel }}</strong>
        <p>{{ event.continue_reason }}</p>
      </div>
    </div>

    <div v-if="event.summary" class="sr__summary">
      {{ event.summary }}
    </div>

    <div v-if="hasBeliefs" class="sr__section">
      <h4>Key findings</h4>
      <ul class="sr__beliefs">
        <li v-for="belief in beliefs.slice(0, 6)" :key="belief.statement" :style="{ '--b-color': beliefColor(belief.status) }">
          <i></i>
          <span>
            <b>{{ beliefLabel(belief.status) }}</b>
            {{ belief.statement }}
          </span>
        </li>
      </ul>
    </div>

    <div v-if="hasResolutions" class="sr__section">
      <h4>Resolved</h4>
      <div class="sr__resolutions">
        <span v-for="res in resolutions.slice(0, 8)" :key="res.id">{{ res.unknown_id || res.id }}</span>
      </div>
    </div>

    <div v-if="hasPatchContext" class="sr__section">
      <h4>Patch context</h4>
      <ul class="sr__facts">
        <li v-for="fact in patchFacts.slice(0, 6)" :key="fact">{{ fact }}</li>
      </ul>
    </div>

    <div v-if="hasBlockers" class="sr__section">
      <h4>Blockers</h4>
      <div class="sr__blockers">
        <div v-for="u in unknowns.slice(0, 4)" :key="u.id" class="sr__blocker">
          <span>{{ u.resolution_strategy === 'ask_user' ? '?' : '→' }}</span>
          {{ u.question }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sr {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid color-mix(in srgb, var(--sr-accent) 30%, #d4e0f2);
  border-radius: 10px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--sr-accent) 4%, transparent), transparent);
}

.sr__banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.sr__banner-icon {
  display: grid;
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  place-items: center;
  border-radius: 8px;
  color: var(--sr-accent);
  background: color-mix(in srgb, var(--sr-accent) 10%, transparent);
}

.sr__banner-body {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.sr__banner-body strong {
  color: var(--sr-accent);
  font-size: 13px;
  font-weight: 650;
  line-height: 1.3;
}

.sr__banner-body p {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
}

.sr__summary {
  color: var(--text, #3f5274);
  font-size: 11.5px;
  line-height: 1.55;
  padding: 8px 10px;
  border-radius: 7px;
  background: var(--code-bg, #f1f4f8);
}

.sr__section {
  display: grid;
  gap: 6px;
}

.sr__section h4 {
  margin: 0;
  color: var(--text-muted, #71809c);
  font: 700 8.5px/1 var(--mono);
  text-transform: uppercase;
  letter-spacing: .04em;
}

.sr__beliefs {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.sr__beliefs li {
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-size: 10.5px;
  line-height: 1.45;
  color: var(--text, #3f5274);
}

.sr__beliefs li i {
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  margin-top: 4px;
  border-radius: 3px;
  background: var(--b-color);
}

.sr__beliefs li b {
  font: 650 8px/1 var(--mono);
  text-transform: uppercase;
  margin-right: 3px;
  color: var(--b-color);
}

.sr__resolutions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.sr__resolutions span {
  padding: 3px 7px;
  border-radius: 5px;
  color: var(--ok, #11866f);
  background: var(--ok-bg, #dff8f1);
  font: 600 9px/1 var(--mono);
}

.sr__facts {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 4px;
}

.sr__facts li {
  color: var(--text, #3f5274);
  font-size: 10.5px;
  line-height: 1.45;
  padding-left: 10px;
  position: relative;
}

.sr__facts li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 6px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--sr-accent);
}

.sr__blockers {
  display: grid;
  gap: 5px;
}

.sr__blocker {
  display: flex;
  align-items: baseline;
  gap: 6px;
  color: var(--text, #3f5274);
  font-size: 10.5px;
  line-height: 1.4;
}

.sr__blocker span {
  flex-shrink: 0;
  color: var(--warn, #c48b00);
  font: 700 10px/1 var(--mono);
}
</style>
