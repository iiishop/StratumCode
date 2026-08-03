<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'

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

const SESSION_PAGE_SIZE = 8
const visibleCounts = ref({})
const collapsedWorkspaceId = ref(null)
const collapsed = ref(false)
const sbRef = ref(null)

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  // 过渡期间抑制 tooltip：折叠时元素滑到鼠标下方会误触发 hover 气泡
  const el = sbRef.value
  if (!el) return
  el.classList.add('is-animating')
  window.setTimeout(() => el.classList.remove('is-animating'), 340)
}

const NAV = [
  { id: 'home', label: 'All sessions', icon: 'M4 6h16v12H4z' },
  { id: 'providers', label: 'Providers', icon: 'M4 8h16M4 13h16M4 18h16' },
  { id: 'mcp', label: 'MCP', icon: 'M8 8h8M8 12h8M8 16h8 M5 5h14v14H5z' },
  { id: 'lsp', label: 'LSP', icon: 'M8 5h8v14H8z M11 10h2 M11 14h2' },
  { id: 'skills', label: 'Skills', icon: 'M5 5h14v5H5z M5 14h6v5H5z M15 14h4v5h-4z' },
  { id: 'settings', label: 'Settings', icon: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z' },
]

function createSession() {
  emit('create-session', props.activeWorkspace?.id)
}

function onKeydown(event) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'n') {
    event.preventDefault()
    createSession()
  }
}

function workspaceLabel(workspace) {
  return (workspace?.name || workspace?.path || 'Workspace').split(/[\\/]/).filter(Boolean).pop() || 'Workspace'
}

function initials(workspace) {
  return workspaceLabel(workspace).slice(0, 2).toUpperCase()
}

function tokenLabel(session) {
  const total = session?.usage?.total_tokens || 0
  if (!total) return ''
  if (total >= 1_000_000) return `${(total / 1_000_000).toFixed(1)}M`
  if (total >= 1_000) return `${(total / 1_000).toFixed(1)}k`
  return String(total)
}

function workspaceKey(workspace) {
  return workspace?.id ?? workspace?.path ?? '__active__'
}

function visibleCountFor(workspace) {
  return visibleCounts.value[workspaceKey(workspace)] ?? SESSION_PAGE_SIZE
}

function visibleSessions(workspace) {
  return props.sessions.slice(0, visibleCountFor(workspace))
}

function hasMoreSessions(workspace) {
  return props.sessions.length > visibleCountFor(workspace)
}

function showMoreSessions(workspace) {
  const key = workspaceKey(workspace)
  visibleCounts.value = {
    ...visibleCounts.value,
    [key]: visibleCountFor(workspace) + SESSION_PAGE_SIZE,
  }
}

function onWorkspaceClick(workspace) {
  if (props.activeWorkspace?.id === workspace.id) {
    collapsedWorkspaceId.value = collapsedWorkspaceId.value === workspace.id ? null : workspace.id
  } else {
    emit('workspace-session', workspace)
  }
}

