<script setup>
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })
</script>

<template>
  <EventFrame
    kind="verdict"
    :state="event.verdict"
    :symbol="event.verdict === 'supported' ? '✓' : event.verdict === 'refuted' ? '×' : '?'"
    label="Evidence verdict"
    :status="event.verdict"
    open
  >
    <div class="verdict__header">
      <div class="verdict__score">{{ Math.round(event.confidence * 100) }}%</div>
      <div class="verdict__meta">
        <span class="verdict__label">{{ event.verdict }}</span>
        <span v-if="event.support_count != null" class="verdict__tally">
          <b class="verdict__tally--support">+{{ event.support_count }}</b>
          <b class="verdict__tally--oppose">−{{ event.oppose_count }}</b>
        </span>
      </div>
    </div>

    <p v-if="event.summary" class="verdict__summary">{{ event.summary }}</p>

    <div v-if="event.findings?.length" class="verdict__findings">
      <div class="verdict__findings-head">Evidence items</div>
      <div
        v-for="f in event.findings"
        :key="f.id"
        class="verdict__finding"
        :class="`is-${f.stance}`"
      >
        <span class="verdict__finding-sign">{{ f.stance === 'support' ? '+' : '−' }}</span>
        <div class="verdict__finding-body">
          <strong>{{ f.claim }}</strong>
          <small>{{ f.source_uri }}</small>
        </div>
        <div class="verdict__finding-weight">
          <b>{{ f.weight }}%</b>
          <em><i :style="{ width: f.weight + '%' }"></i></em>
        </div>
      </div>
    </div>
  </EventFrame>
</template>

<style scoped>
.verdict__header {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  margin-bottom: 10px;
}

.verdict__score {
  flex-shrink: 0;
  color: var(--event, #1756d1);
  font: 800 24px/1 var(--mono, monospace);
}

.verdict__meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.verdict__label {
  color: var(--event, #1756d1);
  font: 700 11px/1.2 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .06em;
}

.verdict__tally {
  display: flex;
  gap: 10px;
  font: 700 10px/1 var(--mono, monospace);
}

.verdict__tally--support { color: #11866f; }
.verdict__tally--oppose { color: #c48b00; }

.verdict__summary {
  margin: 0 0 14px;
  padding: 10px 12px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--event, #1756d1) 5%, #f9fbfe);
  color: var(--text, #3f5274);
  font-size: 12.5px;
  line-height: 1.65;
}

.verdict__findings {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.verdict__findings-head {
  padding: 4px 0 2px;
  color: var(--text-muted, #71809c);
  font: 700 9px/1 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .06em;
}

.verdict__finding {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) 56px;
  gap: 8px;
  align-items: start;
  padding: 8px 10px;
  border: 1px solid rgba(23, 86, 209, .06);
  border-radius: 8px;
  background: #fafcff;
}

.verdict__finding.is-support {
  border-left: 3px solid #11866f;
}

.verdict__finding.is-oppose {
  border-left: 3px solid #c48b00;
}

.verdict__finding-sign {
  display: grid;
  width: 18px;
  height: 18px;
  place-items: center;
  border-radius: 5px;
  color: #fff;
  background: #11866f;
  font: 800 10px/1 var(--mono, monospace);
}

.is-oppose .verdict__finding-sign {
  background: #c48b00;
}

.verdict__finding-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.verdict__finding-body strong {
  color: var(--text-h, #102a5c);
  font-size: 11px;
  line-height: 1.4;
}

.verdict__finding-body small {
  overflow: hidden;
  color: var(--text-muted, #71809c);
  font: 9px/1.3 var(--mono, monospace);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.verdict__finding-weight {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.verdict__finding-weight b {
  color: var(--text-muted, #71809c);
  font: 700 10px/1 var(--mono, monospace);
  text-align: right;
}

.verdict__finding-weight em {
  height: 4px;
  overflow: hidden;
  border-radius: 99px;
  background: #e4ecf8;
}

.verdict__finding-weight em i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--event, #1756d1), color-mix(in srgb, var(--event, #1756d1) 40%, #f5c842));
}
</style>
