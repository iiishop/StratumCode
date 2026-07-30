<script setup>
const props = defineProps({
  tabs: { type: Array, default: () => [] },
  activeTab: { type: String, default: null },
})
const emit = defineEmits(['select'])
</script>

<template>
  <nav class="inspector-rail" aria-label="Inspector sections">
    <button
      v-for="tab in props.tabs"
      :key="tab.id"
      class="inspector-rail__item"
      :class="{ 'is-active': tab.id === props.activeTab }"
      :style="{ '--tab-color': tab.color, '--tab-soft': tab.soft }"
      type="button"
      :aria-label="tab.label"
      :title="tab.label"
      @click="emit('select', tab.id)"
    >
      <span class="inspector-rail__icon" aria-hidden="true">{{ tab.icon }}</span>
      <span class="inspector-rail__label">{{ tab.label }}</span>
      <span v-if="tab.count" class="inspector-rail__count">{{ tab.count }}</span>
    </button>
  </nav>
</template>

<style scoped>
.inspector-rail {
  position: absolute;
  inset: 0 0 0 auto;
  z-index: 31;
  display: flex;
  width: 52px;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  padding: 0;
  border-left: 1px solid rgba(129, 151, 184, 0.18);
  background: transparent;
  box-shadow: -8px 0 24px rgba(22, 53, 98, 0.06);
}

.inspector-rail__item {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 36px;
  align-items: center;
  justify-content: end;
  gap: 10px;
  width: 52px;
  flex: 1 1 0;
  min-height: 54px;
  overflow: hidden;
  align-self: flex-end;
  padding: 0 8px 0 16px;
  border: 0;
  border-radius: 14px 0 0 14px;
  color: #1e3a5f;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--tab-color) 10%, transparent) 0, rgba(255, 255, 255, 0.38) 64%);
  backdrop-filter: blur(18px) saturate(1.05);
  -webkit-backdrop-filter: blur(18px) saturate(1.05);
  cursor: pointer;
  transform-origin: right center;
  transition:
    width 260ms cubic-bezier(0.28, 1.4, 0.55, 1),
    background 200ms ease,
    box-shadow 200ms ease,
    filter 200ms ease,
    border-radius 200ms ease;
}

.inspector-rail__item::before {
  content: "";
  position: absolute;
  inset: 4px 1px 4px 2px;
  border-radius: 11px 0 0 11px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.42), transparent 46%),
    linear-gradient(270deg, transparent 30%, var(--tab-soft) 100%);
  opacity: 0.78;
  transition: opacity 200ms ease;
}

.inspector-rail__item::after {
  content: "";
  position: absolute;
  inset: 12px 1px 12px auto;
  width: 1.5px;
  border-radius: 1px;
  background: linear-gradient(180deg, transparent, rgba(255, 255, 255, 0.72) 30%, rgba(255, 255, 255, 0.72) 70%, transparent);
  transition: opacity 200ms ease;
}

.inspector-rail__item:hover,
.inspector-rail__item:focus-visible {
  width: 162px;
  border-radius: 14px 0 0 14px;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--tab-color) 16%, #ffffff) 0, var(--tab-soft) 58%);
  box-shadow:
    -16px 0 36px rgba(22, 53, 98, 0.1),
    inset 0 0 0 1px color-mix(in srgb, var(--tab-color) 16%, transparent);
  filter: saturate(1.06);
}

.inspector-rail__item:hover::before,
.inspector-rail__item:focus-visible::before {
  opacity: 1;
}

.inspector-rail__item:active {
  transform: scale(0.98);
  transition: transform 80ms ease;
}

.inspector-rail__item:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--tab-color) 60%, transparent);
  outline-offset: -2px;
}

.inspector-rail__item.is-active {
  color: #0d2340;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--tab-color) 20%, #ffffff) 0, var(--tab-soft) 62%);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--tab-color) 24%, transparent),
    -10px 0 24px rgba(22, 53, 98, 0.08);
}

.inspector-rail__item.is-active::before {
  opacity: 1;
}

.inspector-rail__item.is-active::after {
  background: linear-gradient(180deg, transparent, color-mix(in srgb, var(--tab-color) 55%, transparent) 30%, color-mix(in srgb, var(--tab-color) 55%, transparent) 70%, transparent);
}

.inspector-rail__icon {
  display: grid;
  position: relative;
  z-index: 1;
  grid-column: 2;
  width: 36px;
  height: 36px;
  place-items: center;
  color: #ffffff;
  border-radius: 12px;
  background:
    linear-gradient(145deg, rgba(255, 255, 255, 0.26), transparent 50%),
    var(--tab-color);
  font: 800 10.5px/1 var(--mono);
  letter-spacing: 0.02em;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.32),
    0 6px 16px color-mix(in srgb, var(--tab-color) 28%, transparent);
  transition:
    transform 220ms cubic-bezier(0.28, 1.4, 0.55, 1),
    box-shadow 200ms ease;
}

.inspector-rail__item:hover .inspector-rail__icon,
.inspector-rail__item:focus-visible .inspector-rail__icon {
  transform: scale(1.06);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.36),
    0 10px 24px color-mix(in srgb, var(--tab-color) 34%, transparent);
}

.inspector-rail__item.is-active .inspector-rail__icon {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 8px 22px color-mix(in srgb, var(--tab-color) 38%, transparent),
    0 0 0 3px color-mix(in srgb, var(--tab-color) 14%, transparent);
}

.inspector-rail__label {
  position: relative;
  z-index: 1;
  grid-column: 1;
  grid-row: 1;
  justify-self: end;
  white-space: nowrap;
  color: #1a3252;
  font: 650 10.5px/1 var(--mono);
  letter-spacing: 0.03em;
  opacity: 0;
  transform: translateX(14px);
  transition:
    opacity 180ms ease 40ms,
    transform 220ms cubic-bezier(0.16, 1, 0.3, 1) 40ms;
}

.inspector-rail__item:hover .inspector-rail__label,
.inspector-rail__item:focus-visible .inspector-rail__label {
  opacity: 1;
  transform: translateX(0);
  transition:
    opacity 160ms ease 60ms,
    transform 200ms cubic-bezier(0.16, 1, 0.3, 1) 60ms;
}

.inspector-rail__count {
  position: absolute;
  z-index: 3;
  top: 6px;
  right: 10px;
  min-width: 16px;
  height: 16px;
  padding: 0 5px;
  border-radius: 999px;
  color: #ffffff;
  background: var(--tab-color);
  font: 700 9px/16px var(--mono);
  text-align: center;
  box-shadow: 0 2px 8px color-mix(in srgb, var(--tab-color) 36%, transparent);
  opacity: 0;
  transform: scale(0.75);
  transition:
    opacity 160ms ease,
    transform 200ms cubic-bezier(0.28, 1.4, 0.55, 1);
}

.inspector-rail__item:hover .inspector-rail__count,
.inspector-rail__item:focus-visible .inspector-rail__count {
  opacity: 1;
  transform: scale(1);
}

@media (prefers-reduced-motion: reduce) {
  .inspector-rail__item,
  .inspector-rail__label,
  .inspector-rail__count,
  .inspector-rail__icon {
    transition-duration: 0.01ms !important;
  }

  .inspector-rail__item:active {
    transform: none;
  }
}
</style>