watch(() => props.activeWorkspace?.id, (newId, oldId) => {
  if (newId && newId !== oldId) {
    collapsedWorkspaceId.value = newId
    const key = newId
    if (visibleCounts.value[key] !== undefined) {
      visibleCounts.value = {
        ...visibleCounts.value,
        [key]: SESSION_PAGE_SIZE,
      }
    }
  }
})

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <aside ref="sbRef" class="sb" :class="{ 'is-collapsed': collapsed }" aria-label="Primary navigation">
    <header class="sb__top">
      <button class="sb__brand" type="button" :data-tip="'StratumCode'" title="Open workspace" aria-label="Open workspace" @click="emit('navigate', 'home')">
        <span class="sb__mark">S</span>
        <Transition name="sb-fade">
          <span v-if="!collapsed">StratumCode</span>
        </Transition>
      </button>
      <button class="sb__top-action" type="button" :data-tip="'Add workspace'" title="Add workspace" aria-label="Add workspace" @click="emit('add-workspace')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
          <path d="M12 5v14M5 12h14" />
        </svg>
      </button>
      <button class="sb__collapse-toggle" type="button" :data-tip="collapsed ? 'Expand sidebar' : 'Collapse sidebar'" :title="collapsed ? 'Expand sidebar' : 'Collapse sidebar'" :aria-label="collapsed ? 'Expand sidebar' : 'Collapse sidebar'" @click="toggleCollapsed">
        <svg width="14" height="14" class="sb__collapse-icon" :class="{ 'is-collapsed': collapsed }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="m9 6 6 6-6 6" />
        </svg>
      </button>
    </header>

    <div class="sb__new-wrap">
      <button class="sb__new" type="button" :data-tip="'New session'" title="New session" @click="createSession">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
          <path d="M12 5v14M5 12h14" />
        </svg>
        <Transition name="sb-fade">
          <span v-if="!collapsed">New session</span>
        </Transition>
        <Transition name="sb-fade">
          <kbd v-if="!collapsed">Ctrl N</kbd>
        </Transition>
      </button>
    </div>

    <nav class="sb__nav" aria-label="Sections">
      <button
        v-for="item in NAV"
        :key="item.id"
        class="sb__nav-item"
        :class="{ 'is-active': active === item.id }"
        :data-tip="item.label"
        :aria-current="active === item.id ? 'page' : undefined"
        :title="item.label"
        type="button"
        @click="emit('navigate', item.id)"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
          <path :d="item.icon" />
        </svg>
        <Transition name="sb-fade">
          <span v-if="!collapsed">{{ item.label }}</span>
        </Transition>
      </button>
    </nav>

    <div class="sb__scroll">
      <section
        v-for="(workspace, index) in workspaces"
        :key="workspace.id"
        class="sb__workspace-group"
        :class="{ 'is-open': activeWorkspace?.id === workspace.id && collapsedWorkspaceId !== workspace.id }"
      >
        <div class="sb__workspace-row" :class="{ 'is-active': activeWorkspace?.id === workspace.id }">
          <button class="sb__workspace" type="button" :data-tip="workspaceLabel(workspace)" :title="collapsed ? workspaceLabel(workspace) : workspace.path" @click="onWorkspaceClick(workspace)">
            <span class="sb__avatar" :class="index % 2 ? 'is-red' : 'is-blue'">{{ initials(workspace) }}</span>
            <Transition name="sb-fade">
              <span v-if="!collapsed" class="sb__workspace-name">{{ workspaceLabel(workspace) }}</span>
            </Transition>
            <Transition name="sb-fade">
              <span v-if="!collapsed" class="sb__state" :class="{ 'is-live': workspace.is_active }">{{ workspace.is_active ? 'open' : 'idle' }}</span>
            </Transition>
            <Transition name="sb-fade">
              <svg v-if="!collapsed" class="sb__chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="m9 6 6 6-6 6" />
              </svg>
            </Transition>
          </button>
          <button
            v-if="workspaces.length > 1"
            class="sb__row-action is-danger"
            type="button"
            title="Remove workspace"
            @click.stop="emit('delete-workspace', workspace.id)"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <Transition name="sb-expand">
          <div v-if="activeWorkspace && activeWorkspace.id === workspace.id && collapsedWorkspaceId !== workspace.id" class="sb__children">
            <TransitionGroup name="sb-session" tag="div" class="sb__session-list">
              <div
                v-for="session in visibleSessions(workspace)"
                :key="session.id"
                class="sb__session-row"
                :class="{ 'is-active': activeSession?.id === session.id }"
              >
                <button class="sb__session" type="button" title="Open session" @click="emit('open-session', session.id)">
                  <span v-if="session.titleGenerating" class="sb__session-name"><span class="sb__session-spinner"></span></span>
                  <span v-else class="sb__session-name">{{ session.name }}</span>
                  <span class="sb__props">
                    <span v-if="tokenLabel(session)" class="sb__prop is-tokens">{{ tokenLabel(session) }}</span>
                    <span v-if="session.state?.provider || session.provider" class="sb__prop">{{ session.state?.provider || session.provider }}</span>
                  </span>
                </button>
                <span class="sb__session-actions">
                  <button class="sb__row-action" type="button" title="Rename" @click.stop="emit('rename-session', session.id)">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                    </svg>
                  </button>
                  <button class="sb__row-action is-danger" type="button" title="Delete" @click.stop="emit('delete-session', session.id)">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
                    </svg>
                  </button>
                </span>
              </div>

              <button v-if="!sessions.length" key="empty" class="sb__empty" type="button" @click="createSession">
                <span>No sessions</span>
                <small>Create one to start</small>
              </button>
            </TransitionGroup>
            <button
              v-if="hasMoreSessions(workspace)"
              class="sb__show-more"
              type="button"
              @click="showMoreSessions(workspace)"
            >
              展示更多
            </button>
          </div>
        </Transition>
      </section>

      <button v-if="!workspaces.length" class="sb__empty sb__empty-workspace" type="button" :data-tip="'Add workspace'" title="Add workspace" @click="emit('add-workspace')">
        <Transition name="sb-fade">
          <span v-if="!collapsed">No workspace</span>
        </Transition>
        <Transition name="sb-fade">
          <small v-if="!collapsed">Add a folder</small>
        </Transition>
        <svg v-if="collapsed" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M12 5v14M5 12h14" />
        </svg>
      </button>

      <p v-if="workspaceError" class="sb__error">{{ workspaceError }}</p>
    </div>

    <footer class="sb__footer">
      <span class="sb__status-dot" />
      <Transition name="sb-fade">
        <span v-if="!collapsed">Runtime ready</span>
      </Transition>
    </footer>
  </aside>
