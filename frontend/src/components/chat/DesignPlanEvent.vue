<script setup>
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })

function list(value) {
  return Array.isArray(value) ? value : []
}
</script>

<template>
  <EventFrame kind="design-plan" symbol="D" label="Design planning" :detail="event.summary" status="done" state="done" open>
    <div class="design-plan">
      <p v-if="event.summary" class="design-plan__summary">{{ event.summary }}</p>

      <section v-if="list(event.requirement_model).length">
        <small>requirement model</small>
        <p v-for="item in event.requirement_model" :key="item.id || item.concept">
          <b>{{ item.id }}</b><strong>{{ item.concept }}</strong>{{ item.behavior }}
        </p>
      </section>

      <section v-if="list(event.project_alignment).length">
        <small>project alignment</small>
        <p v-for="item in event.project_alignment" :key="`${item.requirement_id}:${item.project_fact}`">
          <b>{{ item.status }}</b><strong>{{ item.requirement_id }}</strong>{{ item.project_fact }}
          <em v-if="list(item.evidence).length">{{ item.evidence.join(' -> ') }}</em>
        </p>
      </section>

      <section v-if="list(event.decision_gaps).length">
        <small>decision gaps</small>
        <p v-for="item in event.decision_gaps" :key="item.id || item.question" :class="{ blocked: item.blocks_implementation }">
          <b>{{ item.id }}</b><strong>{{ item.blocks_implementation ? 'blocks' : 'open' }}</strong>{{ item.question }}
          <em v-if="item.why">{{ item.why }}</em>
        </p>
      </section>

      <section v-if="list(event.design_decisions).length">
        <small>design decisions</small>
        <p v-for="item in event.design_decisions" :key="item.id || item.decision">
          <b>{{ item.id }}</b>{{ item.decision }}
          <em v-if="list(item.because).length">{{ item.because.join(' -> ') }}</em>
        </p>
      </section>

      <section v-if="list(event.out_of_scope).length">
        <small>out of scope</small>
        <p v-for="item in event.out_of_scope" :key="item">{{ item }}</p>
      </section>
    </div>
  </EventFrame>
</template>

<style scoped>
.design-plan { display: grid; gap: 10px; color: var(--text, #3f5274); }
.design-plan__summary { margin: 0; color: var(--text-h, #102a5c); font-weight: 650; }
.design-plan section { display: grid; gap: 5px; }
.design-plan small { color: var(--event, #6658c7); font: 800 8px/1 var(--mono, monospace); letter-spacing: .06em; text-transform: uppercase; }
.design-plan p { margin: 0; font-size: 11px; line-height: 1.45; }
.design-plan p.blocked { color: #8a5b00; }
.design-plan b { margin-right: 7px; color: var(--event, #6658c7); font: 800 8px/1 var(--mono, monospace); text-transform: uppercase; }
.design-plan strong { margin-right: 7px; color: var(--text-h, #102a5c); font-weight: 650; }
.design-plan em { display: block; margin-top: 2px; color: var(--text-muted, #71809c); font: 8.5px/1.35 var(--mono, monospace); font-style: normal; }
</style>
