<script setup>
import { computed } from 'vue'

const props = defineProps({
  title: { type: String, required: true },
  currentLabel: { type: String, required: true },
  targetLabel: { type: String, required: true },
  detail: { type: String, default: '' },
  available: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  progress: { type: Number, default: 0 },
  state: { type: String, default: 'idle' },
  message: { type: String, default: '' },
})

const emit = defineEmits(['start', 'restart'])
const running = computed(() => props.state === 'running')
const done = computed(() => props.state === 'done')
const failed = computed(() => props.state === 'error')
const progressStyle = computed(() => ({ '--progress': `${Math.min(100, Math.max(0, props.progress))}%` }))
</script>

<template>
  <article
    class="update-row"
    :class="{ 'is-running': running, 'is-done': done, 'is-error': failed, 'is-disabled': disabled }"
    :style="progressStyle"
  >
    <div class="update-row__top">
      <h3>{{ title }}</h3>
      <span class="update-row__state">{{ available ? 'Update available' : 'Current' }}</span>
    </div>

    <div class="update-row__stage">
      <div class="update-row__version">
        <span class="update-row__track" aria-hidden="true">
          <span class="update-row__track-target">{{ targetLabel }}</span>
        </span>
        <span v-if="done" class="update-row__shine" aria-hidden="true"></span>
        <span v-if="done" class="update-row__particles" aria-hidden="true">
          <span v-for="index in 10" :key="index" :style="{ '--i': index }"></span>
        </span>
        <span class="update-row__current">{{ currentLabel }}</span>
        <button
          class="update-row__arrow"
          type="button"
          :disabled="!available || disabled || running || done"
          aria-label="Start update"
          @click="emit('start')"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        </button>
      </div>
      <button v-if="done" class="update-row__restart" type="button" @click="emit('restart')">Restart</button>
    </div>

    <p class="update-row__detail">{{ message || detail }}</p>
  </article>
</template>

<style scoped>
.update-row {
  position: relative;
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-raised);
}

.update-row.is-disabled {
  opacity: 0.55;
}

.update-row__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.update-row__top h3 {
  margin: 0;
  color: var(--text-h);
  font: 560 13px/1.2 var(--heading);
}

.update-row__state {
  color: var(--text-muted);
  font: 10px/1 var(--mono);
}

.update-row__stage {
  display: flex;
  align-items: center;
  gap: 10px;
}

.update-row__version {
  position: relative;
  display: flex;
  min-width: 0;
  min-height: 34px;
  flex: 1;
  align-items: center;
  gap: 8px;
  overflow: hidden;
  padding: 0 4px;
}

.update-row__current {
  position: relative;
  z-index: 3;
  font: 12px/1 var(--mono);
  transition: opacity 180ms ease, transform 180ms ease;
  white-space: nowrap;
}

.update-row__current {
  color: var(--text-h);
}

.update-row__arrow,
.update-row__restart {
  flex: 0 0 auto;
  cursor: pointer;
}

.update-row__arrow {
  position: relative;
  z-index: 4;
  display: grid;
  width: 27px;
  height: 27px;
  padding: 0;
  place-items: center;
  border: 1px solid var(--accent-border);
  border-radius: 50%;
  color: #ffffff;
  background: var(--accent);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.2);
  transition:
    left 260ms cubic-bezier(0.16, 1, 0.3, 1),
    transform 260ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 220ms ease;
}

.update-row__arrow:disabled {
  cursor: default;
  opacity: 0.38;
}

.update-row__track {
  position: absolute;
  top: 50%;
  left: 0;
  z-index: 1;
  width: var(--progress);
  height: 1.1em;
  overflow: hidden;
  border: 1px solid rgba(23, 86, 209, 0.34);
  border-radius: 999px;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.28), transparent 28%),
    var(--accent);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12);
  opacity: 0;
  pointer-events: none;
  transform: translateY(-50%);
  transition: width 260ms cubic-bezier(0.16, 1, 0.3, 1);
}