</template>

<style scoped>
.sb {
  --sb-w: 286px;
  --surface: #ffffff;
  --hover: #e4e7ee;
  --active: #dbeafe;
  --text: #1e293b;
  --muted: #64748b;
  --faint: #94a3b8;
  --blue: #2563eb;
  --red: #dc2626;
  --green: #16a34a;

  display: flex;
  width: var(--sb-w);
  height: 100dvh;
  flex: 0 0 var(--sb-w);
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid rgba(15, 23, 42, .06);
  background: var(--surface);
  color: var(--text);
  contain: layout paint;
  user-select: none;
  -webkit-user-select: none;
  transition: width 320ms cubic-bezier(.16, 1, .3, 1), flex-basis 320ms cubic-bezier(.16, 1, .3, 1);
}

.sb.is-collapsed {
  --sb-w: 64px;
}

.sb button {
  font: inherit;
}

.sb__top {
  display: flex;
  height: 44px;
  flex: 0 0 44px;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 14px;
}
.sb.is-collapsed .sb__top {
  padding: 0 8px;
  gap: 4px;
}

.sb.is-collapsed .sb__top .sb__top-action {
  display: none;
}

.sb__brand {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  border: 0;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}

.sb__mark {
  display: grid;
  width: 22px;
  height: 22px;
  flex: 0 0 22px;
  place-items: center;
  border-radius: 5px;
  background: var(--text);
  color: #ffffff;
  font-size: 11px;
  font-weight: 700;
}

.sb__collapse-toggle {
  display: grid;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  place-items: center;
  border: 0;
  border-radius: 7px;
  background: rgba(15, 23, 42, 0.1);
  color: var(--text);
  cursor: pointer;
  transition: background 120ms ease, color 120ms ease, transform 120ms ease;
}

.sb__collapse-toggle:hover,
.sb__collapse-toggle:focus-visible {
  background: rgba(15, 23, 42, 0.18);
}

.sb__collapse-toggle:active {
  transform: scale(0.92);
}

.sb__top-action,
.sb__row-action {
  display: grid;
  width: 20px;
  height: 20px;
  flex: 0 0 20px;
  place-items: center;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: var(--faint);
  cursor: pointer;
}

/* 顶部加号（添加 workspace）：实底主按钮，常态可见 */
.sb__top-action {
  width: 28px;
  height: 28px;
  flex-basis: 28px;
  border-radius: 7px;
  background: var(--blue);
  color: #ffffff;
  transition: background 120ms ease, color 120ms ease, transform 120ms ease;
}

.sb__top-action:hover,
.sb__top-action:focus-visible {
  background: #1d4ed8;
}

.sb__top-action:active {
  transform: scale(0.92);
}

.sb__row-action:hover,
.sb__row-action:focus-visible {
  background: rgba(15, 23, 42, 0.05);
  color: var(--text);
}

.sb__new-wrap {
  margin: 0 10px 6px;
  flex: 0 0 auto;
}

.sb.is-collapsed .sb__new-wrap {
  margin: 0 6px 6px;
}

