<script setup>
import { onMounted, ref } from 'vue'
import { gsap } from 'gsap'
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })

function list(value) {
  return Array.isArray(value) ? value : []
}

const rootRef = ref(null)
const showSteps = ref({})

function toggleStep(id) {
  showSteps.value[id] = !showSteps.value[id]
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  const el = rootRef.value
  if (!el) return
  gsap.fromTo(el.querySelectorAll('.pp__section'), { y: 10, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.35, stagger: 0.06, ease: 'power2.out' })
})
</script>

<template>
  <EventFrame kind="patch-plan" symbol="P" label="Patch planning" :detail="event.summary" status="done" state="done" open>
    <div ref="rootRef" class="pp">
      <p v-if="event.summary" class="pp__summary">{{ event.summary }}</p>

      <section v-if="list(event.files_to_change).length" class="pp__section">
        <div class="pp__section-head">
          <span class="pp__chip pp__chip--files">Files</span>
          <span class="pp__count">{{ event.files_to_change.length }}</span>
        </div>
        <div class="pp__files">
          <code v-for="file in event.files_to_change" :key="file" class="pp__file-chip">{{ file }}</code>
        </div>
      </section>

      <section v-if="list(event.implementation_steps).length" class="pp__section">
        <div class="pp__section-head">
          <span class="pp__chip pp__chip--steps">Implementation steps</span>
          <span class="pp__count">{{ event.implementation_steps.length }}</span>
        </div>
        <div class="pp__steps">
          <div v-for="step in event.implementation_steps" :key="step.id || `${step.file}:${step.action}`" class="pp__step">
            <div class="pp__step-head">
              <span class="pp__step-id">{{ step.id }}</span>
              <code v-if="step.file" class="pp__step-file">{{ step.file }}</code>
              <b v-if="step.target" class="pp__step-target">{{ step.target }}</b>
            </div>
            <p class="pp__step-action">{{ step.action }}</p>
            <div v-if="list(step.because).length" class="pp__step-details">
              <button class="pp__detail-toggle" @click="toggleStep(step.id || step.file + step.action)">
                reasoning
                <span :class="{ 'is-open': showSteps[step.id || step.file + step.action] }">&#9660;</span>
              </button>
              <div v-if="showSteps[step.id || step.file + step.action]" class="pp__detail-body">
                <div class="pp__trail">
                  <span v-for="(r, i) in step.because" :key="i" class="pp__trail-step">
                    {{ r }}
                    <span v-if="i < step.because.length - 1" class="pp__trail-arrow">&#8594;</span>
                  </span>
                </div>
                <em v-if="step.required_behavior_if_removed" class="pp__note pp__note--warn">
                  &#9888; remove breaks: {{ step.required_behavior_if_removed }}
                </em>
                <em v-if="step.minimality_check" class="pp__note">
                  minimality: {{ step.minimality_check }}
                </em>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section v-if="list(event.responsibility_chain).length" class="pp__section">
        <div class="pp__section-head">
          <span class="pp__chip pp__chip--chain">Responsibility chain</span>
          <span class="pp__count">{{ event.responsibility_chain.length }}</span>
        </div>
        <div class="pp__chains">
          <div v-for="item in event.responsibility_chain" :key="item.step_id || item.removal_breaks" class="pp__chain">
            <div class="pp__chain-row">
              <span class="pp__chain-step">{{ item.step_id }}</span>
              <span class="pp__chain-arrow">&#8594;</span>
              <span v-if="list(item.requirement_ids).length" class="pp__pill-group">
                <span class="pp__pill-label">req</span>
                <span v-for="rid in item.requirement_ids" :key="rid" class="pp__pill pp__pill--req">{{ rid }}</span>
              </span>
              <span v-if="list(item.decision_ids).length" class="pp__pill-group">
                <span class="pp__pill-label">dec</span>
                <span v-for="did in item.decision_ids" :key="did" class="pp__pill pp__pill--dec">{{ did }}</span>
              </span>
            </div>
            <div v-if="list(item.project_facts).length" class="pp__trail">
              <span v-for="(f, i) in item.project_facts" :key="i" class="pp__trail-step">
                {{ f }}
                <span v-if="i < item.project_facts.length - 1" class="pp__trail-arrow">&#8594;</span>
              </span>
            </div>
            <em v-if="item.removal_breaks" class="pp__note pp__note--warn">
              &#9888; remove breaks: {{ item.removal_breaks }}
            </em>
          </div>
        </div>
      </section>

      <section v-if="list(event.acceptance_mapping).length" class="pp__section">
        <div class="pp__section-head">
          <span class="pp__chip pp__chip--acceptance">Acceptance mapping</span>
          <span class="pp__count">{{ event.acceptance_mapping.length }}</span>
        </div>
        <div class="pp__acceptances">
          <div v-for="item in event.acceptance_mapping" :key="item.acceptance_id || item.verification" class="pp__acc">
            <div class="pp__acc-head">
              <span class="pp__id-badge">{{ item.acceptance_id }}</span>
              <span v-if="list(item.covered_by).length" class="pp__pill-group">
                <span class="pp__pill pp__pill--covered">{{ item.covered_by.join(', ') }}</span>
              </span>
            </div>
            <p>{{ item.verification }}</p>
          </div>
        </div>
      </section>

      <section v-if="list(event.tests_or_checks).length" class="pp__section">
        <div class="pp__section-head">
          <span class="pp__chip pp__chip--checks">Checks</span>
          <span class="pp__count">{{ event.tests_or_checks.length }}</span>
        </div>
        <ul class="pp__checklist">
          <li v-for="item in event.tests_or_checks" :key="item">
            <span class="pp__check-mark">&#10003;</span>
            {{ item }}
          </li>
        </ul>
      </section>

      <section v-if="list(event.risks).length" class="pp__section">
        <div class="pp__section-head">
          <span class="pp__chip pp__chip--risks">Risks</span>
          <span class="pp__count">{{ event.risks.length }}</span>
        </div>
        <ul class="pp__risk-list">
          <li v-for="item in event.risks" :key="item">
            <span class="pp__risk-icon">&#9888;</span>
            {{ item }}
          </li>
        </ul>
      </section>

      <section v-if="list(event.out_of_scope).length" class="pp__section pp__section--muted">
        <div class="pp__section-head">
          <span class="pp__chip pp__chip--scope">Out of scope</span>
          <span class="pp__count">{{ event.out_of_scope.length }}</span>
        </div>
        <ul class="pp__scope-list">
          <li v-for="item in event.out_of_scope" :key="item">{{ item }}</li>
        </ul>
      </section>
    </div>
  </EventFrame>
