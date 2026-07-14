<script setup>
import { ref, computed, inject, onMounted } from 'vue'
import { gsap } from 'gsap'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

const expanded = ref(true)
const trailRef = ref(null)

const messageEvents = inject('messageEvents', computed(() => []))

const STATES = {
  initializing:             { label: 'Init',       icon: '\u25C8', color: '#94a3b8' },
  analyzing:                { label: 'Analyze',    icon: '\u25C9', color: '#f59e0b' },
  preparing_investigation:  { label: 'Prep',       icon: '\u25C6', color: '#8b5cf6' },
  investigating:            { label: 'Investigate',icon: '\u25C8', color: '#3b82f6' },
  designing:                { label: 'Design',     icon: '\u2B21', color: '#10b981' },
  patch_planning:           { label: 'Plan',       icon: '\u25EB', color: '#f97316' },
  implementing:             { label: 'Implement',  icon: '\u2B22', color: '#06b6d4' },
  saving_session:           { label: 'Save',       icon: '\u2713', color: '#6366f1' },
}

const trail = computed(() => {
  const all = messageEvents.value.filter(e => e.type === 'state_transition')
  const idx = all.findIndex(e =>
    e.data.from_state === props.event.from_state &&
    e.data.to_state === props.event.to_state &&
    e.data.reason === props.event.reason,
  )
  const visible = idx === -1 ? all : all.slice(0, idx + 1)

  const steps = []
  for (let i = 0; i < visible.length; i++) {
    if (i === 0) steps.push(visible[i].data.from_state)
    steps.push(visible[i].data.to_state)
  }
  // dedupe consecutive duplicates (shouldn't happen, but safety)
  return steps.filter((s, i, a) => i === 0 || s !== a[i - 1])
})

const lastIdx = computed(() => trail.value.length - 1)

function stepInfo(key, i) {
  const meta = STATES[key] || { label: key || '?', icon: '\u00B7', color: '#94a3b8' }
  const isLast = i === lastIdx.value
  return { ...meta, isLast }
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

  const lastStep = trailRef.value?.querySelector('.trail__step--current')
  const track = trailRef.value?.querySelector('.trail__track')

  if (track) {
    const w = lastIdx.value > 0 ? `${(lastIdx.value / lastIdx.value) * 100}%` : '0%'
    gsap.fromTo(track, { width: '0%' }, { width: '100%', duration: .7, ease: 'power2.inOut', delay: .1 })
  }
  if (lastStep) {
    gsap.fromTo(lastStep, { scale: 0, opacity: 0 }, { scale: 1, opacity: 1, duration: .45, ease: 'back.out(2)', delay: .4 })
  }
})
</script>

<template>
  <EventFrame
    kind="state-transition"
    :symbol="(STATES[event.from_state] || {}).icon || '\u00B7'"
    label="State transition"
    :detail="`${(STATES[event.from_state] || {}).label || event.from_state} \u2192 ${(STATES[event.to_state] || {}).label || event.to_state}`"
    :status="event.to_state"
    :state="event.to_state"
    :open="expanded"
    collapsible
    @toggle="expanded = !expanded"
  >
    <div ref="trailRef" class="trail">
      <div class="trail__steps">
        <div class="trail__track-bg" />
        <div class="trail__track" />
        <span
          v-for="(key, i) in trail"
          :key="i"
          class="trail__step"
          :class="{ 'trail__step--current': i === lastIdx }"
          :style="{ '--c': stepInfo(key, i).color }"
          :title="stepInfo(key, i).label"
        >
          <span class="trail__dot" :style="{ background: stepInfo(key, i).color }">
            <span class="trail__icon">{{ stepInfo(key, i).icon }}</span>
          </span>
        </span>
      </div>
      <div class="trail__labels">
        <span
          v-for="(key, i) in trail"
          :key="i"
          class="trail__label"
          :class="{ 'is-here': i === lastIdx }"
          :style="i === lastIdx ? { color: stepInfo(key, i).color } : undefined"
        >{{ stepInfo(key, i).label }}</span>
      </div>
      <p v-if="event.reason" class="trail__reason">{{ event.reason }}</p>
    </div>
  </EventFrame>
</template>

<style scoped>
.trail__steps {
  position: relative;
  display: flex;
  align-items: center;
  height: 28px;
  padding: 0 6px;
}

.trail__track-bg {
  position: absolute;
  left: 12px;
  right: 12px;
  top: 50%;
  height: 2px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--event) 10%, transparent);
  transform: translateY(-50%);
}

.trail__track {
  position: absolute;
  left: 12px;
  top: 50%;
  height: 2px;
  border-radius: 999px;
  transform: translateY(-50%);
  width: 0%;
  background: linear-gradient(90deg,
    #94a3b8, #f59e0b, #8b5cf6, #3b82f6, #10b981, #f97316, #06b6d4, #6366f1
  );
}

.trail__step {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  justify-content: center;
}

.trail__dot {
  display: grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border-radius: 999px;
  opacity: .5;
  transition: opacity .2s ease;
}

.trail__step--current .trail__dot {
  width: 22px;
  height: 22px;
  opacity: 1;
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--c) 16%, transparent),
              0 0 12px color-mix(in srgb, var(--c) 25%, transparent);
}

.trail__icon {
  font-size: 8px;
  color: #fff;
  line-height: 1;
}

.trail__step--current .trail__icon {
  font-size: 10px;
}

.trail__labels {
  display: flex;
  margin-top: 5px;
  padding: 0 2px;
}

.trail__label {
  flex: 1;
  font: 500 7.5px/1 var(--mono, monospace);
  color: color-mix(in srgb, currentColor 28%, transparent);
  text-align: center;
  white-space: nowrap;
  letter-spacing: .02em;
  text-transform: uppercase;
}

.trail__label.is-here {
  font-weight: 700;
}

.trail__reason {
  margin: 8px 0 0;
  font: 400 10px/1.5 var(--sans, system-ui);
  color: var(--text-muted, #71809c);
  font-style: italic;
}
</style>
