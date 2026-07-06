<script setup>
import { computed } from 'vue'

const props = defineProps({
  path: { type: String, required: true },
  language: { type: String, default: '' },
  removable: { type: Boolean, default: false },
})

defineEmits(['remove'])

const extension = computed(() => {
  const value = props.language || props.path.split('.').pop() || 'file'
  return value.toLowerCase().slice(0, 2)
})
</script>

<template>
  <span class="file-reference" :title="path">
    <span class="file-reference__ext">{{ extension }}</span>
    <span class="file-reference__path">{{ path }}</span>
    <button
      v-if="removable"
      class="file-reference__remove"
      type="button"
      :aria-label="`Remove ${path}`"
      @click.stop="$emit('remove')"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>
    </button>
  </span>
</template>

<style scoped>
.file-reference {
  display: inline-flex;
  max-width: 100%;
  height: 23px;
  align-items: center;
  gap: 5px;
  padding: 0 6px;
  border: 1px solid var(--border, #cfd9e8);
  border-radius: 6px;
  color: var(--text, #344860);
  background: var(--code-bg, #f1f4f8);
  vertical-align: middle;
  font: 500 9px/1 var(--mono, monospace);
  white-space: nowrap;
}
.file-reference__ext { color: var(--accent-text, #1756d1); font-size: 8px; font-weight: 750; text-transform: lowercase; }
.file-reference__path { overflow: hidden; text-overflow: ellipsis; }
.file-reference__remove { display: grid; width: 14px; height: 14px; padding: 0; place-items: center; border: 0; border-radius: 50%; color: var(--text-muted, #7b8ca2); background: transparent; cursor: pointer; }
.file-reference__remove:hover { color: var(--err, #d92d3d); background: var(--err-bg, #fff0f1); }
.file-reference__remove svg { width: 10px; height: 10px; fill: none; stroke: currentColor; stroke-width: 2; }
</style>
