<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
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

const collapsed = ref(false)

const NAV = [
  { id: 'home', label: 'Workspace', sub: 'Sessions', icon: 'M4 6h16v12H4z M8 10h8 M8 14h5' },
  { id: 'providers', label: 'Providers', sub: 'Models', icon: 'M4 8h16M4 13h16M4 18h16 M8 6v4 M16 11v4 M12 16v4' },
  { id: 'mcp', label: 'MCP', sub: 'Tools', icon: 'M8 8h8M8 12h8M8 16h8 M5 5h14v14H5z' },
  { id: 'lsp', label: 'LSP', sub: 'Language', icon: 'M8 5h8v14H8z M11 10h2 M11 14h2 M4 9h4 M16 9h4 M4 15h4 M16 15h4' },
  { id: 'settings', label: 'Settings', sub: 'Behavior', icon: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M4 12h3 M17 12h3 M12 4v3 M12 17v3' },
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

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <aside class="sb" :class="{ 'is-collapsed': collapsed }" aria-label="Primary navigation">
    <header class="sb__top">
      <button class="sb__brand" type="button" aria-label="Open workspace" @click="emit('navigate', 'home')">
        <span class="sb__mark">S</span>
        <span class="sb__brand-text">
          <ShuffleText text="StratumCode" :duration="0.48" :stagger="0.03" />
        </span>
      </button>

      <button
        class="sb__icon-btn sb__collapse"
        type="button"
        :aria-label="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
        :title="collapsed ? 'Expand' : 'Collapse'"
        @click="collapsed = !collapsed"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="m15 6-6 6 6 6" />
        </svg>
      </button>
    </header>

    <div class="sb__primary">
      <button class="sb__new" type="button" title="New session" @click="createSession">
        <svg class="sb__rail-icon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M12 5v14M5 12h14" />
        </svg>
        <span class="sb__copy sb__new-copy">
          <span>New session</span>
          <kbd>Ctrl N</kbd>
        </span>
      </button>

      <nav class="sb__nav" aria-label="Sections">
        <button
          v-for="item in NAV"
          :key="item.id"
          class="sb__nav-item"
          :class="{ 'is-active': active === item.id }"
          :aria-current="active === item.id ? 'page' : undefined"
          :title="collapsed ? item.label : ''"
          type="button"
          @click="emit('navigate', item.id)"
        >
          <svg class="sb__rail-icon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round">
            <path :d="item.icon" />
          </svg>
          <span class="sb__copy sb__item-copy">
            <strong>{{ item.label }}</strong>
            <small>{{ item.sub }}</small>
          </span>
        </button>
      </nav>
    </div>

    <div class="sb__scroll">
      <section class="sb__section" aria-labelledby="sidebar-workspaces">
        <div class="sb__section-head">
          <span id="sidebar-workspaces" class="sb__copy sb__label">Workspaces</span>
          <button class="sb__icon-btn sb__copy" type="button" title="Add workspace" @click="emit('add-workspace')">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
        </div>

        <div
          v-for="workspace in workspaces"
          :key="workspace.id"
          class="sb__workspace-row"
          :class="{ 'is-active': activeWorkspace?.id === workspace.id }"
          :title="collapsed ? workspaceLabel(workspace) : workspace.path"
        >
          <button class="sb__workspace" type="button" @click="emit('workspace-session', workspace)">
            <span class="sb__avatar" :class="{ 'is-live': workspace.is_active }">{{ initials(workspace) }}</span>
            <span class="sb__copy sb__workspace-copy">
              <strong>{{ workspaceLabel(workspace) }}</strong>
              <small>{{ workspace.path }}</small>
            </span>
            <span class="sb__copy sb__state" :class="{ 'is-live': workspace.is_active }">
              {{ workspace.is_active ? 'open' : 'idle' }}
            </span>
          </button>
          <button
            v-if="workspaces.length > 1"
            class="sb__row-action sb__copy"
            type="button"
            title="Remove workspace"
            @click.stop="emit('delete-workspace', workspace.id)"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <p v-if="workspaceError" class="sb__copy sb__error">{{ workspaceError }}</p>
      </section>

      <section class="sb__section sb__sessions" aria-labelledby="sidebar-sessions">
        <div class="sb__section-head">
          <span id="sidebar-sessions" class="sb__copy sb__label">Sessions</span>
        </div>

        <div
          v-for="session in sessions"
          :key="session.id"
          class="sb__session-row"
          :class="{ 'is-active': activeSession?.id === session.id }"
          :title="collapsed ? session.name : 'Open session'"
        >
          <button class="sb__session" type="button" @click="emit('open-session', session.id)">
            <span class="sb__thread-dot" />
            <svg class="sb__rail-icon sb__session-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round">
              <path d="M5 5h14v11H9l-4 3z" />
            </svg>
            <span class="sb__copy sb__session-copy">
              <strong>{{ session.name }}</strong>
              <small v-if="tokenLabel(session)">{{ tokenLabel(session) }} tokens</small>
            </span>
          </button>
          <span class="sb__copy sb__session-actions">
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

        <button v-if="!sessions.length" class="sb__empty" type="button" @click="createSession">
          <span class="sb__thread-dot is-empty" />
          <svg class="sb__rail-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
          <span class="sb__copy sb__session-copy">
            <strong>No sessions</strong>
            <small>Create one to start</small>
          </span>
        </button>
      </section>
    </div>

    <footer class="sb__footer">
      <span class="sb__status-dot" />
      <span class="sb__copy sb__status-copy">
        <strong>Runtime ready</strong>
        <small>Local agent</small>
      </span>
    </footer>
  </aside>
</template>

<style scoped>
.sb {
  --sb-w: 268px;
  --rail-w: 68px;
  --ink: #111318;
  --ink-2: #171a21;
  --ink-3: #1d2129;
  --hover: #242934;
  --active: #f3f6fb;
  --line: rgba(255, 255, 255, .08);
  --line-strong: rgba(255, 255, 255, .14);
  --text: #e6eaf2;
  --muted: #9ba4b5;
  --faint: #687182;
  --accent: #6aa1ff;
  --good: #8bd88b;
  --danger: #ff7a7a;

  display: flex;
  position: relative;
  width: var(--sb-w);
  min-width: var(--rail-w);
  height: 100dvh;
  flex: 0 0 auto;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid #dbe2ef;
  background: linear-gradient(180deg, var(--ink), var(--ink-2));
  color: var(--text);
  contain: layout paint;
  transition: width 180ms cubic-bezier(.16, 1, .3, 1);
  user-select: none;
  -webkit-user-select: none;
}

.sb.is-collapsed {
  width: var(--rail-w);
}

.sb button {
  font: inherit;
}

.sb__top {
  display: flex;
  height: 54px;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border-bottom: 1px solid var(--line);
}

.sb__brand {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 9px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.sb__mark,
.sb__avatar {
  display: grid;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  place-items: center;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: #202630;
}

.sb__mark {
  color: #ffffff;
  font: 740 14px/1 var(--heading, sans-serif);
}

.sb__brand-text {
  min-width: 0;
  flex: 1;
}

.sb__brand :deep(.shuffle-text) {
  color: #ffffff;
  font: 650 13px/1 var(--heading, sans-serif);
  letter-spacing: 0;
}

.sb__icon-btn,
.sb__row-action {
  display: grid;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  place-items: center;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
}

.sb__icon-btn:hover,
.sb__row-action:hover {
  border-color: var(--line-strong);
  background: var(--hover);
  color: #ffffff;
}

.sb__collapse svg {
  transition: transform 180ms cubic-bezier(.16, 1, .3, 1);
}

.is-collapsed .sb__collapse svg {
  transform: rotate(180deg);
}

.sb__primary {
  padding: 10px;
  border-bottom: 1px solid var(--line);
}

.sb__new,
.sb__nav-item,
.sb__workspace-row,
.sb__session-row,
.sb__empty,
.sb__footer {
  position: relative;
  display: flex;
  width: 100%;
  min-width: 0;
  align-items: center;
  gap: 9px;
  border: 1px solid transparent;
  border-radius: 8px;
  color: inherit;
  text-align: left;
}

.sb__new,
.sb__nav-item,
.sb__empty {
  background: transparent;
  cursor: pointer;
}

.sb__workspace,
.sb__session {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 9px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.sb__new {
  height: 38px;
  margin-bottom: 8px;
  padding: 0 10px;
  background: #eff4ff;
  color: #111827;
  font-weight: 620;
}

.sb__new:hover {
  background: #ffffff;
}

.sb__new-copy {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sb__new kbd {
  color: #687182;
  font: 9px/1 var(--mono, monospace);
}

.sb__nav {
  display: grid;
  gap: 2px;
}

.sb__nav-item {
  height: 36px;
  padding: 0 9px;
  color: #cfd5df;
}

.sb__nav-item:hover,
.sb__workspace-row:hover,
.sb__session-row:hover,
.sb__empty:hover {
  background: var(--hover);
  color: #ffffff;
}

.sb__nav-item.is-active {
  background: var(--active);
  color: #151922;
}

.sb__nav-item.is-active::before,
.sb__session-row.is-active::before,
.sb__workspace-row.is-active::before {
  content: "";
  position: absolute;
  left: 0;
  top: 9px;
  bottom: 9px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--accent);
}

.sb__rail-icon {
  flex: 0 0 auto;
}

.sb__copy {
  min-width: 0;
  opacity: 1;
  overflow: hidden;
  transform: translateX(0);
  transition: opacity 120ms ease, transform 160ms cubic-bezier(.16, 1, .3, 1), visibility 120ms ease;
  visibility: visible;
}

.sb__item-copy,
.sb__workspace-copy,
.sb__session-copy,
.sb__status-copy {
  display: flex;
  flex: 1;
  flex-direction: column;
}

.sb__copy strong,
.sb__copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb__copy strong {
  color: inherit;
  font-size: 12px;
  font-weight: 570;
  line-height: 1.25;
}

.sb__copy small {
  margin-top: 2px;
  color: var(--muted);
  font: 10px/1.2 var(--mono, monospace);
}

.sb__nav-item.is-active small {
  color: #667085;
}

.sb__scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 10px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, .18) transparent;
}

.sb__scroll::-webkit-scrollbar {
  width: 6px;
}

.sb__scroll::-webkit-scrollbar-thumb {
  border: 2px solid transparent;
  border-radius: 6px;
  background: rgba(255, 255, 255, .18);
  background-clip: padding-box;
}

.sb__section + .sb__section {
  margin-top: 14px;
}

.sb__section-head {
  display: flex;
  height: 26px;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 0 7px;
}

.sb__label {
  color: var(--faint);
  font: 700 10px/1 var(--mono, monospace);
  letter-spacing: .04em;
  text-transform: uppercase;
}

.sb__workspace-row {
  height: 42px;
  padding: 0 7px;
}

.sb__workspace-row.is-active,
.sb__session-row.is-active {
  background: #222936;
  border-color: var(--line-strong);
}

.sb__avatar {
  width: 28px;
  height: 28px;
  flex-basis: 28px;
  color: #c8d0dd;
  font: 760 9px/1 var(--mono, monospace);
}

.sb__avatar.is-live {
  border-color: rgba(139, 216, 139, .42);
  color: var(--good);
}

.sb__state {
  flex: 0 0 auto;
  padding: 2px 6px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--faint);
  font: 700 9px/1 var(--mono, monospace);
  text-transform: uppercase;
}

.sb__state.is-live {
  border-color: rgba(139, 216, 139, .3);
  color: var(--good);
}

.sb__row-action {
  width: 24px;
  height: 24px;
  flex-basis: 24px;
  opacity: 0;
}

.sb__workspace-row:hover > .sb__row-action,
.sb__session-row:hover .sb__row-action,
.sb__row-action:focus-visible {
  opacity: 1;
}

.sb__row-action.is-danger:hover {
  border-color: rgba(255, 122, 122, .28);
  background: rgba(255, 122, 122, .12);
  color: var(--danger);
}

.sb__sessions {
  padding-bottom: 4px;
}

.sb__session-row,
.sb__empty {
  height: 38px;
  padding: 0 7px;
}

.sb__thread-dot {
  width: 6px;
  height: 6px;
  flex: 0 0 6px;
  border-radius: 50%;
  background: var(--faint);
}

.sb__session-row.is-active .sb__thread-dot {
  background: var(--accent);
}

.sb__thread-dot.is-empty {
  border: 1px dashed var(--faint);
  background: transparent;
}

.sb__session-icon {
  color: var(--muted);
}

.sb__session-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 2px;
}

