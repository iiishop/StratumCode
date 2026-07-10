<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { gsap } from 'gsap'
import { animate, stagger } from 'animejs'
import ShuffleText from './ShuffleText.vue'

const props = defineProps({
  active: { type: String, default: 'home' },
  workspaces: { type: Array, default: () => [] },
  activeWorkspace: { type: Object, default: null },
  sessions: { type: Array, default: () => [] },
  activeSession: { type: Object, default: null },
  workspaceError: { type: String, default: '' },
})
const emit = defineEmits([
  'navigate',
  'add-workspace',
  'create-session',
  'workspace-session',
  'open-session',
  'rename-session',
  'delete-session',
  'delete-workspace',
])

const sidebarRef = ref(null)
const collapseIconRef = ref(null)
const collapsed = ref(false)
const compactLayout = ref(false)
const showContent = ref(true)
let transitionTimeline
let railAnimation
let motion

const nav = [
  {
    id: 'home',
    label: 'Workspace',
    detail: 'Agent sessions',
    icon: 'M4 5h16v14H4z M8 9h8 M8 13h5',
  },
  {
    id: 'providers',
    label: 'Providers',
    detail: 'Models and endpoints',
    icon: 'M5 7h14 M5 12h14 M5 17h14 M8 5v4 M16 10v4 M11 15v4',
  },
  {
    id: 'mcp',
    label: 'MCP',
    detail: 'External tools',
    icon: 'M7 8h10M7 12h10M7 16h10M4 5h16v14H4z',
  },
  {
    id: 'lsp',
    label: 'LSP',
    detail: 'Language intelligence',
    icon: 'M6 5h12v14H6z M9 9h6 M9 13h4 M3 8h3 M18 8h3 M3 16h3 M18 16h3',
  },
  {
    id: 'settings',
    label: 'Settings',
    detail: 'Agent behavior',
    icon: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M4 12h3 M17 12h3 M12 4v3 M12 17v3',
  },
]

function navigate(id) {
  emit('navigate', id)
}

function startSession() {
  emit('create-session', props.activeWorkspace?.id)
}

function workspaceInitials(workspace) {
  const name = (workspace?.name || workspace?.path || 'WS').split(/[\\/]/).filter(Boolean).pop() || 'WS'
  return name.slice(0, 2).toUpperCase()
}

function sessionDetail(session) {
  const total = session?.usage?.total_tokens || 0
  return total ? `${total} tokens` : 'empty'
}

function handleShortcut(event) {
  if (event.ctrlKey && event.key.toLowerCase() === 'n') {
    event.preventDefault()
    startSession()
  }
}

function animateRailIcons(isCollapsing) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  railAnimation?.cancel()
  railAnimation = animate(sidebarRef.value.querySelectorAll('.sb__rail-icon'), {
    translateX: isCollapsing ? [4, 0] : [-4, 0],
    scale: [0.82, 1],
    delay: stagger(24),
    duration: 360,
    ease: 'outBack',
  })
}

async function toggleCollapse() {
  const isCollapsing = !collapsed.value
  collapsed.value = isCollapsing
  if (isCollapsing) showContent.value = false
  await nextTick()

  const duration = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 0.42
  transitionTimeline?.kill()
  transitionTimeline = gsap.timeline({ defaults: { overwrite: 'auto' } })
    .to(sidebarRef.value, {
      width: isCollapsing ? 72 : 252,
      duration,
      ease: 'power3.inOut',
    }, 0)
    .to(collapseIconRef.value, {
      rotation: isCollapsing ? 180 : 0,
      duration: duration * 0.72,
      ease: 'power2.inOut',
      transformOrigin: '50% 50%',
    }, 0)

  if (isCollapsing) {
    transitionTimeline.call(() => {
      compactLayout.value = true
    }, null, duration * 0.84)
  } else {
    transitionTimeline.call(() => {
      compactLayout.value = false
    }, null, duration * 0.16)
    transitionTimeline.call(() => {
      showContent.value = true
    }, null, duration * 0.38)
  }

  animateRailIcons(isCollapsing)
}

