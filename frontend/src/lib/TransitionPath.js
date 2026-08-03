/**
 * TransitionPath —— 从 start 到 end 的贝塞尔过渡路径
 * pathFunction 定义路径形状（相对 start/end 的控制点），
 * timingFunction 定义时间缓动（标准 CSS cubic-bezier 控制点）。
 */
export class TransitionPath {
  /**
   * @param {Object} startPoint     - {x, y}
   * @param {Object} endPoint       - {x, y}
   * @param {Array}  pathFunction   - 路径贝塞尔 [x1, y1, x2, y2]，默认直线 [0,0,1,1]
   * @param {Array}  timingFunction - 时间贝塞尔 [x1, y1, x2, y2]，默认 ease [0.25,0.1,0.25,0.9]
   * @param {number} duration       - 过渡总时长（毫秒）
   */
  constructor(
    startPoint,
    endPoint,
    pathFunction = [0, 0, 1, 1],
    timingFunction = [0.25, 0.1, 0.25, 0.9],
    duration = 1000,
  ) {
    this.startPoint = { ...startPoint }
    this.endPoint = { ...endPoint }
    this.duration = Math.max(0, duration)

    this.pathFunction = [...pathFunction]
    this.timingFunction = [...timingFunction]

    // 计算向量差，用于缩放路径
    this.dx = this.endPoint.x - this.startPoint.x
    this.dy = this.endPoint.y - this.startPoint.y

    // 预计算路径的实际控制点（已适配 start/end）
    this._pathP0 = this.startPoint
    this._pathP1 = {
      x: this.startPoint.x + this.pathFunction[0] * this.dx,
      y: this.startPoint.y + this.pathFunction[1] * this.dy,
    }
    this._pathP2 = {
      x: this.startPoint.x + this.pathFunction[2] * this.dx,
      y: this.startPoint.y + this.pathFunction[3] * this.dy,
    }
    this._pathP3 = this.endPoint

    // timingFunction 控制点（固定 0→1）
    this._timingP0 = { x: 0, y: 0 }
    this._timingP1 = { x: this.timingFunction[0], y: this.timingFunction[1] }
    this._timingP2 = { x: this.timingFunction[2], y: this.timingFunction[3] }
    this._timingP3 = { x: 1, y: 1 }

    this._startTime = null
    this._isRunning = false
    this._currentPoint = { ...this.startPoint }
    this._animationFrame = null
  }

  /**
   * 获取完整路径点数组（适合 Canvas/SVG 一次性绘制曲线）
   * @param {number} segments - 分段数量，默认 120
   * @returns {Array<{x, y}>}
   */
  getFullPath(segments = 120) {
    const points = []
    for (let i = 0; i <= segments; i++) {
      const t = i / segments
      points.push(this.getPointAt(t))
    }
    return points
  }

  /**
   * 根据归一化进度 t (0~1) 获取对应坐标
   * @param {number} t - 进度值 [0, 1]
   * @returns {{x, y}}
   */
  getPointAt(t) {
    t = Math.max(0, Math.min(1, t))
    // 计算缓动后的 u
    const u = this._getEasedProgress(t)
    // 根据 u 计算路径上的实际点
    return this._cubicBezierPoint(u, this._pathP0, this._pathP1, this._pathP2, this._pathP3)
  }

  /**
   * 开始动画
   * @param {Function} onUpdate    - 每帧回调，参数为当前点 {x, y}
   * @param {Function} [onComplete] - 动画结束回调
   */
  start(onUpdate, onComplete = null) {
    if (this._isRunning) return
    this._isRunning = true
    this._startTime = performance.now()

    const animate = (now) => {
      if (!this._isRunning) return
      const elapsed = now - this._startTime
      let progress = this.duration > 0 ? Math.min(elapsed / this.duration, 1) : 1

      if (progress >= 1) {
        this._currentPoint = { ...this.endPoint }
        onUpdate(this._currentPoint, progress)
        this._isRunning = false
        if (onComplete) onComplete(progress)
        return
      }

      this._currentPoint = this.getPointAt(progress)
      onUpdate(this._currentPoint, progress)
      this._animationFrame = requestAnimationFrame(animate)
    }

    this._animationFrame = requestAnimationFrame(animate)
  }

  /** 停止动画 */
  stop() {
    this._isRunning = false
    if (this._animationFrame) {
      cancelAnimationFrame(this._animationFrame)
      this._animationFrame = null
    }
  }

  /**
   * 立即跳转到指定进度
   * @param {number} t - 进度 [0, 1]
   * @param {Function} [onUpdate]
   */
  seek(t, onUpdate = null) {
    const point = this.getPointAt(t)
    this._currentPoint = point
    if (onUpdate) onUpdate(point)
  }

  /** 当前动画是否运行中 */
  isRunning() {
    return this._isRunning
  }

  // ====================== 内部私有方法 ======================

  /** 计算 timingFunction 的 y 值作为缓动进度（0~1） */
  _getEasedProgress(t) {
    return this._cubicBezierY(t, this._timingP0, this._timingP1, this._timingP2, this._timingP3)
  }

  /** 三次贝塞尔点 */
  _cubicBezier(t, p0, p1, p2, p3) {
    const u = 1 - t
    const tt = t * t
    const uu = u * u
    const uuu = uu * u
    const ttt = tt * t

    return {
      x: uuu * p0.x + 3 * uu * t * p1.x + 3 * u * tt * p2.x + ttt * p3.x,
      y: uuu * p0.y + 3 * uu * t * p1.y + 3 * u * tt * p2.y + ttt * p3.y,
    }
  }

  _cubicBezierY(t, p0, p1, p2, p3) {
    const point = this._cubicBezier(t, p0, p1, p2, p3)
    return Math.max(0, Math.min(1, point.y))
  }

  _cubicBezierPoint(t, p0, p1, p2, p3) {
    return this._cubicBezier(t, p0, p1, p2, p3)
  }
}
