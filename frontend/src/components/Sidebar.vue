<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { gsap } from 'gsap'

defineProps({ active: { type: String, default: 'home' } })
const emit = defineEmits(['navigate'])

const collapsed = ref(false)
const sidebarRef = ref(null)
const itemRefs = ref([])

function setItemRef(el, i) { if (el) itemRefs.value[i] = el }

const nav = [
  { id: 'home', label: 'Home',          icon: 'M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z M9 22V12h6v10' },
  { id: 'providers', label: 'Providers', icon: 'M2 4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z M8 20h8 M12 16v4' },
]

function navigate(id) { emit('navigate', id) }

function toggleCollapse() {
  collapsed.value = !collapsed.value
  nextTick(() => {
    gsap.to(sidebarRef.value, { width: collapsed.value ? 56 : 200, duration: 0.32, ease: 'power3.inOut' })
  })
}

onMounted(() => nextTick(() => {
  const items = itemRefs.value.filter(Boolean)
  gsap.fromTo(items, { x: -12, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: 0.35, stagger: 0.05, ease: 'power2.out', delay: 0.08 })
}))
</script>

<template>
  <aside ref="sidebarRef" class="sb" :class="{ 'is-folded': collapsed }">

    <div class="sb__brand" @click="navigate('home')">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
        <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
        <line x1="12" y1="22.08" x2="12" y2="12"/>
      </svg>
      <Transition name="sb-fade">
        <span v-if="!collapsed" class="sb__brand-text">StratumCode</span>
      </Transition>
    </div>

    <nav class="sb__nav">
      <button
        v-for="(item, i) in nav" :key="item.id"
        :ref="(el) => setItemRef(el, i)"
        class="sb__item"
        :class="{ 'is-on': active === item.id }"
        @click="navigate(item.id)"
        :title="collapsed ? item.label : ''"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path :d="item.icon"/>
        </svg>
        <span class="sb__label">{{ item.label }}</span>
      </button>
    </nav>

    <button class="sb__fold" @click="toggleCollapse" :title="collapsed ? '展开侧栏' : '收起侧栏'">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
        <polyline :points="collapsed ? '9 18 15 12 9 6' : '15 18 9 12 15 6'"/>
      </svg>
    </button>

  </aside>
</template>

<style scoped>
.sb {
  display: flex;
  flex-direction: column;
  width: 200px;
  min-width: 56px;
  height: 100svh;
  padding: 14px 8px 12px;
  background: #080d17;
  border-right: 1px solid rgba(148, 163, 184, 0.08);
  overflow: hidden;
  z-index: 10;
}

/* ---- brand ---- */
.sb__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  margin-bottom: 20px;
  border-radius: 6px;
  cursor: pointer;
  color: #38bdf8;
  transition: background 0.15s;
  overflow: hidden;
  white-space: nowrap;
}
.sb__brand:hover { background: rgba(148, 163, 184, 0.06); }
.sb__brand svg { flex-shrink: 0; }
.sb__brand-text {
  font-size: 14px;
  font-weight: 600;
  color: #e2e8f0;
  letter-spacing: -0.01em;
}

/* ---- nav ---- */
.sb__nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}

.sb__item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 36px;
  padding: 0 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  overflow: hidden;
  white-space: nowrap;
  transition: background 0.12s, color 0.12s;
}
.sb__item:hover { background: rgba(148, 163, 184, 0.05); color: #94a3b8; }
.sb__item.is-on   { background: rgba(56, 189, 248, 0.12); color: #38bdf8; }
.sb__item svg { flex-shrink: 0; }

.sb__label {
  font-weight: 500;
  transition: opacity 0.2s;
}
.is-folded .sb__label { opacity: 0; pointer-events: none; }

/* ---- fold button ---- */
.sb__fold {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #475569;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
  flex-shrink: 0;
}
.sb__fold:hover { background: rgba(148, 163, 184, 0.06); color: #94a3b8; }
.sb__fold svg { transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1); }

/* ---- transitions ---- */
.sb-fade-enter-active { transition: opacity 0.22s ease, transform 0.22s ease; }
.sb-fade-leave-active { transition: opacity 0.1s ease, transform 0.1s ease; }
.sb-fade-enter-from   { opacity: 0; transform: translateX(-6px); }
.sb-fade-leave-to     { opacity: 0; transform: translateX(-6px); }
</style>
