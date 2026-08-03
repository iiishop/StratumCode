<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

// 通用底栏弹出面板：从触发元素位置"生长"出来（macOS Dock 弹窗风格），
// 收回时缩回触发元素。可复用于底栏任意入口（更新面板、后续的通知/设置等）。
//
// 不用 Vue <Transition>（Teleport 场景 enter 过渡偶发不触发），
// 改用 v-show + 手动 .is-open 类切换：显示后 nextTick 再加类，保证过渡必然发生。

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
const visible = ref(false)
const open = ref(false)
const closing = ref(false)
const panelStyle = ref({})
const arrowStyle = ref({})
let hideTimer = 0

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

watch(() => props.modelValue, async (v) => {
  window.clearTimeout(hideTimer)
  if (v) {
    visible.value = true
    closing.value = false
    await nextTick()
    positionPanel()
    // 下一帧再加 .is-open，保证生长动画必然触发
    requestAnimationFrame(() => { open.value = true })
  } else {
    // 收回动画：缩回触发元素
    open.value = false
    closing.value = true
    hideTimer = window.setTimeout(() => {
      if (!props.modelValue) {
        closing.value = false
        visible.value = false
      }
    }, 220)
  }
})

function onResize() {
  if (visible.value && open.value) positionPanel()
}

onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.clearTimeout(hideTimer)
})
</script>

<template>
  <Teleport to="body">
    <div v-show="visible" class="dock-popover">
      <div class="dock-popover__scrim" @click="emit('update:modelValue', false)"></div>
      <div ref="panelEl" class="dock-popover__panel" :class="{ 'is-open': open, 'is-closing': closing }" :style="panelStyle">
        <span v-if="showArrow" class="dock-popover__arrow" :style="arrowStyle"></span>
        <slot></slot>
      </div>
    </div>
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
  /* 初始：锚点边收拢的隐藏态（v-show 显示后由 .is-open 触发"展开"动画） */
  transform: scaleY(0.05) scaleX(0.75);
  opacity: 0;
  will-change: transform, opacity;
}

/* macOS Dock 弹出动画（NSPopover scale effect）：
   - 不是整体缩放渐显，而是"从锚点边展开"：锚点边（transform-origin 所在边）固定不动，
     对面边向外铺开（scaleY 高度展开 + 轻微 scaleX），像纸张从图标上展开
   - 内容几乎立即不透明，视觉焦点在"展开"而不是"淡入" */
.dock-popover__panel.is-open {
  animation: dock-grow 0.32s ease-out forwards;
}

@keyframes dock-grow {
  0% {
    transform: scaleY(0.05) scaleX(0.75);
    opacity: 0;
  }
  20% {
    opacity: 1;
  }
  55% {
    transform: scaleY(1.04) scaleX(1.01);
    opacity: 1;
  }
  75% {
    transform: scaleY(0.985) scaleX(1);
  }
  100% {
    transform: scaleY(1) scaleX(1);
    opacity: 1;
  }
}

/* 收回：从锚点边快速收拢 */
.dock-popover__panel.is-closing {
  animation: dock-shrink 0.15s ease-in forwards;
}

@keyframes dock-shrink {
  0% {
    transform: scaleY(1) scaleX(1);
    opacity: 1;
  }
  100% {
    transform: scaleY(0.05) scaleX(0.75);
    opacity: 0;
  }
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
