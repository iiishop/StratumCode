<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import html2canvas from 'html2canvas'
import { MacDockGenie } from '../../lib/MacDockGenie.js'

// 通用底栏弹出面板：
// - 打开：macOS Dock 弹窗风格，从触发元素（锚点）边"展开"（scaleY + 锚点固定 + 过冲）
// - 收起：macOS 精灵效果（Genie Effect）——截取面板快照，切片沿贝塞尔弧线吸入底栏按钮
// 可复用于底栏任意入口（更新面板、后续的通知/设置等）。
// 不用 Vue <Transition>（Teleport 场景 enter 过渡偶发不触发），手动控制类与动画。

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

watch(() => props.modelValue, async (v) => {
  if (v) {
    cleanupGenieCanvas()
    visible.value = true
    await nextTick()
    positionPanel()
    // 下一帧再加 .is-open，保证生长动画必然触发
    requestAnimationFrame(() => { open.value = true })
  } else {
    // 收起：精灵效果吸入底栏按钮
    closeWithGenie()
  }
})

// 精灵吸入动画：截快照 → 隐藏面板 → canvas 切片沿弧线吸入 Dock 目标
async function closeWithGenie() {
  const panel = panelEl.value
  if (!panel) {
    open.value = false
    visible.value = false
    return
  }

  // 1. 截取面板快照（html2canvas）
  let snapshot
  try {
    snapshot = await html2canvas(panel, { backgroundColor: null, scale: 1, useCORS: true })
  } catch (err) {
    // 快照失败降级：直接隐藏（不阻塞关闭）
    console.warn('[DockPopover] genie snapshot failed, fallback to instant close', err)
    open.value = false
    visible.value = false
    return
  }

  const rect = panel.getBoundingClientRect()

  // 2. 隐藏面板
  open.value = false
  visible.value = false

  // 3. 全屏 canvas 层播放吸入动画
  const canvas = document.createElement('canvas')
  canvas.width = window.innerWidth
  canvas.height = window.innerHeight
  canvas.dataset.genie = '1'
  canvas.style.cssText = 'position:fixed;left:0;top:0;z-index:45;pointer-events:none;'
  document.body.appendChild(canvas)

  const anchor = resolveAnchor()
  let dockTarget
  if (anchor) {
    const ar = anchor.getBoundingClientRect()
    dockTarget = { x: ar.left + ar.width / 2, y: ar.top + ar.height / 2 }
  } else {
    dockTarget = { x: window.innerWidth - 60, y: window.innerHeight - 30 }
  }

  const genie = new MacDockGenie(
    canvas,
    snapshot,
    { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
    dockTarget,
    {
      duration: 800,
      pathFunction: [0.2, -0.5, 0.8, 1.2],
      staggerFactor: 0.6,
      sliceWidth: 3,
    },
  )
  genie.start(() => canvas.remove())
}

// 打开时清理可能残留的 genie canvas（动画中重新打开）
function cleanupGenieCanvas() {
  document.querySelectorAll('canvas[data-genie]').forEach((c) => c.remove())
}

function onResize() {
  if (visible.value && open.value) positionPanel()
}

onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  cleanupGenieCanvas()
})
</script>

<template>
  <Teleport to="body">
    <div v-show="visible" class="dock-popover">
      <div class="dock-popover__scrim" @click="emit('update:modelValue', false)"></div>
      <div ref="panelEl" class="dock-popover__panel" :class="{ 'is-open': open }" :style="panelStyle">
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