onMounted(() => {
  window.addEventListener('keydown', handleShortcut)
  motion = gsap.matchMedia()
  motion.add('(prefers-reduced-motion: no-preference)', () => {
    gsap.fromTo(
      sidebarRef.value.querySelectorAll('.sb__reveal'),
      { x: -8, autoAlpha: 0 },
      { x: 0, autoAlpha: 1, duration: 0.36, stagger: 0.045, ease: 'power2.out', delay: 0.08 },
    )
  })
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleShortcut)
  transitionTimeline?.kill()
  railAnimation?.cancel()
  motion?.revert()
})
</script>

<template>
  <aside ref="sidebarRef" class="sb" :class="{ 'is-compact': compactLayout }" aria-label="Primary navigation">
    <header class="sb__header">
      <button class="sb__brand" type="button" aria-label="Open workspace" @click="navigate('home')">
        <div class="sb__shuffle-logo">
          <ShuffleText
            :text="collapsed ? 'S' : 'StratumCode'"
            :duration="0.56"
            :stagger="0.035"
          />
        </div>
      </button>

      <button
        class="sb__collapse"
        type="button"
        :aria-label="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
        :title="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
        @click="toggleCollapse"
      >
        <svg ref="collapseIconRef" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
          <path d="m15 6-6 6 6 6"/>
        </svg>
      </button>
    </header>

    <button class="sb__new sb__reveal" type="button" @click="startSession">
      <span class="sb__new-icon sb__rail-icon">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
          <path d="M12 5v14M5 12h14"/>
        </svg>
      </span>
      <Transition name="sb-copy">
        <span v-if="showContent" class="sb__new-content">
          <span class="sb__new-label">New session</span>
          <kbd>Ctrl N</kbd>
        </span>
      </Transition>
    </button>

    <section class="sb__group sb__reveal">
      <Transition name="sb-copy">
        <div v-if="showContent" class="sb__group-label">Navigation</div>
      </Transition>
      <nav class="sb__nav">
        <button
          v-for="item in nav"
          :key="item.id"
          class="sb__nav-item"
          :class="{ 'is-active': active === item.id }"
          :aria-current="active === item.id ? 'page' : undefined"
          :title="collapsed ? item.label : ''"
          type="button"
          @click="navigate(item.id)"
        >
          <span class="sb__nav-icon sb__rail-icon">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round">
              <path :d="item.icon"/>
            </svg>
          </span>
          <Transition name="sb-copy">
            <span v-if="showContent" class="sb__nav-copy">
              <strong>{{ item.label }}</strong>
              <small>{{ item.detail }}</small>
            </span>
          </Transition>
        </button>
      </nav>
    </section>

    <section class="sb__group sb__reveal">
      <Transition name="sb-copy">
        <div v-if="showContent" class="sb__group-head">
          <span class="sb__group-label">Workspaces</span>
          <button type="button" title="Add workspace" @click="emit('add-workspace')">+</button>
        </div>
      </Transition>
      <button
        v-for="workspace in workspaces"
        :key="workspace.id"
        class="sb__project"
        :class="{ 'is-active': activeWorkspace?.id === workspace.id }"
        :title="collapsed ? workspace.name : workspace.path"
        type="button"
        @click="emit('workspace-session', workspace)"
      >
        <span class="sb__project-icon sb__rail-icon">
          {{ workspaceInitials(workspace) }}
        </span>
        <Transition name="sb-copy">
          <span v-if="showContent" class="sb__project-content">
            <span class="sb__project-copy">
              <strong>{{ workspace.name }}</strong>
              <small>{{ workspace.path }}</small>
            </span>
            <span class="sb__project-state">{{ workspace.is_active ? 'open' : 'new' }}</span>
          </span>
        </Transition>
      </button>
      <Transition name="sb-copy">
        <small v-if="showContent && workspaceError" class="sb__error">{{ workspaceError }}</small>
      </Transition>
    </section>

    <section class="sb__group sb__sessions sb__reveal">
      <Transition name="sb-copy">
        <div v-if="showContent" class="sb__group-label">Sessions</div>
      </Transition>
      <div
        v-for="session in sessions"
        :key="session.id"
        class="sb__session-row"
        :class="{ 'is-active': activeSession?.id === session.id }"
      >
        <button
          class="sb__session"
          type="button"
          :title="collapsed ? session.name : 'Open session'"
          @click="emit('open-session', session.id)"
        >
          <span class="sb__session-icon sb__rail-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round">
              <path d="M5 5h14v11H9l-4 3z"/>
            </svg>
          </span>
          <Transition name="sb-copy">
            <span v-if="showContent" class="sb__session-copy">
              <strong>{{ session.name }}</strong>
              <small>{{ sessionDetail(session) }}</small>
            </span>
          </Transition>
        </button>
        <Transition name="sb-copy">
          <span v-if="showContent" class="sb__session-actions">
            <button type="button" title="Rename" @click.stop="emit('rename-session', session.id)">R</button>
            <button type="button" title="Delete" @click.stop="emit('delete-session', session.id)">D</button>
          </span>
        </Transition>
      </div>
      <button v-if="!sessions.length" class="sb__session" type="button" @click="startSession">
        <span class="sb__session-icon sb__rail-icon">+</span>
        <Transition name="sb-copy">
          <span v-if="showContent" class="sb__session-copy">
            <strong>No sessions</strong>
            <small>Create one</small>
          </span>
        </Transition>
      </button>
    </section>

    <footer class="sb__footer sb__reveal">
      <div class="sb__runtime" title="Local runtime ready">
        <span class="sb__runtime-icon sb__rail-icon"><i></i></span>
        <Transition name="sb-copy" mode="out-in">
          <span v-if="showContent" key="runtime-full" class="sb__runtime-copy">
            <strong>Local runtime</strong>
            <small>Ready</small>
          </span>
          <span v-else key="runtime-short" class="sb__runtime-short">OK</span>
        </Transition>
      </div>
    </footer>
  </aside>