.sb__empty {
  color: var(--muted);
}

.sb__error {
  margin: 8px 7px 0;
  color: #ffb0b0;
  font: 10px/1.35 var(--mono, monospace);
}

.sb__footer {
  height: 48px;
  flex: 0 0 auto;
  padding: 0 14px;
  border-top: 1px solid var(--line);
  border-radius: 0;
}

.sb__status-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  border-radius: 50%;
  background: var(--good);
  box-shadow: 0 0 0 4px rgba(139, 216, 139, .1);
}

.sb__status-copy strong {
  font-size: 11px;
}

.is-collapsed .sb__top {
  justify-content: space-between;
  gap: 2px;
  padding-inline: 6px;
}

.is-collapsed .sb__brand {
  flex: 0 0 32px;
}

.is-collapsed .sb__brand-text,
.is-collapsed .sb__copy {
  display: none;
  opacity: 0;
  pointer-events: none;
  transform: translateX(-6px);
  visibility: hidden;
}

.is-collapsed .sb__primary,
.is-collapsed .sb__scroll {
  padding-inline: 10px;
}

.is-collapsed .sb__new,
.is-collapsed .sb__nav-item,
.is-collapsed .sb__workspace-row,
.is-collapsed .sb__session-row,
.is-collapsed .sb__empty {
  justify-content: center;
  padding-inline: 0;
}

