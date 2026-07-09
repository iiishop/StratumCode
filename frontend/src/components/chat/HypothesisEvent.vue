<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { animate } from 'animejs'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })
const bar = ref(null)
const shown = ref(50)
const counter = { value: 50 }
const percent = computed(() => Math.round((props.event.confidence ?? .5) * 100))

function animateConfidence(value, previous = 50) {
  if (!bar.value || matchMedia('(prefers-reduced-motion: reduce)').matches) {
    shown.value = value
    return
  }
  animate(bar.value, {
    scaleX: [previous / 100, value / 100],
    duration: 620,
    ease: 'out(4)',
  })
  counter.value = previous
  animate(counter, {
    value,
    duration: 560,
    ease: 'outExpo',
    onUpdate: () => { shown.value = Math.round(counter.value) },
  })
}

onMounted(() => animateConfidence(percent.value))
watch(percent, (value, previous) => animateConfidence(value, previous))
</script>

<template>
  <EventFrame kind="hypothesis" symbol="H" label="Hypothesis" :status="event.status" :state="event.status" open>
    <p class="hypothesis__text">{{ event.text }}</p>
    <div class="hypothesis__confidence">
      <span>Confidence</span>
      <strong>{{ shown }}%</strong>
    </div>
    <div class="hypothesis__track"><i ref="bar"></i></div>
  </EventFrame>
</template>

<style scoped>
.hypothesis__text {
  margin: 0 0 12px;
  padding: 4px 0 0;
  color: var(--text-h, #102a5c);
  font-size: 12.5px;
  line-height: 1.65;
}

.hypothesis__confidence {
  display: flex;
  justify-content: space-between;
  color: var(--text-muted, #71809c);
  font: 700 10px/1.2 var(--mono, monospace);
  text-transform: uppercase;
  letter-spacing: .04em;
}

.hypothesis__confidence strong {
  color: var(--event, #1756d1);
}

.hypothesis__track {
  height: 6px;
  margin-top: 7px;
  overflow: hidden;
  border-radius: 99px;
  background: color-mix(in srgb, var(--event, #1756d1) 12%, #e4ecf8);
}

.hypothesis__track i {
  display: block;
  width: 100%;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--event, #1756d1), #f5c842);
  transform: scaleX(.5);
  transform-origin: left;
}
</style>