</template>

<style scoped>
.sb {
  display: flex;
  width: 252px;
  min-width: 72px;
  height: 100svh;
  flex: 0 0 auto;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid #0d3b9b;
  color: #dbe8ff;
  background: #1248b8;
  box-shadow: 8px 0 28px rgba(18, 72, 184, 0.18);
  z-index: 20;
}

.sb__header {
  display: flex;
  min-height: 68px;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.14);
}

.sb__brand {
  display: flex;
  width: 100%;
  height: 40px;
  flex: 1 1 auto;
  min-width: 0;
  align-items: center;
  gap: 10px;
  padding: 0;
  border: 0;
  color: inherit;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.sb__logo {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  place-items: center;
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 9px;
  color: #38bdf8;
  background: #0b1424;
  font: 700 12px/1 var(--mono);
  letter-spacing: -0.06em;
  text-shadow: 0 0 8px rgba(56, 189, 248, 0.4);
}

.sb__shuffle-logo {
  display: flex;
  width: 100%;
  height: 40px;
  min-width: 0;
  flex: 1 1 auto;
  align-items: center;
  overflow: hidden;
}

.is-compact .sb__shuffle-logo :deep(.shuffle-text) {
  color: var(--yellow);
  font-size: 20px;
  letter-spacing: 0;
  text-align: center;
}

.sb__brand-copy,
.sb__nav-copy,
.sb__project-copy,
.sb__session-copy,
.sb__runtime-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  overflow: hidden;
  white-space: nowrap;
  transition: opacity 120ms ease, transform 180ms ease;
}

.sb__brand-copy strong {
  color: #ffffff;
  font: 600 13px/1.2 var(--heading);
  letter-spacing: -0.02em;
}

.sb__brand-copy small {
  margin-top: 2px;
  color: #b9cff8;
  font: 8.5px/1.2 var(--mono);
}

.sb__collapse {
  display: grid;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  padding: 0;
  place-items: center;
  border: 1px solid transparent;
  border-radius: 7px;
  color: #b9cff8;
  background: transparent;
  cursor: pointer;
}

.sb__collapse:hover {
  border-color: rgba(255, 255, 255, 0.25);
  color: #ffffff;
  background: rgba(255, 255, 255, 0.1);
}

