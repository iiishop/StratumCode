<script setup>
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })

function list(value) {
  return Array.isArray(value) ? value : []
}
</script>

<template>
  <EventFrame kind="patch-plan" symbol="P" label="Patch planning" :detail="event.summary" status="done" state="done" open>
    <div class="patch-plan">
      <p v-if="event.summary" class="patch-plan__summary">{{ event.summary }}</p>

      <section v-if="list(event.files_to_change).length">
        <small>files</small>
        <p v-for="file in event.files_to_change" :key="file"><code>{{ file }}</code></p>
      </section>

      <section v-if="list(event.implementation_steps).length">
        <small>implementation steps</small>
        <p v-for="step in event.implementation_steps" :key="step.id || `${step.file}:${step.action}`">
          <b>{{ step.id }}</b><code v-if="step.file">{{ step.file }}</code><strong v-if="step.target">{{ step.target }}</strong>{{ step.action }}
          <em v-if="list(step.because).length">because: {{ step.because.join(' -> ') }}</em>
          <em v-if="step.required_behavior_if_removed">remove breaks: {{ step.required_behavior_if_removed }}</em>
          <em v-if="step.minimality_check">minimality: {{ step.minimality_check }}</em>
        </p>
      </section>

      <section v-if="list(event.responsibility_chain).length">
        <small>responsibility chain</small>
        <p v-for="item in event.responsibility_chain" :key="item.step_id || item.removal_breaks">
          <b>{{ item.step_id }}</b>
          <span v-if="list(item.requirement_ids).length">req {{ item.requirement_ids.join(', ') }}</span>
          <span v-if="list(item.decision_ids).length">decision {{ item.decision_ids.join(', ') }}</span>
          <em v-if="list(item.project_facts).length">{{ item.project_facts.join(' -> ') }}</em>
          <em v-if="item.removal_breaks">remove breaks: {{ item.removal_breaks }}</em>
        </p>
      </section>

      <section v-if="list(event.acceptance_mapping).length">
        <small>acceptance mapping</small>
        <p v-for="item in event.acceptance_mapping" :key="item.acceptance_id || item.verification">
          <b>{{ item.acceptance_id }}</b>
          <span v-if="list(item.covered_by).length">{{ item.covered_by.join(', ') }}</span>
          {{ item.verification }}
        </p>
      </section>

      <section v-if="list(event.tests_or_checks).length">
        <small>checks</small>
        <p v-for="item in event.tests_or_checks" :key="item">{{ item }}</p>
      </section>

      <section v-if="list(event.risks).length">
        <small>risks</small>
        <p v-for="item in event.risks" :key="item">{{ item }}</p>
      </section>

      <section v-if="list(event.out_of_scope).length">
        <small>out of scope</small>
        <p v-for="item in event.out_of_scope" :key="item">{{ item }}</p>
      </section>
    </div>
  </EventFrame>
</template>

<style scoped>
.patch-plan { display: grid; gap: 10px; color: var(--text, #3f5274); }
.patch-plan__summary { margin: 0; color: var(--text-h, #102a5c); font-weight: 650; }
.patch-plan section { display: grid; gap: 5px; }
.patch-plan small { color: var(--event, #6658c7); font: 800 8px/1 var(--mono, monospace); letter-spacing: .06em; text-transform: uppercase; }
.patch-plan p { margin: 0; font-size: 11px; line-height: 1.45; }
.patch-plan code { margin-right: 7px; padding: 1px 5px; border-radius: 4px; color: var(--accent-text, #1748a3); background: rgba(23, 86, 209, .07); font: 10px/1.5 var(--mono, monospace); }
.patch-plan b { margin-right: 7px; color: var(--event, #6658c7); font: 800 8px/1 var(--mono, monospace); }
.patch-plan strong { margin-right: 7px; color: var(--text-h, #102a5c); font-weight: 650; }
.patch-plan span { margin-right: 7px; color: var(--accent-text, #1748a3); font: 9px/1.35 var(--mono, monospace); }
.patch-plan em { display: block; margin-top: 2px; color: var(--text-muted, #71809c); font: 8.5px/1.35 var(--mono, monospace); font-style: normal; }
</style>
