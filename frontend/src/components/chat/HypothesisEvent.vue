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
.hypothesis__text { margin: 0 0 10px; color: #243e5c; font-size: 12px; line-height: 1.65; }
.hypothesis__confidence { display: flex; justify-content: space-between; color: #7589a3; font: 700 10px/1.2 var(--mono); text-transform: uppercase; }
.hypothesis__confidence strong { color: #1756d1; }
.hypothesis__track { height: 5px; margin-top: 6px; overflow: hidden; border-radius: 99px; background: #dce7f6; }
.hypothesis__track i { display: block; width: 100%; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #1756d1, #f5c642); transform: scaleX(.5); transform-origin: left; }
</style>
