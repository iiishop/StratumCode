<script setup>
import { computed, ref, onMounted } from 'vue'
import { gsap } from 'gsap'
import { animate } from 'animejs'
import HighlightedText from './HighlightedText.vue'

const props = defineProps({
  kind: { type: String, required: true },
  label: { type: String, required: true },
  detail: { type: String, default: '' },
  status: { type: String, default: '' },
  symbol: { type: String, default: '·' },
  state: { type: String, default: '' },
  open: { type: Boolean, default: false },
  collapsible: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle'])
const surface = ref(null)
const expanded = computed(() => props.collapsible ? props.open : true)

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  gsap.fromTo(
    surface.value,
    { autoAlpha: 0, y: 10, filter: 'blur(5px)' },
    { autoAlpha: 1, y: 0, filter: 'blur(0px)', duration: .4, ease: 'power3.out', clearProps: 'filter' },
  )
})

function moveSpotlight(event) {
  const bounds = surface.value?.getBoundingClientRect()
  if (!bounds) return
  surface.value.style.setProperty('--spot-x', `${event.clientX - bounds.left}px`)
  surface.value.style.setProperty('--spot-y', `${event.clientY - bounds.top}px`)
}

function expandEnter(el, done) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    done()
    return
  }
  el.style.overflow = 'hidden'
  gsap.fromTo(el, { height: 0, autoAlpha: 0 }, {
    height: el.scrollHeight,
    autoAlpha: 1,
    duration: .3,
    ease: 'power3.out',
    onComplete: () => {
      gsap.set(el, { height: 'auto', clearProps: 'overflow' })
      done()
    },
  })
  animate(el.querySelector('.event-frame__body'), {
    translateY: [-6, 0],
    opacity: [0, 1],
    duration: 260,
    ease: 'outCubic',
  })
}

function collapseLeave(el, done) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    done()
    return
  }
  el.style.overflow = 'hidden'
  gsap.fromTo(el, { height: el.scrollHeight, autoAlpha: 1 }, {
    height: 0,
    autoAlpha: 0,
    duration: .24,
    ease: 'power2.inOut',
    onComplete: done,
  })
}
</script>

<template>
  <article class="event-frame" :class="[`event-frame--${kind}`, state && `event-frame--${state}`, status && `event-frame--${status}`]">
    <div class="event-frame__rail">
      <span class="event-frame__node">{{ symbol }}</span>
    </div>
    <div ref="surface" class="event-frame__surface" @pointermove="moveSpotlight">
      <button
        class="event-frame__head"
        :class="{ 'is-static': !collapsible }"
        type="button"
        :disabled="!collapsible"
        @click="collapsible && emit('toggle')"
      >
        <span class="event-frame__titles">
          <span class="event-frame__label">{{ label }}</span>
          <small v-if="detail"><HighlightedText :text="detail" /></small>
        </span>
        <span v-if="status" class="event-frame__status" :class="{ 'is-running': status === 'running' }">{{ status }}</span>
        <span v-if="collapsible" class="event-frame__chevron" :class="{ 'is-open': open }">⌄</span>
      </button>
      <Transition @enter="expandEnter" @leave="collapseLeave">
        <div v-show="expanded" class="event-frame__expand">
          <div class="event-frame__clip">
            <div class="event-frame__body"><slot /></div>
          </div>
        </div>
      </Transition>
    </div>
  </article>
</template>

