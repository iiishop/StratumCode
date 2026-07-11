<script setup>
import { onMounted, ref } from 'vue'
import { gsap } from 'gsap'
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })

function list(value) {
  return Array.isArray(value) ? value : []
}

const rootRef = ref(null)

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  const el = rootRef.value
  if (!el) return
  gsap.fromTo(el.querySelectorAll('.dp__section'), { y: 10, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.35, stagger: 0.06, ease: 'power2.out' })
})
</script>

<template>
  <EventFrame kind="design-plan" symbol="D" label="Design planning" :detail="event.summary" status="done" state="done" open>
    <div ref="rootRef" class="dp">
      <p v-if="event.summary" class="dp__summary">{{ event.summary }}</p>

      <section v-if="list(event.requirement_model).length" class="dp__section">
        <div class="dp__section-head">
          <span class="dp__chip dp__chip--requirement">Requirements</span>
          <span class="dp__count">{{ event.requirement_model.length }}</span>
        </div>
        <div class="dp__requirements">
          <div v-for="item in event.requirement_model" :key="item.id || item.concept" class="dp__req">
            <span class="dp__id-badge">{{ item.id }}</span>
            <strong>{{ item.concept }}</strong>
            <p v-if="item.behavior">{{ item.behavior }}</p>
          </div>
        </div>
      </section>

      <section v-if="list(event.project_alignment).length" class="dp__section">
        <div class="dp__section-head">
          <span class="dp__chip dp__chip--alignment">Project alignment</span>
          <span class="dp__count">{{ event.project_alignment.length }}</span>
        </div>
        <div class="dp__alignments">
          <div v-for="item in event.project_alignment" :key="`${item.requirement_id}:${item.project_fact}`" class="dp__align" :class="`is-${item.status || 'info'}`">
            <div class="dp__align-head">
              <span class="dp__status-dot" :class="`is-${item.status || 'info'}`"></span>
              <span class="dp__align-status">{{ item.status }}</span>
              <b class="dp__align-req">{{ item.requirement_id }}</b>
            </div>
            <p>{{ item.project_fact }}</p>
            <div v-if="list(item.evidence).length" class="dp__trail">
              <span v-for="(e, i) in item.evidence" :key="i" class="dp__trail-step">
                {{ e }}
                <span v-if="i < item.evidence.length - 1" class="dp__trail-arrow">&#8594;</span>
              </span>
            </div>
          </div>
        </div>
      </section>

      <section v-if="list(event.decision_gaps).length" class="dp__section">
        <div class="dp__section-head">
          <span class="dp__chip dp__chip--gap">Decision gaps</span>
          <span class="dp__count">{{ event.decision_gaps.length }}</span>
        </div>
        <div class="dp__gaps">
          <div v-for="item in event.decision_gaps" :key="item.id || item.question" class="dp__gap" :class="{ 'is-blocking': item.blocks_implementation }">
            <div class="dp__gap-head">
              <span class="dp__id-badge dp__id-badge--gap">{{ item.id }}</span>
              <span v-if="item.blocks_implementation" class="dp__block-tag">&#9888; blocks impl</span>
              <span v-else class="dp__open-tag">open</span>
            </div>
            <p>{{ item.question }}</p>
            <p v-if="item.why" class="dp__gap-why">{{ item.why }}</p>
          </div>
        </div>
      </section>

      <section v-if="list(event.design_decisions).length" class="dp__section">
        <div class="dp__section-head">
          <span class="dp__chip dp__chip--decision">Design decisions</span>
          <span class="dp__count">{{ event.design_decisions.length }}</span>
        </div>
        <div class="dp__decisions">
          <div v-for="item in event.design_decisions" :key="item.id || item.decision" class="dp__dec">
            <div class="dp__dec-head">
              <span class="dp__id-badge dp__id-badge--dec">{{ item.id }}</span>
            </div>
            <p>{{ item.decision }}</p>
            <div v-if="list(item.because).length" class="dp__trail">
              <span class="dp__trail-label">because</span>
              <span v-for="(r, i) in item.because" :key="i" class="dp__trail-step">
                {{ r }}
                <span v-if="i < item.because.length - 1" class="dp__trail-arrow">&#8594;</span>
              </span>
            </div>
          </div>
        </div>
      </section>

      <section v-if="list(event.out_of_scope).length" class="dp__section dp__section--muted">
        <div class="dp__section-head">
          <span class="dp__chip dp__chip--scope">Out of scope</span>
          <span class="dp__count">{{ event.out_of_scope.length }}</span>
        </div>
        <ul class="dp__scope-list">
          <li v-for="item in event.out_of_scope" :key="item">{{ item }}</li>
        </ul>
      </section>
    </div>
  </EventFrame>
</template>

<style scoped>
.dp {
  display: grid;
  gap: 8px;
}

