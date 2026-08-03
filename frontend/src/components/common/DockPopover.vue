<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

// 通用底栏弹出面板：从触发元素位置"生长"出来（macOS Dock 弹窗风格），
// 收回时缩回触发元素。可复用于底栏任意入口（更新面板、后续的通知/设置等）。
// 动画用 CSS Transition（transform-origin 指向触发元素，实现"生长"感）。

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

function resolveAnchor() {
  const el = props.anchor
  if (!el) return null
  if (typeof el.getBoundingClientRect === 'function') return el
  if (el.value && typeof el.value.getBoundingClientRect === 'function') return el.value
  return null
}

function positionPanel() {
  const anchor = resolveAnchor()
  if (!anchor || !panelEl.value) {
    // 兜底：anchor 未就绪时固定右下角，保证面板一定能显示
    if (panelEl.value) {
      const vw = window.innerWidth
      const vh = window.innerHeight
      const panelW = panelEl.value.offsetWidth || 560
      const panelH = panelEl.value.offsetHeight || 400
      panelStyle.value = {
        left: `${Math.max(8, vw - panelW - 18)}px`,
        top: `${Math.max(8, vh - panelH - 50)}px`,
        width: props.width,
        transformOrigin: '50% 100%',
      }
    }
    return
  }
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
  const growsUp = panelTop >= 8
  const top = growsUp ? panelTop : rect.bottom + props.offset

  panelStyle.value = {
    left: `${left}px`,
    top: `${top}px`,
    width: props.width,
    // 生长原点：面板在触发元素上方 → 从底边中心生长；下方 → 从顶边中心
    transformOrigin: growsUp ? '50% 100%' : '50% 0%',
  }

  // 箭头对齐触发元素中心
  const anchorCenterX = rect.left + rect.width / 2
  const arrowLeft = anchorCenterX - left - 6
  arrowStyle.value = {
    left: `${Math.max(4, Math.min(arrowLeft, panelW - 14))}px`,
    top: growsUp ? 'calc(100% - 5px)' : '-5px',
  }
}

function onEnter() {
  positionPanel()
}

function onResize() {
  if (props.modelValue) positionPanel()
}

onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => window.removeEventListener('resize', onResize))
</script>

<template>
  <Teleport to="body">
    <Transition name="dock-popover" @enter="onEnter">
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

/* macOS 生长动画 */
.dock-popover-enter-active .dock-popover__panel {
  transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.2s ease;
}

.dock-popover-enter-from .dock-popover__panel {
  transform: scale(0.5);
  opacity: 0;
}

.dock-popover-leave-active .dock-popover__panel {
  transition: transform 0.16s ease-in, opacity 0.14s ease;
}

.dock-popover-leave-to .dock-popover__panel {
  transform: scale(0.55);
  opacity: 0;
}

.dock-popover__arrow {
  position: absolute;
  width: 10px;
  height: 10px;
  border: 1px solid var(--border-strong);
  background: #ffffff;
  box-shadow: 0 1px 3px rgba(16, 42, 92, 0.1);
  transform: translateX(-50%) rotate(45deg);
  pointer-events: none;
  z-index: 42;
}
</style>
