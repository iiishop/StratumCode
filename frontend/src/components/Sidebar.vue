<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { gsap } from 'gsap'
import { animate, stagger } from 'animejs'

defineProps({ active: { type: String, default: 'home' } })
const emit = defineEmits(['navigate'])

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
]

function navigate(id) {
  emit('navigate', id)
}

function startSession() {
  emit('navigate', 'home')
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
        <span class="sb__logo sb__rail-icon">SC</span>
        <Transition name="sb-copy">
          <span v-if="showContent" class="sb__brand-copy">
            <strong>StratumCode</strong>
            <small>Agent workspace</small>
          </span>
        </Transition>
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
        <div v-if="showContent" class="sb__group-label">Project</div>
      </Transition>
      <div class="sb__project" title="StratumCode repository">
        <span class="sb__project-icon sb__rail-icon">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 7h7l2 2h9v10H3z M3 7V5h7l2 2"/>
          </svg>
        </span>
        <Transition name="sb-copy">
          <span v-if="showContent" class="sb__project-content">
            <span class="sb__project-copy">
              <strong>StratumCode</strong>
              <small>main</small>
            </span>
            <span class="sb__project-state">open</span>
          </span>
        </Transition>
      </div>
    </section>

    <section class="sb__group sb__sessions sb__reveal">
      <Transition name="sb-copy">
        <div v-if="showContent" class="sb__group-label">Current session</div>
      </Transition>
      <button class="sb__session" type="button" title="New session" @click="navigate('home')">
        <span class="sb__session-icon sb__rail-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round">
            <path d="M5 5h14v11H9l-4 3z"/>
          </svg>
        </span>
        <Transition name="sb-copy">
          <span v-if="showContent" class="sb__session-copy">
            <strong>New session</strong>
            <small>0 changes</small>
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
  border-right: 1px solid var(--border);
  color: var(--text);
  background: #121211;
  box-shadow: 8px 0 28px rgba(5, 4, 3, 0.08);
  z-index: 20;
}

.sb__header {
  display: flex;
  min-height: 68px;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 14px;
  border-bottom: 1px solid var(--border);
}

.sb__brand {
  display: flex;
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
  border: 1px solid var(--accent-border);
  border-radius: 9px;
  color: #fff2ed;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.12), transparent 52%),
    var(--accent);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.15);
  font: 700 10px/1 var(--mono);
  letter-spacing: -0.06em;
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
  color: var(--text-h);
  font: 600 13px/1.2 var(--heading);
  letter-spacing: -0.02em;
}

.sb__brand-copy small {
  margin-top: 2px;
  color: var(--text-muted);
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
  color: var(--text-muted);
  background: transparent;
  cursor: pointer;
}

.sb__collapse:hover {
  border-color: var(--border);
  color: var(--text-h);
  background: var(--code-bg-hover);
}

.sb__new {
  display: flex;
  min-height: 38px;
  align-items: center;
  gap: 9px;
  margin: 14px 12px 8px;
  padding: 0 9px;
  overflow: hidden;
  border: 1px solid var(--accent-border);
  border-radius: var(--radius-sm);
  color: #f0e4df;
  background: var(--accent-bg);
  cursor: pointer;
  white-space: nowrap;
}

.sb__new:hover {
  border-color: var(--accent);
  background: var(--accent-bg-hover);
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

.sb__new-icon { color: var(--accent-text); }
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
  color: #776e69;
  font: 8px/1 var(--mono);
}

.sb__group {
  padding: 12px 12px 2px;
}

.sb__group-label {
  height: 18px;
  padding: 0 7px;
  overflow: hidden;
  color: #5f5a57;
  font: 550 9px/1 var(--mono);
  white-space: nowrap;
  transition: opacity 120ms ease;
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
  color: #8e8884;
  background: transparent;
  cursor: pointer;
  text-align: left;
  white-space: nowrap;
}

.sb__nav-item:hover {
  color: #c7c0bb;
  border-color: #292624;
  background: #191817;
}

.sb__nav-item.is-active {
  color: var(--accent-text);
  border-color: var(--accent-border);
  background: var(--accent-bg);
}

.sb__nav-item.is-active::before {
  content: "";
  position: absolute;
  left: -1px;
  width: 2px;
  height: 20px;
  border-radius: 0 2px 2px 0;
  background: var(--accent);
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
  color: #5f5a57;
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
  color: #aaa39e;
  background: transparent;
  white-space: nowrap;
}

.sb__project {
  border-color: #292624;
  background: #171615;
}

.sb__project-icon {
  color: #8c8580;
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
  color: var(--text-muted);
  font: 8px/1 var(--mono);
}

.sb__sessions {
  min-height: 0;
  flex: 1;
}

.sb__session {
  width: 100%;
  color: #9a9490;
  cursor: pointer;
  text-align: left;
}

.sb__session:hover {
  color: #c7c0bb;
  border-color: #292624;
  background: #191817;
}

.sb__footer {
  min-height: 58px;
  margin: 0 12px;
  padding: 9px 7px;
  border-top: 1px solid var(--border);
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
  border: 1px solid #33302e;
  border-radius: 7px;
  background: #181716;
}

.sb__runtime-icon i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
}

.sb__runtime-short {
  display: none;
  color: var(--ok);
  font: 650 8px/1 var(--mono);
}

/* Compact alignment is applied only near the end of the GSAP collapse. */
.is-compact .sb__header {
  justify-content: center;
  padding-inline: 0;
}

.is-compact .sb__collapse {
  width: 20px;
  flex-basis: 20px;
  border-color: var(--border);
  background: #171615;
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
.is-compact .sb__session {
  width: 42px;
  padding-inline: 8px;
}

.is-compact .sb__project {
  background: transparent;
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
  .sb__session {
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
