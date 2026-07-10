<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

const props = defineProps({
  files: { type: Array, default: () => [] },
  searchText: { type: String, default: '' },
  visible: { type: Boolean, default: false },
  top: { type: Number, default: 0 },
  left: { type: Number, default: 0 },
})

const emit = defineEmits(['select', 'close'])

const selectedIndex = ref(0)
const listRef = ref(null)

const results = computed(() => {
  const query = props.searchText.toLowerCase().replace(/\s+/g, '')
  if (!query) return props.files.slice(0, 20)
  const scored = []
  for (const file of props.files) {
    const s = fuzzyScore(query, file.path.toLowerCase())
    if (s >= 0) scored.push({ ...file, _score: s })
  }
  scored.sort((a, b) => b._score - a._score)
  return scored.slice(0, 20)
})

function fuzzyScore(query, target) {
  let qi = 0, score = 0, lastMatch = -1
  for (let ti = 0; ti < target.length && qi < query.length; ti++) {
    if (target[ti] === query[qi]) {
      const gap = lastMatch >= 0 ? ti - lastMatch - 1 : 0
      score += gap === 0 ? 200 : Math.max(100 - gap * 10, 10)
      if (ti === 0 || target[ti - 1] === '/') score += 80
      lastMatch = ti
      qi++
    }
  }
  return qi === query.length ? score : -1
}

function highlightChars(text, query) {
  if (!query) return [{ text, hl: false }]
  const parts = []
  let ti = 0
  const q = query.toLowerCase()
  const t = text.toLowerCase()
  for (let qi = 0; qi < q.length; qi++) {
    const idx = t.indexOf(q[qi], ti)
    if (idx === -1) break
    if (idx > ti) parts.push({ text: text.slice(ti, idx), hl: false })
    parts.push({ text: text[idx], hl: true })
    ti = idx + 1
  }
  if (ti < text.length) parts.push({ text: text.slice(ti), hl: false })
  return parts.length ? parts : [{ text, hl: false }]
}

const itemHeight = 28
const scrollPadding = 4

function scrollToSelected() {
  nextTick(() => {
    const list = listRef.value
    if (!list) return
    const item = list.children[selectedIndex.value]
    if (!item) return
    const listTop = list.scrollTop
    const listBottom = listTop + list.clientHeight
    const itemTop = item.offsetTop
    const itemBottom = itemTop + item.offsetHeight
    if (itemTop < listTop + scrollPadding) {
      list.scrollTop = itemTop - scrollPadding
    } else if (itemBottom > listBottom - scrollPadding) {
      list.scrollTop = itemBottom - list.clientHeight + scrollPadding
    }
  })
}

function onKeydown(e) {
  if (!props.visible) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    selectedIndex.value = Math.min(selectedIndex.value + 1, results.value.length - 1)
    scrollToSelected()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    selectedIndex.value = Math.max(selectedIndex.value - 1, 0)
    scrollToSelected()
  } else if (e.key === 'Enter' || e.key === 'Tab') {
    e.preventDefault()
    if (results.value[selectedIndex.value]) {
      emit('select', results.value[selectedIndex.value].path)
    }
  } else if (e.key === 'Escape') {
    e.preventDefault()
    emit('close')
  }
}

function selectItem(index) {
  selectedIndex.value = index
  emit('select', results.value[index].path)
}

watch(() => props.searchText, () => { selectedIndex.value = 0 })
watch(() => props.visible, (v) => { if (v) selectedIndex.value = 0 })

onMounted(() => document.addEventListener('keydown', onKeydown, true))
onUnmounted(() => document.removeEventListener('keydown', onKeydown, true))
</script>

<template>
  <Teleport to="body">
    <div v-if="visible && results.length" class="mention-dropdown" ref="listRef" :style="{ top: top + 'px', left: left + 'px' }">
      <button
        v-for="(file, i) in results"
        :key="file.path"
        type="button"
        class="mention-item"
        :class="{ 'is-selected': i === selectedIndex, 'is-dir': file.type === 'dir' }"
        @click="selectItem(i)"
        @pointerenter="selectedIndex = i"
      >
        <span class="mention-icon">{{ file.type === 'dir' ? '▸' : '' }}</span>
        <span class="mention-path">
          <template v-for="(part, pi) in highlightChars(file.path, searchText)" :key="pi">
            <mark v-if="part.hl">{{ part.text }}</mark>
            <span v-else>{{ part.text }}</span>
          </template>
        </span>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.mention-dropdown {
  position: fixed;
  z-index: 9999;
  max-height: 320px;
  min-width: 260px;
  overflow-y: auto;
  padding: 4px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 8px;
  background: var(--bg-raised, #ffffff);
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.12), 0 2px 8px rgba(0, 0, 0, 0.06);
}
.mention-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 28px;
  padding: 0 8px;
  border: 0;
  border-radius: 5px;
  color: var(--text, #3f5274);
  background: transparent;
  font: 11px/1 var(--mono, monospace);
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
}
.mention-item.is-selected {
  color: var(--accent-text, #1748a3);
  background: var(--accent-bg, rgba(23, 86, 209, 0.08));
}
.mention-item mark {
  color: inherit;
  background: none;
  font-weight: 700;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.mention-icon {
  width: 14px;
  flex-shrink: 0;
  color: var(--text-muted, #71809c);
  font-size: 10px;
  text-align: center;
}
.mention-item.is-dir .mention-icon { color: var(--accent-text, #1748a3); }
.mention-path { overflow: hidden; text-overflow: ellipsis; }
</style>