.sb__new {
  display: flex;
  width: 100%;
  height: 30px;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 11.5px;
  font-weight: 500;
  opacity: .56;
  transition: background 120ms ease, color 120ms ease, opacity 120ms ease,
    width 300ms cubic-bezier(.16, 1, .3, 1), height 300ms cubic-bezier(.16, 1, .3, 1),
    border-radius 300ms ease, margin 300ms cubic-bezier(.16, 1, .3, 1), padding 300ms ease;
}

.sb__new:hover,
.sb__new:focus-visible {
  background: var(--hover);
  color: var(--text);
  opacity: 1;
}

.sb__new kbd {
  margin-left: auto;
  color: var(--faint);
  font: 8.5px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}

.sb__nav {
  display: grid;
  flex: 0 0 auto;
  padding: 2px 0;
  border-bottom: 1px solid rgba(15, 23, 42, .04);
}

.sb__nav-item {
  display: flex;
  height: 30px;
  align-items: center;
  gap: 8px;
  margin: 0 4px;
  padding: 0 14px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  transition: background 100ms ease, color 100ms ease,
    width 300ms cubic-bezier(.16, 1, .3, 1), height 300ms cubic-bezier(.16, 1, .3, 1),
    border-radius 300ms ease, margin 300ms cubic-bezier(.16, 1, .3, 1), padding 300ms ease;
}

.sb__nav-item:hover,
.sb__nav-item:focus-visible {
  background: var(--hover);
  color: var(--text);
}

.sb__nav-item.is-active {
  background: rgba(37, 99, 235, .08);
  color: var(--blue);
  font-weight: 550;
}

.sb__scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 4px 0;
  scrollbar-width: thin;
  scrollbar-color: rgba(15, 23, 42, .08) transparent;
}

.sb__scroll::-webkit-scrollbar {
  width: 6px;
}

.sb__scroll::-webkit-scrollbar-thumb {
  border: 2px solid transparent;
  border-radius: 6px;
  background: rgba(15, 23, 42, .12);
  background-clip: padding-box;
}

.sb__workspace-group {
  margin: 4px 0;
}

.sb__workspace-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 2px;
  margin: 0 4px;
  border-radius: 5px;
}

.sb__workspace-row:hover,
.sb__workspace-row:focus-within {
  background: var(--hover);
}

.sb__workspace {
  display: flex;
  min-width: 0;
  flex: 1;
  height: 28px;
  align-items: center;
  gap: 6px;
  padding: 0 8px;
  border: 0;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 11.5px;
  font-weight: 550;
  text-align: left;
  transition: padding 300ms cubic-bezier(.16, 1, .3, 1), justify-content 300ms ease, background 100ms ease, color 100ms ease;
}

.sb.is-collapsed .sb__workspace {
  justify-content: center;
  padding: 0;
}

.sb.is-collapsed .sb__workspace-row > .sb__row-action {
  display: none;
}

.sb.is-collapsed .sb__empty-workspace {
  align-items: center;
  justify-content: center;
  padding: 0;
}

.sb__workspace-row:hover .sb__workspace,
.sb__workspace-row:focus-within .sb__workspace {
  color: var(--text);
}

.sb__avatar {
  display: grid;
  width: 20px;
  height: 20px;
  flex: 0 0 20px;
  place-items: center;
  border-radius: 4px;
  color: #ffffff;
  font-size: 8px;
  font-weight: 700;
  transition: width 300ms cubic-bezier(.16, 1, .3, 1), height 300ms cubic-bezier(.16, 1, .3, 1),
    flex-basis 300ms cubic-bezier(.16, 1, .3, 1), border-radius 300ms ease, font-size 300ms ease;
}

.sb__avatar.is-blue {
  background: var(--blue);
}

.sb__avatar.is-red {
  background: var(--red);
}

.sb__workspace-name,
.sb__session-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb__workspace-name {
  flex: 1;
}

.sb__state {
  flex: 0 0 auto;
  color: var(--faint);
  font: 9px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  text-transform: uppercase;
}

.sb__state.is-live {
  color: var(--green);
}

.sb__chev {
  flex: 0 0 auto;
  color: var(--faint);
  transition: transform 180ms ease;
}

.sb__workspace-group.is-open .sb__chev {
  transform: rotate(90deg);
}

.sb__children {
  padding-bottom: 2px;
}

