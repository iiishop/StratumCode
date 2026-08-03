<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import gsap from 'gsap'

// 通用底栏弹出面板：从触发元素位置"生长"出来（macOS Dock 弹窗风格），
// 收回时缩回触发元素。可复用于底栏任意入口（更新面板、后续的通知/设置等）。

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // 触发元素：HTMLElement 或 ref 对象
  anchor: { type: Object, default: null },
  width: { type: String, default: '560px' },
  // 水平对齐触发元素：'start' | 'center' | 'end'
  align: { type: String, default: 'end' },
  // 面板与触发元素的间距
  offset: { type: Number, default: 10 },
  showArrow: { type: Boolean, default: true },
})

const emit = defineEmits(['update:modelValue'])

const panelEl = ref(null)
const panelStyle = ref({})
const arrowStyle = ref({})
const origin = ref('50% 100%')
let anim = null

function resolveAnchor() {
  const el = props.anchor
  if (!el) return null
  if (typeof el.getBoundingClientRect === 'function') return el
  if (el.value && typeof el.value.getBoundingClientRect === 'function') return el.value
  return null
}

function positionPanel() {
  const anchor = resolveAnchor()
  if (!anchor || !panelEl.value) return
  const rect = anchor.getBoundingClientRect()
  const vw = window.innerWidth
  const vh = window.innerHeight
  const panelW = panelEl.value.offsetWidth
  const panelH = panelEl.value.offsetHeight

  // 水平位置
  let left
  if (props.align === 'start') left = rect.left
  else if (props.align === 'center') left = rect.left + rect.width / 2 - panelW / 2
  else left = rect.right - panelW
  left = Math.max(8, Math.min(left, vw - panelW - 8))

  // 垂直：优先向上弹出（底栏在底部），空间不够才向下
  const panelTop = rect.top - props.offset - panelH
  const top = panelTop >= 8 ? panelTop : rect.bottom + props.offset

  panelStyle.value = {
    left: `${left}px`,
    top: `${top}px`,
    width: props.width,
  }

  // 生长原点：面板在触发元素上方 → 从底边中心生长；下方 → 从顶边中心
  const growsUp = panelTop >= 8
  origin.value = growsUp ? '50% 100%' : '50% 0%'

  // 箭头对齐触发元素中心
  const anchorCenterX = rect.left + rect.width / 2
  const arrowLeft = anchorCenterX - left - 6
  arrowStyle.value = {
    left: `${Math.max(4, Math.min(arrowLeft, panelW - 14))}px`,
    top: growsUp ? 'calc(100% - 1px)' : '-5px',
    transform: growsUp ? 'translateX(-50%) rotate(45deg)' : 'translateX(-50%) rotate(45deg)',
  }
}

function onEnter(el, done) {
  positionPanel()
  el.style.opacity = 0
  el.style.transform = 'scale(0.5)'
  el.style.transformOrigin = origin.value
  anim = gsap.to(el, {
    opacity: 1,
    scale: 1,
    duration: 0.3,
    ease: 'back.out(1.7)',
    onComplete: done,
  })
}

function onLeave(el, done) {
  el.style.transformOrigin = origin.value
  anim = gsap.to(el, {
    opacity: 0,
    scale: 0.55,
    duration: 0.16,
    ease: 'power2.in',
    onComplete: done,
  })
}

// 窗口尺寸变化时重定位
function onResize() {
  if (props.modelValue) positionPanel()
}

onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  anim?.kill()
})
</script>

<template>
  <Teleport to="body">
    <Transition :css="false" @enter="onEnter" @leave="onLeave">
      <div v-if="modelValue" class="dock-popover">
        <div class="dock-popover__scrim" @click="emit('update:modelValue', false)"></div>
        <div ref="panelEl" class="dock-popover__panel" :style="panelStyle">
          <span v-if="showArrow" class="dock-popover__arrow" :style="arrowStyle"></span>
          <slot></slot>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dock-popover__scrim {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: transparent;
}

.dock-popover__panel {
  position: fixed;
  z-index: 41;
  will-change: transform, opacity;
}

.dock-popover__arrow {
  position: absolute;
  width: 10px;
  height: 10px;
  border: 1px solid var(--border-strong);
  background: #ffffff;
  box-shadow: 0 1px 3px rgba(16, 42, 92, 0.1);
  pointer-events: none;
  z-index: 42;
}
</style>