.sb__new {
  display: flex;
  min-height: 38px;
  align-items: center;
  gap: 9px;
  margin: 14px 12px 8px;
  padding: 0 9px;
  overflow: hidden;
  border: 1px solid #e6bb2d;
  border-radius: var(--radius-sm);
  color: #113a88;
  background: var(--yellow);
  cursor: pointer;
  white-space: nowrap;
}

.sb__new:hover {
  border-color: #ffe98f;
  background: #ffdc5c;
}

.sb__new-icon,
.sb__nav-icon,
.sb__project-icon,
.sb__session-icon {
  display: grid;
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  place-items: center;
}

.sb__new-icon { color: #b72435; }
.sb__new-label { font-size: 11px; font-weight: 600; }

.sb__new-content {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.sb__new kbd {
  margin-left: auto;
  color: #665000;
  font: 8px/1 var(--mono);
}

.sb__group {
  padding: 12px 12px 2px;
}

.sb__group-label {
  height: 18px;
  padding: 0 7px;
  overflow: hidden;
  color: #9dbcf4;
  font: 550 9px/1 var(--mono);
  white-space: nowrap;
  transition: opacity 120ms ease;
}

.sb__group-head {
  display: flex;
  height: 18px;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sb__group-head .sb__group-label {
  padding-right: 0;
}

.sb__group-head > button {
  display: grid;
  width: 18px;
  height: 18px;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, .22);
  border-radius: 5px;
  color: #dbe8ff;
  background: transparent;
  font: 700 12px/1 var(--mono);
  cursor: pointer;
}

.sb__group-head > button:hover {
  color: #123f9d;
  background: #ffffff;
}

.sb__nav {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.sb__nav-item {
  position: relative;
  display: flex;
  min-height: 44px;
  align-items: center;
  gap: 9px;
  padding: 4px 7px;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: #d4e2ff;
  background: transparent;
  cursor: pointer;
  text-align: left;
  white-space: nowrap;
}

.sb__nav-item:hover {
  color: #ffffff;
  border-color: rgba(255, 255, 255, 0.18);
  background: rgba(255, 255, 255, 0.09);
}

.sb__nav-item.is-active {
  color: #123f9d;
  border-color: #ffffff;
  background: #ffffff;
}

.sb__nav-item.is-active::before {
  content: "";
  position: absolute;
  left: -1px;
  width: 2px;
  height: 20px;
  border-radius: 0 2px 2px 0;
  background: var(--red);
}

.sb__nav-item.is-active .sb__nav-copy small {
  color: #6179a8;
}

.sb__nav-copy strong,
.sb__project-copy strong,
.sb__session-copy strong,
.sb__runtime-copy strong {
  overflow: hidden;
  color: inherit;
  font-size: 10.5px;
  font-weight: 580;
  line-height: 1.25;
  text-overflow: ellipsis;
}

.sb__nav-copy small,
.sb__project-copy small,
.sb__session-copy small,
.sb__runtime-copy small {
  margin-top: 2px;
  overflow: hidden;
  color: #9dbcf4;
  font: 8.5px/1.2 var(--mono);
  text-overflow: ellipsis;
}

.sb__project,
.sb__session {
  display: flex;
  min-height: 42px;
  align-items: center;
  gap: 9px;
  padding: 4px 7px;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: #e0eaff;
  background: transparent;
  white-space: nowrap;
}

.sb__project {
  width: 100%;
  border-color: rgba(255, 255, 255, 0.16);
  background: rgba(6, 37, 111, 0.24);
  cursor: pointer;
  text-align: left;
}

.sb__project-icon {
  border: 1px solid rgba(245, 198, 66, .45);
  border-radius: 7px;
  color: var(--yellow);
  font: 800 8px/1 var(--mono);
}

.sb__project-copy {
  flex: 1;
}

.sb__project-content {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.sb__project-state {
  color: #b9cff8;
  font: 8px/1 var(--mono);
}

.sb__project.is-active {
  border-color: rgba(255, 255, 255, .36);
  background: rgba(255, 255, 255, .12);
}

.sb__error {
  display: block;
  margin: 7px;
  color: #ffd7de;
  font: 8.5px/1.35 var(--mono);
}

.sb__sessions {
  min-height: 0;
  flex: 1;
}

.sb__session {
  width: 100%;
  color: #dbe8ff;
  cursor: pointer;
  text-align: left;
}

.sb__session-row {
  display: flex;
  align-items: center;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
}

.sb__session-row:hover,
.sb__session-row.is-active {
  color: #ffffff;
  border-color: rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.08);
}

.sb__session-row.is-active {
  border-color: rgba(245, 198, 66, .42);
}

.sb__session-row .sb__session {
  border: 0;
  background: transparent;
}

.sb__session-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  padding-right: 5px;
}

.sb__session-actions button {
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  border: 1px solid transparent;
  border-radius: 5px;
  color: #9dbcf4;
  background: transparent;
  font: 700 8px/1 var(--mono);
  cursor: pointer;
}

.sb__session-actions button:hover {
  border-color: rgba(255, 255, 255, .2);
  color: #ffffff;
  background: rgba(255, 255, 255, .08);
}

.sb__footer {
  min-height: 58px;
  margin: 0 12px;
  padding: 9px 7px;
  border-top: 1px solid rgba(255, 255, 255, 0.14);
}

.sb__runtime {
  display: flex;
  align-items: center;
  gap: 10px;
}

.sb__runtime-icon {
  display: grid;
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.1);
}

.sb__runtime-icon i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yellow);
}