.sb-expand-enter-active,
.sb-expand-leave-active {
  max-height: min(72vh, 720px);
  overflow: hidden;
  transform-origin: top;
  transition:
    max-height 240ms cubic-bezier(.16, 1, .3, 1),
    opacity 170ms ease,
    transform 240ms cubic-bezier(.16, 1, .3, 1);
  will-change: max-height, opacity, transform;
}

.sb-expand-enter-from,
.sb-expand-leave-to {
  max-height: 0;
  opacity: 0;
  transform: translateY(-4px) scaleY(.985);
}

.sb-expand-enter-to,
.sb-expand-leave-from {
  max-height: min(72vh, 720px);
  opacity: 1;
  transform: translateY(0) scaleY(1);
}

.sb__session-list {
  position: relative;
}

.sb__session-row {
  display: flex;
  min-width: 0;
  height: 34px;
  align-items: center;
  gap: 2px;
  margin: 0 4px;
  padding-left: 24px;
  border-radius: 5px;
  transition: background 100ms ease;
}

.sb-session-enter-active,
.sb-session-leave-active {
  transition:
    opacity 160ms ease,
    transform 200ms cubic-bezier(.16, 1, .3, 1);
  will-change: opacity, transform;
}

.sb-session-enter-from,
.sb-session-leave-to {
  opacity: 0;
  transform: translateY(-5px);
}

.sb-session-move {
  transition: transform 180ms cubic-bezier(.16, 1, .3, 1);
}

.sb-session-leave-active {
  position: absolute;
  right: 0;
  left: 0;
}

.sb__session-row:hover,
.sb__session-row:focus-within {
  background: var(--hover);
}

.sb__session-row.is-active {
  background: rgba(37, 99, 235, .05);
}

.sb__session {
  display: flex;
  min-width: 0;
  flex: 1;
  height: 100%;
  align-items: center;
  gap: 8px;
  padding: 0 4px 0 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.sb__session-name {
  flex: 1;
  color: var(--text);
  font-size: 11.5px;
  font-weight: 500;
}

.sb__session-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid rgba(15, 23, 42, .18);
  border-top-color: var(--blue);
  border-radius: 50%;
  animation: sb-spin .7s linear infinite;
}

@keyframes sb-spin {
  to { transform: rotate(360deg); }
}

.sb__session-row.is-active .sb__session-name {
  color: var(--blue);
  font-weight: 550;
}

.sb__props {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
}

.sb__prop {
  max-width: 68px;
  overflow: hidden;
  padding: 1px 6px;
  border-radius: 3px;
  background: rgba(15, 23, 42, .03);
  color: var(--muted);
  font-size: 9.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb__prop.is-tokens {
  color: var(--faint);
  font: 9px/1.3 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}

.sb__session-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 2px;
  opacity: 0.55; /* 常态可见，hover 全显 */
  transition: opacity 120ms ease;
}

.sb__session-row:hover .sb__session-actions,
.sb__session-row:focus-within .sb__session-actions {
  opacity: 1;
}

.sb__show-more {
  display: flex;
  width: calc(100% - 32px);
  height: 28px;
  align-items: center;
  justify-content: center;
  margin: 3px 4px 3px 28px;
  border: 0;
  border-radius: 5px;
  background: rgba(37, 99, 235, .06);
  color: var(--blue);
  cursor: pointer;
  font-size: 11px;
  font-weight: 550;
  transition: background 100ms ease, color 100ms ease;
}

.sb__show-more:hover,
.sb__show-more:focus-visible {
  background: rgba(37, 99, 235, .11);
}

.sb__workspace-row > .sb__row-action {
  margin-right: 4px;
  opacity: 0.55; /* 常态可见，hover 全显 */
  transition: opacity 120ms ease;
}

.sb__workspace-row:hover > .sb__row-action,
.sb__workspace-row:focus-within > .sb__row-action {
  opacity: 1;
}

.sb__row-action.is-danger:hover,
.sb__row-action.is-danger:focus-visible {
  background: #fdecea;
  color: var(--red);
}

.sb__empty {
  display: flex;
  min-width: 0;
  height: 34px;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  margin: 0 4px;
  padding: 0 12px 0 28px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  text-align: left;
}

.sb__empty:hover,
.sb__empty:focus-visible {
  background: var(--hover);
  color: var(--text);
}

