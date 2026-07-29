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
  width: 48px;
  flex-direction: column;
  align-items: flex-end;
  padding: 0;
  border-left: 1px solid rgba(129, 151, 184, 0.24);
  background: transparent;
  box-shadow: -10px 0 30px rgba(31, 67, 119, 0.09);
}

.inspector-rail__item {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 34px;
  align-items: center;
  justify-content: end;
  gap: 8px;
  width: 48px;
  flex: 1 1 0;
  min-height: 58px;
  overflow: hidden;
  align-self: flex-end;
  padding: 0 7px 0 15px;
  border: 0;
  border-left: 4px solid var(--tab-color);
  border-right: 0;
  border-radius: 13px 0 0 13px;
  color: #253d5d;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--tab-color) 16%, transparent) 0, rgba(255, 255, 255, 0.52) 58%);
  backdrop-filter: blur(14px);
  cursor: pointer;
  transform-origin: right center;
  transition:
    width 240ms cubic-bezier(0.34, 1.56, 0.64, 1),
    background 160ms ease,
    box-shadow 160ms ease,
    filter 160ms ease;
}

.inspector-rail__item + .inspector-rail__item {
  margin-top: 1px;
}

.inspector-rail__item::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 20px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.36), transparent 42%),
    var(--tab-color);
  opacity: 0.9;
}

.inspector-rail__item::after {
  content: "";
  position: absolute;
  inset: 10px 0 10px auto;
  width: 1px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: -1px 0 0 rgba(39, 66, 95, 0.08);
}

.inspector-rail__item:hover,
.inspector-rail__item:focus-visible {
  width: 154px;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--tab-color) 20%, #ffffff) 0, var(--tab-soft) 62%);
  box-shadow:
    -14px 0 30px rgba(31, 67, 119, 0.13),
    inset 0 0 0 1px color-mix(in srgb, var(--tab-color) 22%, transparent);
  filter: saturate(1.08);
}

.inspector-rail__item.is-active {
  color: #10233f;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--tab-color) 22%, #ffffff) 0, var(--tab-soft) 64%);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--tab-color) 28%, transparent),
    -8px 0 20px rgba(31, 67, 119, 0.1);
}

.inspector-rail__icon {
  display: grid;
  position: relative;
  z-index: 1;
  grid-column: 2;
  width: 34px;
  height: 34px;
  place-items: center;
  color: #ffffff;
  border-radius: 11px;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.22), transparent 45%),
    var(--tab-color);
  font: 800 10px/1 var(--mono);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.28),
    0 8px 18px color-mix(in srgb, var(--tab-color) 24%, transparent);
}

.inspector-rail__label {
  position: relative;
  z-index: 1;
  grid-column: 1;
  grid-row: 1;
  justify-self: end;
  white-space: nowrap;
  color: #203956;
  font: 760 10px/1 var(--mono);
  opacity: 0;
  transform: translateX(10px);
  transition: opacity 150ms ease, transform 180ms ease;
}

.inspector-rail__item:hover .inspector-rail__label,
.inspector-rail__item:focus-visible .inspector-rail__label {
  opacity: 1;
  transform: translateX(0);
}

.inspector-rail__count {
  position: absolute;
  z-index: 2;
  top: calc(50% - 24px);
  right: 5px;
  min-width: 15px;
  height: 15px;
  padding: 0 4px;
  border-radius: 999px;
  color: #ffffff;
  background: var(--tab-color);
  font: 800 8px/15px var(--mono);
  text-align: center;
  opacity: 0;
  transform: scale(0.82);
  transition: opacity 150ms ease, transform 180ms ease;
}

.inspector-rail__item:hover .inspector-rail__count,
.inspector-rail__item:focus-visible .inspector-rail__count {
  opacity: 1;
  transform: scale(1);
}

@media (prefers-reduced-motion: reduce) {
  .inspector-rail__item,
  .inspector-rail__label,
  .inspector-rail__count {
    transition: none;
  }
}
</style>
