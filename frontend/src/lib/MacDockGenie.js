/**
 * MacDockGenie —— macOS 收起到 Dock 的精灵效果（Genie Effect）
 * 把图形快照切成垂直切片，每个切片沿贝塞尔弧线 + 错开延迟 + 高度收缩吸入 Dock 图标。
 */
import { TransitionPath } from './TransitionPath.js'

export class MacDockGenie {
  /**
   * @param {HTMLCanvasElement} canvas          - 绘制用的 canvas（覆盖全屏，坐标系 = viewport）
   * @param {HTMLCanvasElement|HTMLImageElement} originalSnapshot - 图形快照（宽高 = startBounds 宽高）
   * @param {Object} startBounds                - 原始图形位置和尺寸 {left, top, width, height}
   * @param {Object} dockTarget                 - Dock 图标中心点 {x, y}
   * @param {Object} options
   *   - duration: number       总时长（ms）
   *   - pathFunction: number[] 路径贝塞尔 [x1,y1,x2,y2]（推荐 [0.2, -0.5, 0.8, 1.2]）
   *   - timingFunction: number[] 时间贝塞尔，默认 ease
   *   - staggerFactor: number  错开强度（0~1），越大波浪越明显
   *   - sliceWidth: number     切片宽度（1px 最精细，2px 更流畅）
   */
  constructor(canvas, originalSnapshot, startBounds, dockTarget, options = {}) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d', { alpha: true })
    this.snapshot = originalSnapshot
    this.startBounds = { ...startBounds }
    this.dockTarget = { ...dockTarget }

    this.duration = options.duration ?? 1100
    this.pathFunction = options.pathFunction ?? [0.2, -0.5, 0.8, 1.2]
    this.timingFunction = options.timingFunction ?? [0.25, 0.1, 0.25, 0.9]
    this.staggerFactor = options.staggerFactor ?? 0.65
    this.sliceWidth = options.sliceWidth ?? 2

    this.slices = []
    const numSlices = Math.max(1, Math.floor(this.startBounds.width / this.sliceWidth))

    // dock 目标在起始位置左侧还是右侧：靠近 dock 的 slice 先动
    const isDockOnLeft = this.dockTarget.x < this.startBounds.left + this.startBounds.width / 2

    for (let i = 0; i < numSlices; i++) {
      const sliceLeft = this.startBounds.left + i * this.sliceWidth
      // 每个切片的锚点定在底部中心（吸入时底部先动，视觉更自然）
      const sliceStartPoint = {
        x: sliceLeft + this.sliceWidth / 2,
        y: this.startBounds.top + this.startBounds.height,
      }

      const path = new TransitionPath(
        sliceStartPoint,
        this.dockTarget,
        this.pathFunction,
        this.timingFunction,
        this.duration,
      )

      // 根据 dock 位置调整错开方向：左侧 dock 左侧 slice 先动，右侧 dock 反之
      const staggerIndex = isDockOnLeft ? i : (numSlices - 1 - i)
      const staggerDelay = (staggerIndex / numSlices) * this.duration * this.staggerFactor

      this.slices.push({
        path,
        originalLeft: sliceLeft,
        sliceWidth: this.sliceWidth,
        staggerDelay,
      })
    }

    this._raf = null
    this._startTime = 0
    this._isRunning = false
  }

  start(onComplete = null) {
    if (this._isRunning) return
    this._isRunning = true
    this._startTime = performance.now()

    const animate = (now) => {
      if (!this._isRunning) return

      const elapsed = now - this._startTime
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height)

      let allFinished = true

      // 第一步：计算所有 slice 的原始位置
      const slicePositions = this.slices.map((slice, index) => {
        const sliceElapsed = elapsed - slice.staggerDelay
        const sourceX = slice.originalLeft - this.startBounds.left

        if (sliceElapsed < 0) {
          return { slice, sliceElapsed, sourceX, notStarted: true }
        }

        const t = Math.min(1, sliceElapsed / this.duration)
        const eased = slice.path._getEasedProgress(t)
        const point = slice.path.getPointAt(t)
        const currentHeight = this.startBounds.height * (1 - eased)

        return {
          slice,
          sliceElapsed,
          sourceX,
          notStarted: false,
          t,
          eased,
          point,
          currentHeight,
          destX: point.x - slice.sliceWidth / 2,
          destWidth: slice.sliceWidth,
        }
      })

      // 第二步：调整位置消除间隙 - 每个 slice 的左边界对齐到前一个的右边界
      for (let i = 1; i < slicePositions.length; i++) {
        const curr = slicePositions[i]
        const prev = slicePositions[i - 1]

        if (!curr.notStarted && !prev.notStarted && curr.currentHeight > 0.5 && prev.currentHeight > 0.5) {
          const prevRightEdge = prev.destX + prev.destWidth
          const currRightEdge = curr.point.x + curr.slice.sliceWidth / 2
          // 拉伸：左边界对齐到前一个的右边界
          curr.destX = prevRightEdge
          curr.destWidth = Math.max(1, currRightEdge - curr.destX)
        }
      }

      // 第三步：绘制所有 slice
      for (const pos of slicePositions) {
        const { slice, sourceX } = pos

        if (pos.notStarted) {
          // 尚未开始：绘制原始切片
          this.ctx.drawImage(
            this.snapshot,
            sourceX, 0, slice.sliceWidth, this.startBounds.height,
            slice.originalLeft, this.startBounds.top, slice.sliceWidth, this.startBounds.height,
          )
          allFinished = false
          continue
        }

        const { currentHeight, destX, destWidth } = pos

        if (currentHeight > 0.5) {
          // 对齐到整数像素
          const alignedDestX = Math.floor(destX)
          const alignedDestY = Math.floor(pos.point.y - currentHeight)
          const alignedDestWidth = Math.ceil(destX + destWidth) - alignedDestX
          const alignedDestHeight = Math.max(1, Math.ceil(currentHeight))

          this.ctx.drawImage(
            this.snapshot,
            sourceX, 0, slice.sliceWidth, this.startBounds.height,
            alignedDestX, alignedDestY, alignedDestWidth, alignedDestHeight,
          )
          allFinished = false
        }
      }

      if (allFinished) {
        this._isRunning = false
        if (onComplete) onComplete()
        return
      }

      this._raf = requestAnimationFrame(animate)
    }

    this._raf = requestAnimationFrame(animate)
  }

  stop() {
    this._isRunning = false
    if (this._raf) cancelAnimationFrame(this._raf)
  }
}