.sb__runtime-short {
  display: none;
  color: var(--yellow);
  font: 650 8px/1 var(--mono);
}

/* Compact alignment is applied only near the end of the GSAP collapse. */
.is-compact .sb__header {
  justify-content: space-between;
  gap: 2px;
  padding-inline: 8px;
}

.is-compact .sb__brand {
  width: 34px;
  flex: 0 0 34px;
}

.is-compact .sb__collapse {
  width: 20px;
  flex-basis: 20px;
  border-color: rgba(255, 255, 255, 0.2);
  background: rgba(255, 255, 255, 0.08);
}

.is-compact .sb__new {
  width: 40px;
  margin: 14px auto 8px;
  padding: 0 7px;
}

.is-compact .sb__group {
  width: 100%;
  padding-inline: 14px;
}

.is-compact .sb__nav-item,
.is-compact .sb__project,
.is-compact .sb__session,
.is-compact .sb__session-row {
  width: 42px;
  padding-inline: 8px;
}

.is-compact .sb__project {
  background: rgba(6, 37, 111, 0.2);
}

.is-compact .sb__footer {
  margin-inline: 14px;
  padding-inline: 9px;
}

.is-compact .sb__runtime {
  flex-direction: column;
  gap: 3px;
}

.is-compact .sb__runtime-short {
  display: block;
  margin-top: 0;
  text-align: center;
}

/* Vue owns copy mount/unmount; GSAP owns shell geometry. */
.sb-copy-enter-active {
  transition: opacity 180ms ease 45ms, transform 220ms cubic-bezier(0.16, 1, 0.3, 1) 45ms;
}

.sb-copy-leave-active {
  transition: opacity 105ms ease, transform 130ms ease;
}

.sb-copy-enter-from,
.sb-copy-leave-to {
  opacity: 0;
  transform: translateX(-7px);
}

@media (max-width: 760px) {
  .sb {
    width: 72px !important;
  }

  .sb__header {
    justify-content: center;
    padding-inline: 0;
  }

  .sb__brand-copy,
  .sb__new-label,
  .sb__new kbd,
  .sb__group-label,
  .sb__nav-copy,
  .sb__project-copy,
  .sb__project-state,
  .sb__session-copy,
  .sb__runtime-copy,
  .sb__collapse {
    display: none;
  }

  .sb__new {
    width: 40px;
    margin-inline: auto;
    padding-inline: 7px;
  }

  .sb__group {
    width: 100%;
    padding-inline: 14px;
  }

  .sb__nav-item,
  .sb__project,
  .sb__session,
  .sb__session-row {
    width: 42px;
    padding-inline: 8px;
  }

  .sb__project {
    background: transparent;
  }

  .sb__footer {
    margin-inline: 14px;
    padding-inline: 9px;
  }

  .sb__runtime {
    flex-direction: column;
    gap: 3px;
  }

  .sb__runtime-short {
    display: block;
    margin-top: 0;
    text-align: center;
  }
}
</style>