.is-collapsed .sb__workspace-row,
.is-collapsed .sb__session-row,
.is-collapsed .sb__empty {
  width: 48px;
}

.is-collapsed .sb__new {
  width: 48px;
}

.is-collapsed .sb__collapse {
  width: 22px;
  height: 22px;
  background: #202630;
}

.is-collapsed .sb__section-head {
  height: 8px;
  padding: 0;
}

.is-collapsed .sb__avatar {
  width: 28px;
  height: 28px;
  flex-basis: 28px;
}

.is-collapsed .sb__thread-dot {
  position: absolute;
  left: 8px;
}

.is-collapsed .sb__footer {
  justify-content: center;
  padding: 0;
}

@media (width <= 760px) {
  .sb {
    width: var(--rail-w) !important;
  }

  .sb__collapse,
  .sb__brand-text,
  .sb__copy {
    display: none;
  }

  .sb__top {
    justify-content: center;
    padding-inline: 0;
  }

  .sb__primary,
  .sb__scroll {
    padding-inline: 10px;
  }

  .sb__new,
  .sb__nav-item,
  .sb__workspace-row,
  .sb__session-row,
  .sb__empty {
    width: 48px;
    justify-content: center;
    padding-inline: 0;
  }

  .sb__section-head {
    height: 8px;
    padding: 0;
  }

  .sb__thread-dot {
    position: absolute;
    left: 8px;
  }

  .sb__footer {
    justify-content: center;
    padding: 0;
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
