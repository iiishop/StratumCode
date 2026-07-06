<script setup>
import { computed } from 'vue'
import { highlightCode } from '../lib/highlight'

const props = defineProps({
  path: { type: String, required: true },
  preview: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  position: { type: Object, required: true },
})

defineEmits(['enter', 'leave'])

const highlighted = computed(() => (
  props.preview ? highlightCode(props.preview.content, props.path) : ''
))
</script>

<template>
  <aside
    class="file-preview"
    :style="{ left: `${position.left}px`, top: `${position.top}px`, width: `${position.width}px` }"
    @pointerenter="$emit('enter')"
    @pointerleave="$emit('leave')"
  >
    <header class="file-preview__header">
      <span class="file-preview__mark">PREVIEW</span>
      <strong>{{ path }}</strong>
      <span v-if="preview">{{ preview.shown_lines }}/{{ preview.total_lines }} lines</span>
    </header>
    <div v-if="loading" class="file-preview__loading"><i></i><i></i><i></i><span>Reading file</span></div>
    <div v-else-if="error" class="file-preview__error">{{ error }}</div>
    <pre v-else-if="preview" class="file-preview__code"><code v-html="highlighted"></code></pre>
    <footer v-if="preview?.truncated" class="file-preview__footer">Preview truncated for performance</footer>
  </aside>
</template>

<style scoped>
.file-preview {
  position: fixed;
  z-index: 1000;
  max-height: min(390px, calc(100vh - 24px));
  overflow: hidden;
  border: 1px solid #b9c9e7;
  border-radius: 10px;
  color: #263f5e;
  background: rgba(255, 255, 255, .97);
  box-shadow: 0 20px 55px rgba(20, 55, 105, .22), 0 3px 10px rgba(20, 55, 105, .1);
  backdrop-filter: blur(14px);
}
.file-preview__header { display: flex; min-height: 39px; align-items: center; gap: 8px; padding: 7px 10px; border-bottom: 1px solid #d9e3f2; background: linear-gradient(100deg, #edf4ff, #fff); }
.file-preview__header strong { min-width: 0; flex: 1; overflow: hidden; color: #173d70; font: 600 11px/1.3 var(--mono, monospace); text-overflow: ellipsis; white-space: nowrap; }
.file-preview__header > span:last-child { color: #71809c; font: 9px/1 var(--mono, monospace); white-space: nowrap; }
.file-preview__mark { padding: 3px 5px; border-radius: 4px; color: #fff; background: #1756d1; font: 700 8px/1 var(--mono, monospace); letter-spacing: .08em; }
.file-preview__code { max-height: 326px; margin: 0; padding: 11px 13px; overflow: auto; color: #263f5e; background: #f8faff; font: var(--font-code, 12px)/1.62 var(--mono, monospace); tab-size: 2; }
.file-preview__code code { display: block; min-width: max-content; }
.file-preview__loading { display: flex; min-height: 100px; align-items: center; justify-content: center; gap: 5px; color: #71809c; font-size: 11px; }
.file-preview__loading i { width: 5px; height: 5px; border-radius: 1px; background: #1756d1; animation: preview-pulse .8s ease-in-out infinite alternate; }.file-preview__loading i:nth-child(2) { animation-delay: .12s; }.file-preview__loading i:nth-child(3) { animation-delay: .24s; }.file-preview__loading span { margin-left: 5px; }
.file-preview__error { min-height: 90px; padding: 24px; color: #b42333; font-size: 12px; }
.file-preview__footer { padding: 6px 10px; border-top: 1px solid #d9e3f2; color: #856500; background: #fff9dc; font-size: 9px; }
@keyframes preview-pulse { to { opacity: .2; transform: translateY(-3px); } }
@media (prefers-reduced-motion: reduce) { .file-preview__loading i { animation: none; } }
</style>