<style scoped>
.event-frame {
  --event: #1756d1;
  position: relative;
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  min-width: 0;
}
.event-frame--thinking { --event: #c48b00; }
.event-frame--subagent { --event: #6658c7; }
.event-frame--diff { --event: #e56b2f; }
.event-frame--output { --event: #1756d1; }
.event-frame--stage { --event: #1756d1; }
.event-frame--hypothesis { --event: #1756d1; }
.event-frame--evidence { --event: #0f7d65; }
.event-frame--evidence.event-frame--oppose { --event: #c48b00; }
.event-frame--relation { --event: #6658c7; }
.event-frame--verdict { --event: #1756d1; }
.event-frame--verdict.event-frame--supported { --event: #0f7d65; }
.event-frame--verdict.event-frame--refuted { --event: #c44747; }
.event-frame--verdict.event-frame--inconclusive { --event: #c48b00; }
.event-frame--accepted { --event: #00a878; }
.event-frame--rejected { --event: #e11d74; }
.event-frame--error { --event: #c44747; }
.event-frame--usage { --event: #7c8ba0; }

.event-frame__rail {
  position: relative;
  display: flex;
  justify-content: center;
  padding-top: 2px;
}

.event-frame__rail::after {
  position: absolute;
  top: 28px;
  bottom: -12px;
  left: 50%;
  width: 1px;
  content: "";
  background: linear-gradient(180deg, color-mix(in srgb, var(--event) 22%, #b8cae8), transparent 88%);
}
.event-frame:last-child .event-frame__rail::after { display: none; }

.event-frame__node {
  position: relative;
  z-index: 1;
  display: grid;
  width: 24px;
  height: 24px;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--event) 28%, #e0e8f5);
  border-radius: 7px;
  color: var(--event);
  background: color-mix(in srgb, var(--event) 5%, #ffffff);
  box-shadow: 0 0 0 5px color-mix(in srgb, var(--event) 5%, transparent);
  font: 800 10px/1 var(--mono, monospace);
  transition: box-shadow var(--transition, 180ms ease);
}

.event-frame--thinking .event-frame__node,
.event-frame--tool .event-frame__node,
.event-frame--subagent .event-frame__node {
  color: #fff;
  background: var(--event);
  border-color: var(--event);
}

.event-frame:has(.event-frame__status.is-running) .event-frame__node {
  animation: node-pulse 1.55s ease-in-out infinite;
}

.event-frame__surface {
  --spot-x: 50%;
  --spot-y: 0%;
  position: relative;
  min-width: 0;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--event) 16%, #d4e0f2);
  border-radius: 11px;
  background: color-mix(in srgb, var(--event) 3%, #ffffff);
  box-shadow: 0 3px 14px rgba(31, 68, 119, .038);
  transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;
}

.event-frame__surface::before {
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  content: "";
  background: linear-gradient(180deg, var(--event), color-mix(in srgb, var(--event) 24%, transparent));
  opacity: .68;
}

.event-frame__surface::after {
  position: absolute;
  inset: 0;
  content: "";
  background: radial-gradient(300px circle at var(--spot-x) var(--spot-y), color-mix(in srgb, var(--event) 4%, transparent), transparent 70%);
  opacity: 0;
  transition: opacity .32s ease;
  pointer-events: none;
}

.event-frame__surface:hover {
  border-color: color-mix(in srgb, var(--event) 30%, #b8cae8);
  box-shadow: 0 8px 22px rgba(31, 68, 119, .065);
  transform: translateY(-1px);
}

.event-frame__surface:hover::after { opacity: 1; }

.event-frame--accepted .event-frame__surface {
  border-color: color-mix(in srgb, var(--event) 36%, #a8d8c8);
  background: color-mix(in srgb, var(--event) 4%, #f6fffc);
}

.event-frame--rejected .event-frame__surface {
  border-color: color-mix(in srgb, var(--event) 36%, #e8c0d0);
  background: color-mix(in srgb, var(--event) 3%, #fff8fa);
}

.event-frame__head {
  display: flex;
  width: 100%;
  min-height: 46px;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 0;
  color: var(--text-h, #102a5c);
  background: transparent;
  text-align: left;
  cursor: pointer;
  position: relative;
  z-index: 1;
}
.event-frame__head.is-static { cursor: default; }
.event-frame__head:disabled { opacity: 1; }

.event-frame__titles {
  display: grid;
  min-width: 0;
  flex: 1 1 auto;
  gap: 2px;
}

.event-frame__label {
  overflow: hidden;
  color: var(--text-h, #102a5c);
  font: 650 var(--font-ui, 12px)/1.3 var(--mono, monospace);
  letter-spacing: .01em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-frame__titles small {
  display: flex;
  min-height: 21px;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  color: var(--text-muted, #71809c);
  font-size: var(--font-caption, 11px);
  line-height: 21px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-frame__status {
  flex: 0 0 auto;
  margin-left: auto;
  padding: 2px 7px;
  border-radius: 5px;
  color: var(--event);
  background: color-mix(in srgb, var(--event) 8%, transparent);
  font: 700 9px/1.3 var(--mono, monospace);
  letter-spacing: .06em;
  text-transform: uppercase;
  white-space: nowrap;
}

.event-frame__status.is-running {
  color: #5c4200;
  background: rgba(245, 200, 66, .22);
  animation: status-glow 1.8s ease-in-out infinite;
}

.event-frame__chevron {
  flex-shrink: 0;
  color: var(--text-muted, #71809c);
  font-size: 11px;
  transition: transform .24s cubic-bezier(.22, 1, .36, 1);
}
.event-frame__chevron.is-open { transform: rotate(180deg); }

.event-frame__expand { opacity: 1; position: relative; z-index: 1; }
.event-frame__clip { min-height: 0; overflow: hidden; }

.event-frame__body {
  padding: 2px 14px 14px;
  min-width: 0;
  overflow-wrap: anywhere;
}

@keyframes node-pulse {
  50% {
    box-shadow: 0 0 0 8px color-mix(in srgb, var(--event) 12%, transparent),
                0 0 18px color-mix(in srgb, var(--event) 26%, transparent);
  }
}

@keyframes status-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 200, 66, 0); }
  50% { box-shadow: 0 0 0 4px rgba(245, 200, 66, .16); }
}

@media (prefers-reduced-motion: reduce) {
  .event-frame__surface,
  .event-frame__chevron,
  .event-frame__expand,
  .event-frame__body { transition: none; }
  .event-frame__status.is-running { animation: none; }
  .event-frame:has(.event-frame__status.is-running) .event-frame__node { animation: none; }
}
</style>
