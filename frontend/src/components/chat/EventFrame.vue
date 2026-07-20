<script setup>
import { computed } from 'vue'
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
const expanded = computed(() => props.collapsible ? props.open : true)
</script>

<template>
  <article class="event-frame" :class="[`event-frame--${kind}`, state && `event-frame--${state}`, status && `event-frame--${status}`]">
    <div class="event-frame__rail">
      <span class="event-frame__node">{{ symbol }}</span>
    </div>
    <div class="event-frame__surface">
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
      <Transition name="event-frame-expand">
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
  grid-template-columns: 36px minmax(0, 1fr);
  min-width: 0;
}
.event-frame--thinking { --event: #c48b00; }
.event-frame--code-nav { --event: #6658c7; }
.event-frame--subagent { --event: #6658c7; }
.event-frame--diff { --event: #e56b2f; }
.event-frame--patch { --event: #e56b2f; }
.event-frame--output { --event: #1756d1; }
.event-frame--task-analysis { --event: #6658c7; }
.event-frame--stage { --event: #1756d1; }
.event-frame--state-transition { --event: #7c8ba0; }
.event-frame--hypothesis { --event: #1756d1; }
.event-frame--evidence { --event: #0f7d65; }
.event-frame--evidence.event-frame--oppose { --event: #c48b00; }
.event-frame--relation { --event: #6658c7; }
.event-frame--verdict { --event: #1756d1; }
.event-frame--verdict.event-frame--supported { --event: #0f7d65; }
.event-frame--verdict.event-frame--refuted { --event: #c44747; }
.event-frame--verdict.event-frame--inconclusive { --event: #c48b00; }
.event-frame--step-result { --event: #6658c7; }
.event-frame--safety-stop { --event: #c44747; }
.event-frame--user-question { --event: #c48b00; }
.event-frame--accepted { --event: #00a878; }
.event-frame--rejected { --event: #e11d74; }
.event-frame--error { --event: #c44747; }
.event-frame--usage { --event: #7c8ba0; }

.event-frame__rail {
  position: relative;
  display: flex;
  justify-content: center;
  padding-top: 3px;
}

.event-frame__rail::after {
  position: absolute;
  top: 31px;
  bottom: -10px;
  left: 50%;
  width: 2px;
  content: "";
  background: linear-gradient(180deg, color-mix(in srgb, var(--event) 18%, #d8e3f4), rgba(216, 227, 244, .18));
}
.event-frame:last-child .event-frame__rail::after { display: none; }

.event-frame__node {
  position: relative;
  z-index: 1;
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 2px solid color-mix(in srgb, var(--event) 24%, #e0e8f5);
  border-radius: 50%;
  color: var(--event);
  background: color-mix(in srgb, var(--event) 5%, #ffffff);
  box-shadow: 0 0 0 4px #f7f9fd;
  font: 800 10px/1 var(--mono, monospace);
  transition: box-shadow var(--transition, 180ms ease);
}

.event-frame--thinking .event-frame__node,
.event-frame--code-nav .event-frame__node,
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
  position: relative;
  min-width: 0;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--event) 13%, #d4e0f2);
  border-radius: 8px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--event) 3%, #ffffff), #ffffff);
  box-shadow: 0 2px 10px rgba(31, 68, 119, .032);
  animation: event-frame-in 220ms cubic-bezier(.16, 1, .3, 1) both;
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

.event-frame__surface:hover {
  border-color: color-mix(in srgb, var(--event) 26%, #b8cae8);
  box-shadow: 0 6px 18px rgba(31, 68, 119, .058);
  transform: translateY(-1px);
}

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
  min-height: 42px;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
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
.event-frame__head:not(.is-static):hover {
  background: color-mix(in srgb, var(--event) 4%, transparent);
}

.event-frame__titles {
  display: flex;
  min-width: 0;
  flex: 1 1 auto;
  align-items: center;
  gap: 8px;
}

.event-frame__label {
  flex: 0 0 auto;
  overflow: hidden;
  max-width: 180px;
  padding: 2px 7px;
  border-radius: 4px;
  color: var(--event);
  background: color-mix(in srgb, var(--event) 8%, transparent);
  font: 760 9.5px/1.3 var(--mono, monospace);
  letter-spacing: .06em;
  text-overflow: ellipsis;
  text-transform: uppercase;
  white-space: nowrap;
}

.event-frame__titles small {
  display: flex;
  min-height: 18px;
  align-items: center;
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--text-muted, #71809c);
  font-size: var(--font-caption, 11px);
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-frame__status {
  flex: 0 0 auto;
  margin-left: auto;
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--event);
  background: color-mix(in srgb, var(--event) 8%, transparent);
  font: 700 8.5px/1.3 var(--mono, monospace);
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
  padding: 1px 14px 13px;
  min-width: 0;
  overflow-wrap: anywhere;
}

@keyframes event-frame-in {
  from {
    opacity: 0;
    transform: translateY(7px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
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

.event-frame-expand-enter-active,
.event-frame-expand-leave-active {
  max-height: min(70vh, 720px);
  overflow: hidden;
  transition:
    max-height 230ms cubic-bezier(.16, 1, .3, 1),
    opacity 160ms ease,
    transform 220ms cubic-bezier(.16, 1, .3, 1);
  will-change: max-height, opacity, transform;
}

.event-frame-expand-enter-from,
.event-frame-expand-leave-to {
  max-height: 0;
  opacity: 0;
  transform: translateY(-5px);
}

.event-frame-expand-enter-to,
.event-frame-expand-leave-from {
  max-height: min(70vh, 720px);
  opacity: 1;
  transform: translateY(0);
}

@media (prefers-reduced-motion: reduce) {
  .event-frame__surface,
  .event-frame__chevron,
  .event-frame__expand,
  .event-frame__body { transition: none; }
  .event-frame__surface { animation: none; }
  .event-frame__status.is-running { animation: none; }
  .event-frame:has(.event-frame__status.is-running) .event-frame__node { animation: none; }
}
</style>