.update-row__track-target {
  display: flex;
  height: 100%;
  align-items: center;
  padding-left: 40px;
  color: #ffffff;
  font: 12px/1 var(--mono);
  white-space: nowrap;
}

.update-row.is-running .update-row__version {
  padding-left: 4px;
}

.update-row.is-running .update-row__current {
  opacity: 0.8;
  transform: scale(0.96);
}

.update-row.is-running .update-row__track,
.update-row.is-done .update-row__track {
  opacity: 1;
}

.update-row.is-running .update-row__arrow {
  position: absolute;
  left: clamp(0px, calc(var(--progress) - 14px), calc(100% - 27px));
  transform: rotate(90deg) scale(0.86);
}

.update-row.is-running .update-row__arrow::before {
  content: "";
  position: absolute;
  right: 24px;
  width: 48px;
  height: 12px;
  border-radius: 999px;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.58));
  filter: blur(5px);
}

.update-row.is-done .update-row__current {
  display: none;
}

.update-row.is-done .update-row__track-target {
  animation: update-label-flash 520ms ease both;
}

.update-row.is-done .update-row__track {
  width: 100%;
  background: var(--accent);
}

.update-row.is-done .update-row__arrow {
  position: absolute;
  left: calc(100% - 27px);
  opacity: 1;
  animation: update-knob-finish 300ms cubic-bezier(0.55, 0, 1, 0.45) both;
}

.update-row__shine {
  position: absolute;
  top: 50%;
  left: 0;
  z-index: 3;
  width: 100%;
  height: 1.1em;
  border-radius: 999px;
  pointer-events: none;
  transform: translateY(-50%);
}

.update-row__shine::before {
  content: "";
  position: absolute;
  width: 26px;
  height: 2px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.96);
  animation: update-edge-shine 900ms ease both;
}

.update-row__particles {
  position: absolute;
  left: calc(100% - 16px);
  top: 50%;
  z-index: 5;
}

.update-row__particles span {
  position: absolute;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
  transform: rotate(calc(var(--i) * 36deg)) translateX(0);
  animation: update-particle 480ms ease-out both;
}

.update-row__restart {
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  color: #ffffff;
  background: var(--accent);
  font: 11px/1 var(--mono);
}

.update-row__detail {
  display: -webkit-box;
  min-height: 32px;
  margin: 9px 0 0;
  overflow: hidden;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.update-row.is-error {
  border-color: var(--err-border);
}

.update-row.is-error .update-row__detail {
  color: var(--err);
}

@keyframes update-label-flash {
  0%, 100% { opacity: 1; }
  45% { opacity: 0.42; }
}

@keyframes update-knob-finish {
  0% { opacity: 0.72; transform: rotate(90deg) scale(0.86); }
  34% { opacity: 0.84; transform: rotate(450deg) scale(1.12); }
  100% { opacity: 0; transform: rotate(1890deg) scale(2); }
}

@keyframes update-edge-shine {
  0% { left: 0; top: 0; transform: rotate(0deg); }
  25% { left: calc(100% - 26px); top: 0; transform: rotate(0deg); }
  50% { left: calc(100% - 26px); top: calc(100% - 2px); transform: rotate(90deg); }
  75% { left: 0; top: calc(100% - 2px); transform: rotate(180deg); }
  100% { left: 0; top: 0; transform: rotate(270deg); }
}

@keyframes update-particle {
  0% { opacity: 1; transform: rotate(calc(var(--i) * 36deg)) translateX(0) scale(1); }
  100% { opacity: 0; transform: rotate(calc(var(--i) * 36deg)) translateX(34px) scale(0.3); }
}

@media (prefers-reduced-motion: reduce) {
  .update-row__arrow,
  .update-row__current,
  .update-row__track,
  .update-row__shine::before,
  .update-row__particles span {
    animation: none;
    transition: none;
  }
}

@media (max-width: 620px) {
  .update-row__stage {
    align-items: stretch;
    flex-direction: column;
  }

  .update-row__restart {
    width: 100%;
  }
}
</style>
