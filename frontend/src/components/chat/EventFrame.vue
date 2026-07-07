<script setup>
import { ref, onMounted } from 'vue'
import { gsap } from 'gsap'
import HighlightedText from './HighlightedText.vue'

defineProps({
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
      <div class="event-frame__expand" :class="{ 'is-open': open }">
        <div class="event-frame__clip">
          <div class="event-frame__body"><slot /></div>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.event-frame {
  --event: #1756d1;
  position: relative;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  min-width: 0;
}
.event-frame--thinking { --event: #d49d00; }
.event-frame--subagent { --event: #6658c7; }
.event-frame--diff { --event: #e56b2f; }
.event-frame--output { --event: #1756d1; }
.event-frame--stage { --event: #1756d1; }
.event-frame--hypothesis { --event: #1756d1; }
.event-frame--evidence { --event: #11866f; }
.event-frame--evidence.event-frame--oppose { --event: #d49300; }
.event-frame--relation { --event: #6658c7; }
.event-frame--verdict { --event: #1756d1; }
.event-frame--verdict.event-frame--supported { --event: #11866f; }
.event-frame--verdict.event-frame--refuted { --event: #c44747; }
.event-frame--verdict.event-frame--inconclusive { --event: #d49300; }
.event-frame--accepted { --event: #00a878; }
.event-frame--rejected { --event: #e11d74; }
.event-frame--error { --event: #c44747; }
.event-frame__rail { position: relative; display: flex; justify-content: flex-start; }
.event-frame__rail::after { position: absolute; top: 25px; bottom: -10px; left: 10px; width: 1px; content: ""; background: linear-gradient(#a8bfe5, transparent); }
.event-frame:last-child .event-frame__rail::after { display: none; }
.event-frame__node {
  position: relative;
  z-index: 1;
  display: grid;
  width: 21px;
  height: 21px;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--event) 44%, white);
  border-radius: 6px;
  color: var(--event);
  background: #fff;
  box-shadow: 0 0 0 4px #edf3fc;
  font: 800 10px/1 var(--mono, monospace);
}
.event-frame__surface {
  --spot-x: 50%;
  --spot-y: 0%;
  position: relative;
  min-width: 0;
  overflow: hidden;
  border: 1px solid rgba(139, 166, 205, .38);
  border-radius: 11px;
  background:
    radial-gradient(260px circle at var(--spot-x) var(--spot-y), color-mix(in srgb, var(--event) 10%, transparent), transparent 68%),
    rgba(255, 255, 255, .82);
  box-shadow: 0 7px 22px rgba(31, 68, 119, .055);
  backdrop-filter: blur(12px);
  transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;
}
.event-frame__surface::before {
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  content: "";
  background: linear-gradient(180deg, var(--event), color-mix(in srgb, var(--event) 30%, transparent));
  opacity: .78;
}
.event-frame__surface:hover { border-color: color-mix(in srgb, var(--event) 35%, #cbd8eb); box-shadow: 0 10px 28px rgba(31, 68, 119, .09); transform: translateY(-1px); }
.event-frame--thinking .event-frame__surface { background: radial-gradient(260px circle at var(--spot-x) var(--spot-y), rgba(245, 200, 66, .2), transparent 68%), linear-gradient(105deg, rgba(255, 249, 222, .72), rgba(255, 255, 255, .88)); }
.event-frame--tool .event-frame__surface { background: radial-gradient(260px circle at var(--spot-x) var(--spot-y), rgba(23, 86, 209, .14), transparent 68%), linear-gradient(105deg, rgba(235, 243, 255, .74), rgba(255, 255, 255, .9)); }
.event-frame--subagent .event-frame__surface { background: radial-gradient(260px circle at var(--spot-x) var(--spot-y), rgba(102, 88, 199, .15), transparent 68%), linear-gradient(105deg, rgba(242, 240, 255, .78), rgba(255, 255, 255, .9)); }
.event-frame--accepted .event-frame__surface {
  border-color: rgba(0, 168, 120, .46);
  background: radial-gradient(320px circle at var(--spot-x) var(--spot-y), rgba(0, 220, 155, .2), transparent 68%), linear-gradient(105deg, rgba(224, 255, 246, .94), rgba(255, 255, 255, .96));
  box-shadow: 0 12px 32px rgba(0, 168, 120, .15);
}
.event-frame--rejected .event-frame__surface {
  border-color: rgba(225, 29, 116, .44);
  background: radial-gradient(320px circle at var(--spot-x) var(--spot-y), rgba(255, 45, 139, .17), transparent 68%), linear-gradient(105deg, rgba(255, 232, 244, .94), rgba(255, 255, 255, .96));
  box-shadow: 0 12px 32px rgba(225, 29, 116, .14);
}
.event-frame--thinking .event-frame__node,
.event-frame--tool .event-frame__node,
.event-frame--subagent .event-frame__node { color: #fff; background: var(--event); border-color: var(--event); }
.event-frame:has(.event-frame__status.is-running) .event-frame__node {
  animation: node-pulse 1.55s ease-in-out infinite;
}
.event-frame__head { display: flex; width: 100%; min-height: 43px; align-items: center; gap: 9px; padding: 8px 11px; border: 0; color: #1b3656; background: transparent; text-align: left; cursor: pointer; }
.event-frame__head.is-static { cursor: default; }.event-frame__head:disabled { opacity: 1; }
.event-frame__titles { display: grid; min-width: 0; gap: 2px; }
.event-frame__label { color: #153252; font: 720 var(--font-ui, 12px)/1.25 var(--mono, monospace); letter-spacing: .01em; }
.event-frame__titles small {
  display: flex;
  min-height: 23px;
  align-items: center;
  overflow: visible;
  color: #76889f;
  font-size: var(--font-caption, 11px);
  line-height: 23px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.event-frame__status { margin-left: auto; color: var(--event); font: 750 10px/1 var(--mono, monospace); letter-spacing: .07em; text-transform: uppercase; }
.event-frame__status.is-running {
  color: transparent;
  background: linear-gradient(100deg, color-mix(in srgb, var(--event) 70%, #40536d) 30%, #fff 48%, var(--event) 66%);
  background-size: 220% 100%;
  background-clip: text;
  animation: shiny 1.5s linear infinite;
}
.event-frame__chevron { color: #7f91a7; transition: transform .24s cubic-bezier(.22, 1, .36, 1); }.event-frame__chevron.is-open { transform: rotate(180deg); }
.event-frame__expand {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  transition:
    grid-template-rows .34s cubic-bezier(.22, 1, .36, 1),
    opacity .2s ease;
}
.event-frame__expand.is-open { grid-template-rows: 1fr; opacity: 1; }
.event-frame__clip { min-height: 0; overflow: hidden; }
.event-frame__body {
  padding: 0 11px 11px;
  transform: translateY(-5px);
  transition: transform .34s cubic-bezier(.22, 1, .36, 1);
}
.event-frame__expand.is-open .event-frame__body { transform: translateY(0); }
@keyframes shiny { to { background-position: -220% 0; } }
@keyframes node-pulse { 50% { box-shadow: 0 0 0 6px color-mix(in srgb, var(--event) 17%, transparent), 0 0 16px color-mix(in srgb, var(--event) 35%, transparent); } }
@media (prefers-reduced-motion: reduce) {
  .event-frame__surface,.event-frame__chevron,.event-frame__expand,.event-frame__body { transition: none; }
  .event-frame__status.is-running { animation: none; color: var(--event); }
  .event-frame:has(.event-frame__status.is-running) .event-frame__node { animation: none; }
}
</style>