.dp__summary {
  margin: 0;
  padding: 8px 10px;
  border-radius: 7px;
  color: var(--text-h, #102a5c);
  background: color-mix(in srgb, var(--event, #6658c7) 5%, transparent);
  font-weight: 580;
  font-size: 12px;
  line-height: 1.55;
}

.dp__section {
  display: grid;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 8px;
  background: var(--bg-raised, #ffffff);
}

.dp__section--muted {
  background: var(--code-bg, #f1f4f8);
  border-style: dashed;
}

.dp__section-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.dp__chip {
  padding: 2px 7px;
  border-radius: 4px;
  font: 700 8px/1 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .04em;
}

.dp__chip--requirement { color: #3b5998; background: rgba(59,89,152,.09); }
.dp__chip--alignment { color: #7b2d8e; background: rgba(123,45,142,.08); }
.dp__chip--gap { color: var(--warn, #c48b00); background: rgba(196,139,0,.08); }
.dp__chip--decision { color: var(--ok, #11866f); background: rgba(17,134,111,.08); }
.dp__chip--scope { color: var(--text-muted, #71809c); background: rgba(113,128,156,.08); }

.dp__count {
  margin-left: auto;
  color: var(--text-muted, #71809c);
  font: 9px/1 var(--mono, monospace);
}

.dp__id-badge {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 4px;
  color: #fff;
  background: var(--event, #6658c7);
  font: 700 8px/1.3 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .03em;
}

.dp__id-badge--gap { background: var(--warn, #c48b00); }
.dp__id-badge--dec { background: var(--ok, #11866f); }

/* Requirements */
.dp__requirements {
  display: grid;
  gap: 5px;
}

.dp__req {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: baseline;
  gap: 6px 8px;
  padding: 7px 9px;
  border-radius: 6px;
  border-left: 3px solid var(--event, #6658c7);
  background: var(--code-bg, #f1f4f8);
}

.dp__req strong {
  color: var(--text-h, #102a5c);
  font-size: 11.5px;
  line-height: 1.4;
}

.dp__req p {
  grid-column: 2;
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 10.5px;
  line-height: 1.5;
}

/* Project alignment */
.dp__alignments {
  display: grid;
  gap: 5px;
}

.dp__align {
  display: grid;
  gap: 4px;
  padding: 7px 9px;
  border-radius: 6px;
  border-left: 3px solid transparent;
  background: var(--code-bg, #f1f4f8);
}

.dp__align.is-aligned { border-color: var(--ok, #11866f); }
.dp__align.is-conflict { border-color: var(--err, #c44747); }
.dp__align.is-neutral,
.dp__align.is-info { border-color: var(--accent-text, #1748a3); }

.dp__align-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.dp__status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--text-muted);
}

.dp__status-dot.is-aligned { background: var(--ok, #11866f); }
.dp__status-dot.is-conflict { background: var(--err, #c44747); }
.dp__status-dot.is-neutral,
.dp__status-dot.is-info { background: var(--accent-text, #1748a3); }

.dp__align-status {
  color: var(--text-muted, #71809c);
  font: 700 8px/1 var(--mono, monospace);
  text-transform: uppercase;
}

.dp__align-req {
  margin-left: auto;
  color: var(--event, #6658c7);
  font: 700 8px/1 var(--mono, monospace);
}

.dp__align p {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
}

/* Reasoning trail */
.dp__trail {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 2px;
}

.dp__trail-label {
  color: var(--text-muted);
  font: italic 8.5px/1 var(--mono, monospace);
  margin-right: 2px;
}

.dp__trail-step {
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--accent-text, #1748a3);
  background: var(--accent-bg, rgba(23,86,209,.07));
  font: 9px/1.4 var(--mono, monospace);
}

.dp__trail-arrow {
  color: var(--text-muted);
  font-size: 8px;
  margin: 0 1px;
}

/* Decision gaps */
.dp__gaps {
  display: grid;
  gap: 5px;
}

.dp__gap {
  display: grid;
  gap: 4px;
  padding: 7px 9px;
  border-radius: 6px;
  border-left: 3px solid var(--border, #d9e3f5);
  background: var(--code-bg, #f1f4f8);
}

.dp__gap.is-blocking {
  border-color: #d4a017;
  background: rgba(212,160,23,.06);
}

.dp__gap-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.dp__block-tag {
  padding: 1px 5px;
  border-radius: 3px;
  color: #8a5b00;
  background: rgba(196,139,0,.12);
  font: 700 8px/1 var(--mono, monospace);
  text-transform: uppercase;
}

.dp__open-tag {
  padding: 1px 5px;
  border-radius: 3px;
  color: var(--text-muted);
  background: rgba(113,128,156,.08);
  font: 700 8px/1 var(--mono, monospace);
  text-transform: uppercase;
}

.dp__gap p {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
}

.dp__gap-why {
  padding: 5px 7px;
  border-radius: 5px;
  color: var(--text-muted);
  background: rgba(0,0,0,.03);
  font: 10px/1.45 var(--mono, monospace);
}

/* Design decisions */
.dp__decisions {
  display: grid;
  gap: 5px;
}

.dp__dec {
  display: grid;
  gap: 4px;
  padding: 7px 9px;
  border-radius: 6px;
  border-left: 3px solid var(--ok, #11866f);
  background: rgba(17,134,111,.04);
}

.dp__dec-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.dp__dec p {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 11px;
  line-height: 1.5;
}

/* Out of scope */
.dp__scope-list {
  margin: 0;
  padding: 0 0 0 14px;
  list-style: none;
  display: grid;
  gap: 3px;
}

.dp__scope-list li {
  color: var(--text-muted, #71809c);
  font-size: 10.5px;
  line-height: 1.5;
  position: relative;
}

.dp__scope-list li::before {
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