</template>

<style scoped>
.pp {
  display: grid;
  gap: 8px;
}

.pp__summary {
  margin: 0;
  padding: 8px 10px;
  border-radius: 7px;
  color: var(--text-h, #102a5c);
  background: color-mix(in srgb, var(--event, #6658c7) 5%, transparent);
  font-weight: 580;
  font-size: 12px;
  line-height: 1.55;
}

.pp__section {
  display: grid;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 8px;
  background: var(--bg-raised, #ffffff);
}

.pp__section--muted {
  background: var(--code-bg, #f1f4f8);
  border-style: dashed;
}

.pp__section-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.pp__chip {
  padding: 2px 7px;
  border-radius: 4px;
  font: 700 8px/1 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .04em;
}

.pp__chip--files    { color: var(--accent-text, #1748a3); background: var(--accent-bg, rgba(23,86,209,.08)); }
.pp__chip--steps    { color: var(--event, #6658c7); background: rgba(102,88,199,.08); }
.pp__chip--chain    { color: #7b2d8e; background: rgba(123,45,142,.08); }
.pp__chip--acceptance { color: var(--ok, #11866f); background: rgba(17,134,111,.08); }
.pp__chip--checks   { color: #3b5998; background: rgba(59,89,152,.08); }
.pp__chip--risks    { color: var(--warn, #c48b00); background: rgba(196,139,0,.08); }
.pp__chip--scope    { color: var(--text-muted, #71809c); background: rgba(113,128,156,.08); }

.pp__count {
  margin-left: auto;
  color: var(--text-muted, #71809c);
  font: 9px/1 var(--mono, monospace);
}

/* Files */
.pp__files {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.pp__file-chip {
  padding: 3px 8px;
  border-radius: 5px;
  color: var(--accent-text, #1748a3);
  background: var(--accent-bg, rgba(23,86,209,.07));
  border: 1px solid color-mix(in srgb, var(--accent-text, #1748a3) 16%, #d4e0f2);
  font: 10px/1.5 var(--mono, monospace);
}

/* Implementation steps */
.pp__steps {
  display: grid;
  gap: 5px;
}

.pp__step {
  display: grid;
  gap: 4px;
  padding: 7px 9px;
  border-radius: 6px;
  border-left: 3px solid var(--event, #6658c7);
  background: var(--code-bg, #f1f4f8);
}

.pp__step-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.pp__step-id {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 4px;
  color: #fff;
  background: var(--event, #6658c7);
  font: 700 8px/1.3 var(--mono, monospace);
  text-transform: uppercase;
}

.pp__step-file {
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--accent-text, #1748a3);
  background: var(--accent-bg, rgba(23,86,209,.07));
  font: 9px/1.4 var(--mono, monospace);
}

.pp__step-target {
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--ok, #11866f);
  background: rgba(17,134,111,.08);
  font: 8px/1 var(--mono, monospace);
  text-transform: uppercase;
}

.pp__step-action {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
}

.pp__step-details {
  display: grid;
  gap: 4px;
}

.pp__detail-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 4px;
  color: var(--text-muted);
  background: transparent;
  cursor: pointer;
  font: 700 8px/1 var(--mono, monospace);
  text-transform: uppercase;
  width: fit-content;
}

.pp__detail-toggle span {
  font-size: 7px;
  transition: transform .2s ease;
}

.pp__detail-toggle span.is-open {
  transform: rotate(180deg);
}

.pp__detail-body {
  display: grid;
  gap: 4px;
  padding: 5px 7px 5px 9px;
  border-left: 2px solid var(--border, #d9e3f5);
  margin-left: 1px;
}

/* Reasoning trail */
.pp__trail {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}

.pp__trail-step {
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--accent-text, #1748a3);
  background: var(--accent-bg, rgba(23,86,209,.07));
  font: 9px/1.4 var(--mono, monospace);
}

.pp__trail-arrow {
  color: var(--text-muted);
  font-size: 8px;
  margin: 0 1px;
}

.pp__note {
  display: block;
  padding: 3px 6px;
  border-radius: 4px;
  color: var(--text-muted);
  background: rgba(0,0,0,.03);
  font: 8.5px/1.35 var(--mono, monospace);
  font-style: normal;
}

.pp__note--warn {
  color: #8a5b00;
  background: rgba(196,139,0,.06);
}

/* Responsibility chain */
.pp__chains {
  display: grid;
  gap: 5px;
}

.pp__chain {
  display: grid;
  gap: 4px;
  padding: 7px 9px;
  border-radius: 6px;
  border-left: 3px solid #7b2d8e;
  background: rgba(123,45,142,.03);
}

.pp__chain-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.pp__chain-step {
  padding: 1px 6px;
  border-radius: 4px;
  color: #fff;
  background: #7b2d8e;
  font: 700 8px/1.3 var(--mono, monospace);
}

.pp__chain-arrow {
  color: var(--text-muted);
  font-size: 10px;
}

.pp__pill-group {
  display: flex;
  align-items: center;
  gap: 3px;
}

.pp__pill-label {
  color: var(--text-muted);
  font: 700 7px/1 var(--mono, monospace);
  text-transform: uppercase;
}

.pp__pill {
  padding: 1px 6px;
  border-radius: 4px;
  font: 8px/1.3 var(--mono, monospace);
}

.pp__pill--req { color: #3b5998; background: rgba(59,89,152,.09); }
.pp__pill--dec { color: var(--ok, #11866f); background: rgba(17,134,111,.09); }
.pp__pill--covered { color: var(--accent-text, #1748a3); background: var(--accent-bg, rgba(23,86,209,.07)); }

/* Acceptance mapping */
.pp__acceptances {
  display: grid;
  gap: 5px;
}

.pp__acc {
  display: grid;
  gap: 4px;
  padding: 7px 9px;
  border-radius: 6px;
  border-left: 3px solid var(--ok, #11866f);
  background: rgba(17,134,111,.03);
}

.pp__acc-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.pp__id-badge {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 4px;
  color: #fff;
  background: var(--ok, #11866f);
  font: 700 8px/1.3 var(--mono, monospace);
  text-transform: uppercase;
}

.pp__acc p {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
}

/* Checklist */
.pp__checklist {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 4px;
}

.pp__checklist li {
  display: flex;
  align-items: baseline;
  gap: 6px;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
  padding: 4px 8px;
  border-radius: 5px;
  background: var(--code-bg, #f1f4f8);
}

.pp__check-mark {
  flex-shrink: 0;
  color: var(--ok, #11866f);
  font-weight: 700;
  font-size: 11px;
}

/* Risks */
.pp__risk-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 4px;
}

.pp__risk-list li {
  display: flex;
  align-items: baseline;
  gap: 5px;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
  padding: 5px 8px;
  border-radius: 5px;
  border-left: 2px solid var(--warn, #c48b00);
  background: rgba(196,139,0,.04);
}

.pp__risk-icon {
  flex-shrink: 0;
  color: var(--warn, #c48b00);
  font-size: 11px;
}

/* Out of scope */
.pp__scope-list {
  margin: 0;
  padding: 0 0 0 14px;
  list-style: none;
  display: grid;
  gap: 3px;
}

.pp__scope-list li {
  color: var(--text-muted, #71809c);
  font-size: 10.5px;
  line-height: 1.5;
  position: relative;
}

.pp__scope-list li::before {
  content: '';
  position: absolute;
  left: -11px;
  top: 5px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--text-muted);
  opacity: .5;
}
</style>
