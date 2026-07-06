<script setup>
import { computed, onUnmounted, reactive, ref } from 'vue'
import FilePreviewPopover from './FilePreviewPopover.vue'

const previewCache = new Map()

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

const referenceRef = ref(null)
const visible = ref(false)
const loading = ref(false)
const preview = ref(null)
const error = ref('')
const position = reactive({ left: 12, top: 12, width: 560 })
let showTimer
let hideTimer
let controller

function placePreview() {
  const bounds = referenceRef.value?.getBoundingClientRect()
  if (!bounds) return
  const width = Math.min(600, window.innerWidth - 24)
  position.width = width
  position.left = Math.max(12, Math.min(bounds.left, window.innerWidth - width - 12))
  position.top = window.innerHeight - bounds.bottom >= 390
    ? bounds.bottom + 8
    : Math.max(12, bounds.top - 398)
}

async function loadPreview() {
  if (previewCache.has(props.path)) {
    preview.value = await previewCache.get(props.path)
    return
  }
  controller?.abort()
  controller = new AbortController()
  const request = fetch('/api/files/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: props.path }),
    signal: controller.signal,
  }).then(async response => {
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || `Preview failed (${response.status})`)
    return data
  })
  previewCache.set(props.path, request)
  try {
    preview.value = await request
  } catch (reason) {
    previewCache.delete(props.path)
    if (reason.name !== 'AbortError') error.value = reason.message
  }
}

function scheduleShow() {
  clearTimeout(hideTimer)
  clearTimeout(showTimer)
  showTimer = setTimeout(async () => {
    placePreview()
    visible.value = true
    loading.value = true
    error.value = ''
    await loadPreview()
    loading.value = false
  }, 240)
}

function keepOpen() {
  clearTimeout(hideTimer)
}

function scheduleHide() {
  clearTimeout(showTimer)
  hideTimer = setTimeout(() => { visible.value = false }, 140)
}

onUnmounted(() => {
  clearTimeout(showTimer)
  clearTimeout(hideTimer)
  controller?.abort()
})
</script>

<template>
  <span
    ref="referenceRef"
    class="file-reference"
    tabindex="0"
    @pointerenter="scheduleShow"
    @pointerleave="scheduleHide"
    @focusin="scheduleShow"
    @focusout="scheduleHide"
  >
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
  <Teleport to="body">
    <Transition name="preview-pop">
      <FilePreviewPopover
        v-if="visible"
        :path="path"
        :preview="preview"
        :loading="loading"
        :error="error"
        :position="position"
        @enter="keepOpen"
        @leave="scheduleHide"
      />
    </Transition>
  </Teleport>
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
  font: 500 10px/1 var(--mono, monospace);
  white-space: nowrap;
  cursor: default;
}
.file-reference:hover,.file-reference:focus-visible { border-color: var(--accent-border, #8eaddf); background: var(--accent-bg, #eaf1ff); }
.file-reference__ext { color: var(--accent-text, #1756d1); font-size: 9px; font-weight: 750; text-transform: lowercase; }
.file-reference__path { overflow: hidden; text-overflow: ellipsis; }
.file-reference__remove { display: grid; width: 14px; height: 14px; padding: 0; place-items: center; border: 0; border-radius: 50%; color: var(--text-muted, #7b8ca2); background: transparent; cursor: pointer; }
.file-reference__remove:hover { color: var(--err, #d92d3d); background: var(--err-bg, #fff0f1); }
.file-reference__remove svg { width: 10px; height: 10px; fill: none; stroke: currentColor; stroke-width: 2; }
.preview-pop-enter-active,.preview-pop-leave-active { transition: opacity .16s ease, transform .2s cubic-bezier(.22,1,.36,1); }
.preview-pop-enter-from,.preview-pop-leave-to { opacity: 0; transform: translateY(5px) scale(.985); }
</style>