.sb__empty span {
  font-size: 11.5px;
  font-weight: 550;
}

.sb__empty small {
  color: var(--faint);
  font-size: 10px;
}

.sb__empty-workspace {
  margin-top: 6px;
  padding-left: 14px;
}

.sb__error {
  margin: 8px 12px;
  color: var(--red);
  font-size: 11px;
  line-height: 1.35;
}

.sb__footer {
  display: flex;
  height: 36px;
  flex: 0 0 36px;
  align-items: center;
  gap: 8px;
  padding: 0 14px;
  border-top: 1px solid rgba(15, 23, 42, .04);
  color: var(--faint);
  font-size: 10.5px;
}

.sb__status-dot {
  width: 5px;
  height: 5px;
  flex: 0 0 5px;
  border-radius: 50%;
  background: var(--green);
}

/* 文字淡入淡出（收缩/展开时） */
.sb-fade-enter-active,
.sb-fade-leave-active {
  transition: opacity 130ms ease;
}

.sb-fade-enter-from,
.sb-fade-leave-to {
  opacity: 0;
}

/* 文字时序错开：收缩时文字先快速淡出（宽度后收），
   展开时宽度先展开、文字延迟淡入——避免文字挤在窄条里闪现 */
.sb.is-collapsed .sb-fade-leave-active {
  transition: opacity 100ms ease;
}

.sb:not(.is-collapsed) .sb-fade-enter-active {
  transition: opacity 200ms ease 170ms;
}

/* --- 收缩态 --- */
.sb.is-collapsed {
  --sb-w: 64px;
  overflow: visible;
  contain: layout; /* 去掉 paint，允许 tooltip 溢出到侧栏外 */
}

/* 收缩态：hover 气泡提示 */
.sb.is-collapsed [data-tip] {
  position: relative;
}

.sb.is-collapsed [data-tip]::after {
  content: attr(data-tip);
  position: absolute;
  left: calc(100% + 12px);
  top: 50%;
  transform: translateY(-50%) translateX(-4px);
  z-index: 40;
  padding: 5px 9px;
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.92);
  color: #ffffff;
  font-size: 11px;
  font-weight: 500;
  line-height: 1.3;
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  transition: opacity 120ms ease 60ms, transform 120ms ease 60ms;
}

.sb.is-collapsed [data-tip]:hover::after {
  opacity: 1;
  transform: translateY(-50%) translateX(0);
}

/* 折叠过渡期间抑制 tooltip 误触（元素位移到鼠标下方会误触发 hover） */
.sb.is-animating [data-tip]::after {
  opacity: 0 !important;
  transform: translateY(-50%) translateX(-4px) !important;
}

/* 收缩态：nav 圆形图标 + active 蓝底 */
.sb.is-collapsed .sb__nav-item {
  width: 30px;
  height: 30px;
  justify-content: center;
  margin: 2px auto;
  padding: 0;
  border-radius: 50%;
}

.sb.is-collapsed .sb__nav-item.is-active {
  background: var(--blue);
  color: #ffffff;
}

.sb.is-collapsed .sb__nav-item.is-active:hover {
  background: var(--blue);
}

/* 收缩态：New session 圆形 */
.sb.is-collapsed .sb__new {
  width: 30px;
  height: 30px;
  margin: 0 auto;
  justify-content: center;
  border-radius: 50%;
  padding: 0;
}

/* 收缩态：workspace 头像稍大、圆形 */
.sb.is-collapsed .sb__avatar {
  width: 24px;
  height: 24px;
  flex-basis: 24px;
  border-radius: 50%;
  font-size: 9px;
}

/* 收缩态：footer 状态点居中 */
.sb.is-collapsed .sb__footer {
  justify-content: center;
  padding: 0;
}

/* 收缩切换箭头旋转 */
.sb__collapse-icon {
  transition: transform 300ms cubic-bezier(0.16, 1, 0.3, 1);
}

.sb__collapse-icon.is-collapsed {
  transform: rotate(180deg);
}

@media (max-width: 760px) {
  .sb {
    --sb-w: 240px;
  }

  .sb__props {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .sb,
  .sb *,
  .sb *::before,
  .sb *::after {
    transition-duration: .01ms !important;
  }
}
</style>
