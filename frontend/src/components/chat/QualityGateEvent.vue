<script setup>
import { computed } from 'vue'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const statusMeta = computed(() => {
  const s = props.event.status || ''
  if (s === 'grounded') return { symbol: '✓', cls: 'is-grounded', label: 'Grounded' }
  if (s === 'verify') return { symbol: '?', cls: 'is-verify', label: 'Verify' }
  if (s === 'clearify') return { symbol: '?', cls: 'is-clearify', label: 'Clearify' }
  if (s === 'investigate') return { symbol: '!', cls: 'is-investigate', label: 'Investigate' }
  return { symbol: '…', cls: '', label: s || 'Unknown' }
})

const missing = computed(() => (Array.isArray(props.event.missing) ? props.event.missing : []))
</script>

<template>
  <EventFrame
    kind="quality_gate"
    :state="event.status"
    :symbol="statusMeta.symbol"
    label="Semantic quality gate"
    :status="event.status"
    open
  >
    <div class="gate__header">
      <span class="gate__badge" :class="statusMeta.cls">{{ statusMeta.label }}</span>
      <span v-if="event.unknown_id" class="gate__unknown">{{ event.unknown_id }}</span>
      <span v-if="event.repair_mode" class="gate__repair">repair: {{ event.repair_mode }}</span>
    </div>

    <p v-if="event.reason" class="gate__reason">{{ event.reason }}</p>

    <div v-if="missing.length" class="gate__missing">
      <div class="gate__missing-head">Missing requirements</div>
      <div v-for="(m, i) in missing" :key="i" class="gate__missing-item">
        <span class="gate__missing-dot" />
        <div class="gate__missing-body">
          <span v-if="m.acceptance_id" class="gate__missing-ac">{{ m.acceptance_id }}</span>
          <span>{{ m.requirement }}</span>
        </div>
      </div>
    </div>

    <p v-if="event.hypothesis" class="gate__extra"><b>Hypothesis:</b> {{ event.hypothesis }}</p>
    <p v-if="event.question" class="gate__extra"><b>Question:</b> {{ event.question }}</p>
  </EventFrame>
</template>

<style scoped>
.gate__header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.gate__badge {
  padding: 3px 8px;
  border-radius: 5px;
  color: #fff;
  font: 800 10px/1.2 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .05em;
}

.gate__badge.is-grounded { background: #11866f; }
.gate__badge.is-verify   { background: #c48b00; }
.gate__badge.is-clearify { background: #1756d1; }
.gate__badge.is-investigate { background: #c44747; }

.gate__unknown {
  color: var(--text-muted, #71809c);
  font: 700 10px/1.2 var(--mono, monospace);
}

.gate__repair {
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(196, 139, 0, .12);
  color: #a06c00;
  font: 700 9px/1.2 var(--mono, monospace);
}

.gate__reason {
  margin: 0 0 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--event, #6658c7) 5%, #f9fbfe);
  color: var(--text, #3f5274);
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
}

.gate__missing {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.gate__missing-head {
  padding: 4px 0 2px;
  color: var(--text-muted, #71809c);
  font: 700 9px/1 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .06em;
}

.gate__missing-item {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 7px 10px;
  border: 1px solid rgba(196, 71, 71, .1);
  border-radius: 8px;
  background: rgba(196, 71, 71, .03);
}

.gate__missing-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  margin-top: 5px;
  border-radius: 50%;
  background: #c44747;
}

.gate__missing-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--text, #3f5274);
  font-size: 11.5px;
  line-height: 1.5;
}

.gate__missing-ac {
  color: #c44747;
  font: 700 9px/1 var(--mono, monospace);
}

.gate__extra {
  margin: 10px 0 0;
  color: var(--text-muted, #71809c);
  font-size: 11.5px;
  line-height: 1.5;
}

.gate__extra b {
  color: var(--text, #3f5274);
}
</style>
