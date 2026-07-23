<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  title: { type: String, required: true },
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  empty: { type: String, default: 'No skills' },
  busy: { type: String, default: '' },
  canAdd: { type: Boolean, default: false },
})
const emit = defineEmits(['preview', 'add'])

const sortKey = ref('name')

const sorted = computed(() => {
  const items = [...props.items]
  return items.sort((a, b) => {
    if (sortKey.value === 'installed') return Number(b.installed) - Number(a.installed) || byName(a, b)
    if (sortKey.value === 'source') return sourceLabel(a).localeCompare(sourceLabel(b)) || byName(a, b)
    return byName(a, b)
  })
})

function byName(a, b) {
  return String(a.name || '').localeCompare(String(b.name || ''))
}

function addSource(item) {
  return item.package || item.url || item.path || ''
}

function sourceLabel(item) {
  return String(item.source_label || item.source || (item.installed ? 'local' : 'remote'))
}
</script>

<template>
  <section class="skill-list">
    <header class="skill-list__head">
      <div>
        <strong>{{ title }}</strong>
        <span>{{ loading ? 'loading' : `${items.length} skills` }}</span>
      </div>
      <select v-model="sortKey" title="Sort skills">
        <option value="name">Name</option>
        <option value="installed">Installed</option>
        <option value="source">Source</option>
      </select>
    </header>

    <p v-if="!sorted.length && !loading" class="skill-list__empty">{{ empty }}</p>

    <article v-for="item in sorted" :key="item.id || item.path || item.package" class="skill-item">
      <button type="button" class="skill-item__body" @click="emit('preview', item)">
        <span class="skill-item__mark">{{ (item.name || '?').slice(0, 2).toUpperCase() }}</span>
        <span class="skill-item__text">
          <strong>{{ item.name }}</strong>
          <small>{{ item.description || item.package || item.path || item.url }}</small>
        </span>
      </button>
      <span class="skill-item__meta">{{ sourceLabel(item) }}</span>
      <button
        v-if="canAdd && !item.installed"
        type="button"
        class="skill-item__add"
        :disabled="busy === addSource(item)"
        @click="emit('add', addSource(item))"
      >
        {{ busy === addSource(item) ? 'Adding' : 'Add' }}
      </button>
    </article>
  </section>
</template>

<style scoped>
.skill-list {
  min-width: 0;
  border-top: 1px solid var(--border-strong);
  padding-top: 14px;
}

.skill-list__head,
.skill-item,
.skill-item__body {
  display: flex;
  align-items: center;
}

.skill-list__head {
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.skill-list__head div {
  display: grid;
  gap: 3px;
}

.skill-list__head strong {
  color: var(--text-h);
  font-size: 13px;
}

.skill-list__head span,
.skill-item__meta,
.skill-list__empty {
  color: var(--text-muted);
  font: 9px/1 var(--mono);
}

.skill-list__head select {
  height: 28px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-h);
  background: #fff;
  font: 10px/1 var(--mono);
}

.skill-list__empty {
  margin: 18px 0;
}

.skill-item {
  gap: 10px;
  min-height: 56px;
  border: 1px solid var(--border);
  border-bottom: 0;
  background: rgba(255, 255, 255, .88);
}

.skill-item:last-child {
  border-bottom: 1px solid var(--border);
}

.skill-item:hover {
  background: #fff;
}

.skill-item__body {
  min-width: 0;
  flex: 1;
  gap: 10px;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.skill-item__mark {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  place-items: center;
  border-radius: var(--radius-sm);
  color: var(--accent-text);
  background: var(--accent-bg);
  font: 800 9px/1 var(--mono);
}

.skill-item__text {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.skill-item__text strong,
.skill-item__text small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-item__text strong {
  color: var(--text-h);
  font-size: 12px;
}

.skill-item__text small {
  color: var(--text-muted);
  font: 9px/1.35 var(--mono);
}

.skill-item__meta {
  flex: 0 0 auto;
}

.skill-item__add {
  height: 28px;
  margin-right: 10px;
  padding: 0 10px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  color: #fff;
  background: var(--accent);
  font: 650 10px/1 var(--mono);
  cursor: pointer;
}

.skill-item__add:disabled {
  opacity: .45;
  cursor: default;
}
</style>
