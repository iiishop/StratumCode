<script setup>
import { computed, ref, watch } from 'vue'
import { BaseEdge, EdgeLabelRenderer, getBezierPath, MarkerType, Position, VueFlow } from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

const props = defineProps({
  workspace: { type: Object, default: null },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['open-lsp'])

const graph = ref(null)
const loading = ref(false)
const error = ref('')
const loaded = ref(false)
const search = ref('')
const semanticMode = ref('fast')
const showBuiltin = ref(false)
const showUnresolved = ref(true)
const showExternal = ref(false)
const selected = ref(null)
const nodePositions = ref({})

const projectNodes = computed(() => (graph.value?.nodes || []).filter(node => node.kind === 'project'))
const edges = computed(() => graph.value?.edges || [])
const diagnostics = computed(() => graph.value?.diagnostics || [])
const semanticStatus = computed(() => graph.value?.meta?.semantic_status || null)

const nodeDegree = computed(() => {
  const degree = new Map()
  for (const edge of edges.value) {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1)
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1)
  }
  return degree
})

const filteredGraphNodes = computed(() => {
  const q = search.value.trim().toLowerCase()
  let nodes = graph.value?.nodes || []
  nodes = nodes.filter(node => {
    if (!showBuiltin.value && node.kind === 'builtin_call') return false
    if (!showExternal.value && node.kind === 'external_member_call') return false
    if (!showUnresolved.value && node.kind === 'unresolved') return false
    if (!q) return true
    return [
      node.name,
      node.signature,
      node.file,
      node.language,
      node.kind,
    ].some(value => String(value || '').toLowerCase().includes(q))
  })
  if (q) return nodes.slice(0, 260)
  return [...nodes]
    .sort((a, b) => (nodeDegree.value.get(b.id) || 0) - (nodeDegree.value.get(a.id) || 0))
    .slice(0, 180)
})

const visibleIdSet = computed(() => new Set(filteredGraphNodes.value.map(node => node.id)))

const visibleGraphEdges = computed(() => edges.value
  .filter(edge => visibleIdSet.value.has(edge.source) && visibleIdSet.value.has(edge.target))
  .slice(0, 600))

const visibleSelfEdges = computed(() => visibleGraphEdges.value.filter(edge => edge.source === edge.target))

const visibleCallEdges = computed(() => visibleGraphEdges.value.filter(edge => edge.source !== edge.target))

const aggregatedCallEdges = computed(() => aggregateCallEdges(visibleCallEdges.value))

const selfEdgesByNode = computed(() => {
  const byNode = new Map()
  for (const edge of visibleSelfEdges.value) {
    if (!byNode.has(edge.source)) byNode.set(edge.source, [])
    byNode.get(edge.source).push(edge)
  }
  return byNode
})

const flowNodes = computed(() => {
  const positions = layoutGraph(filteredGraphNodes.value, aggregatedCallEdges.value)
  const selfEdgeMap = selfEdgesByNode.value
  const functionNodes = filteredGraphNodes.value.map((node, index) => ({
    id: node.id,
    type: 'functionBlock',
    position: nodePositions.value[node.id] || positions.get(node.id) || { x: index * 330, y: 0 },
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
    zIndex: 10,
    data: {
      raw: node,
      degree: nodeDegree.value.get(node.id) || 0,
      selfEdges: selfEdgeMap.get(node.id) || [],
      location: nodeLocation(node),
      summary: docSummary(node.doc),
    },
    class: [
      'structure-flow-node',
      `structure-flow-node--${node.kind}`,
      `structure-flow-node--lang-${safeClass(node.language)}`,
      node.doc?.summary ? 'structure-flow-node--documented' : '',
    ].filter(Boolean).join(' '),
  }))
  return [
    ...moduleGroupNodes(functionNodes),
    ...functionNodes,
  ]
})

const flowEdges = computed(() => aggregatedCallEdges.value.map(edge => ({
    id: edge.id,
    type: 'callEdge',
    source: edge.source,
    target: edge.target,
    animated: edge.kind === 'dynamic_possible' || edge.kind === 'runtime_observed',
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: isEdgeRelatedToSelectedNode(edge) ? '#315f9c' : '#7a8798',
      width: 18,
      height: 18,
    },
    data: { raw: edge },
    class: [
      'structure-flow-edge',
      `structure-flow-edge--${edge.kind}`,
      edge.calls.length > 1 ? 'structure-flow-edge--multi' : '',
      isEdgeRelatedToSelectedNode(edge) ? 'structure-flow-edge--active' : '',
    ].filter(Boolean).join(' '),
  })))

const stats = computed(() => ({
  functions: projectNodes.value.length,
  calls: graph.value?.meta?.call_count || 0,
  visibleNodes: filteredGraphNodes.value.length,
  visibleEdges: flowEdges.value.length + visibleSelfEdges.value.length,
}))

const semanticBadge = computed(() => {
  const status = semanticStatus.value
  if (!status || status.mode !== 'lsp') return null
  if (status.used && status.error) {
    const languages = status.disabled_languages?.length ? ` Disabled: ${status.disabled_languages.join(', ')}.` : ''
    return {
      kind: 'warn',
      label: status.server ? `LSP partial ${status.server}` : 'LSP partial',
      detail: `${status.resolved || 0} definitions resolved from ${status.requests || 0} requests. ${status.error}.${languages} Using fallback where needed.`,
    }
  }
  if (status.used) {
    return {
      kind: 'ok',
      label: status.server ? `LSP ${status.server}` : 'LSP active',
      detail: `${status.resolved || 0} definitions resolved from ${status.requests || 0} requests.`,
    }
  }
  if (!status.attempted) {
    return {
      kind: 'idle',
      label: 'LSP idle',
      detail: 'No visible call site required semantic definition lookup.',
    }
  }
  return {
    kind: 'warn',
    label: 'LSP fallback',
    detail: `${status.error || 'No matching enabled LSP server responded.'} Using name-index fallback. Open the LSP page to install or enable a server for this language.`,
  }
})

watch(() => props.active, (active) => {
  if (active && !loaded.value && !loading.value) loadGraph()
}, { immediate: true })

watch(semanticMode, () => {
  if (props.active && loaded.value) refresh()
})

async function loadGraph() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ semantic: semanticMode.value })
    const res = await fetch(`/api/code-structure/functions?${params}`)
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || 'Failed to load code structure')
    graph.value = data
    loaded.value = true
  } catch (reason) {
    error.value = reason.message || 'Failed to load code structure'
  } finally {
    loading.value = false
  }
}

function refresh() {
  loaded.value = false
  selected.value = null
  nodePositions.value = {}
  loadGraph()
}

function safeClass(value) {
  return String(value || 'unknown').toLowerCase().replace(/[^a-z0-9_-]+/g, '-')
}

function nodeLocation(node) {
  return node.file ? `${node.file}:${node.line || 1}` : node.kind
}

function docSummary(doc) {
  return doc?.summary || doc?.description || ''
}

function aggregateCallEdges(callEdges) {
  const groups = new Map()
  for (const edge of callEdges) {
    const key = `${edge.source}\u0000${edge.target}`
    const current = groups.get(key)
    if (!current) {
      groups.set(key, {
        ...edge,
        id: `edge-group:${stableHash(key)}`,
        calls: [edge],
        order: edge.order,
      })
      continue
    }
    current.calls.push(edge)
    current.order = Math.min(current.order, edge.order)
    current.confidence = Math.max(current.confidence || 0, edge.confidence || 0)
    if (current.kind !== edge.kind) current.kind = 'mixed'
  }
  return [...groups.values()].map(edge => ({
    ...edge,
    calls: edge.calls.sort((a, b) => (a.order || 0) - (b.order || 0)),
  }))
}

function hasDoc(doc) {
  return Boolean(doc?.summary || doc?.description || doc?.params?.length || doc?.returns?.description || doc?.raw)
}

function moduleGroupNodes(functionNodes) {
  const groups = new Map()
  const paddingX = 28
  const paddingTop = 44
  const paddingBottom = 24
  const nodeWidth = 254
  const nodeHeight = 126
  for (const node of functionNodes) {
    const file = node.data.raw.file || '(external)'
    const current = groups.get(file) || {
      file,
      language: node.data.raw.language || '',
      count: 0,
      minX: Number.POSITIVE_INFINITY,
      minY: Number.POSITIVE_INFINITY,
      maxX: Number.NEGATIVE_INFINITY,
      maxY: Number.NEGATIVE_INFINITY,
    }
    current.count += 1
    current.minX = Math.min(current.minX, node.position.x)
    current.minY = Math.min(current.minY, node.position.y)
    current.maxX = Math.max(current.maxX, node.position.x + nodeWidth)
    current.maxY = Math.max(current.maxY, node.position.y + nodeHeight)
    groups.set(file, current)
  }
  return [...groups.values()]
    .filter(group => group.count > 1 && Number.isFinite(group.minX))
    .map(group => ({
      id: `module:${stableHash(group.file)}`,
      type: 'moduleGroup',
      position: {
        x: group.minX - paddingX,
        y: group.minY - paddingTop,
      },
      draggable: false,
      selectable: false,
      focusable: false,
      zIndex: -10,
      data: {
        file: group.file,
        label: moduleLabel(group.file),
        language: group.language,
        count: group.count,
      },
      style: {
        width: `${group.maxX - group.minX + paddingX * 2}px`,
        height: `${group.maxY - group.minY + paddingTop + paddingBottom}px`,
      },
      class: [
        'structure-module-group',
        `structure-module-group--lang-${safeClass(group.language)}`,
      ].join(' '),
    }))
}

function moduleLabel(file) {
  const parts = String(file || '').split('/')
  return parts.slice(-2).join('/') || file
}

function layoutGraph(nodes, graphEdges) {
  if (!nodes.length) return new Map()
  const groups = fileLayoutGroups(nodes, graphEdges)
  return moduleFirstLayout(groups)
}

function fileLayoutGroups(nodes, graphEdges) {
  const groups = new Map()
  for (const node of nodes) {
    const file = node.file || '(external)'
    if (!groups.has(file)) {
      groups.set(file, {
        id: file,
        nodes: [],
        internalEdges: [],
        incoming: new Set(),
        outgoing: new Set(),
        nodeOffsets: new Map(),
        width: 0,
        height: 0,
        rank: 0,
      })
    }
    groups.get(file).nodes.push(node)
  }

  const nodeToFile = new Map(nodes.map(node => [node.id, node.file || '(external)']))
  for (const edge of graphEdges) {
    const sourceFile = nodeToFile.get(edge.source)
    const targetFile = nodeToFile.get(edge.target)
    if (!sourceFile || !targetFile) continue
    if (sourceFile === targetFile) {
      groups.get(sourceFile)?.internalEdges.push(edge)
      continue
    }
    groups.get(sourceFile)?.outgoing.add(targetFile)
    groups.get(targetFile)?.incoming.add(sourceFile)
  }

  for (const group of groups.values()) {
    group.nodes.sort(compareLayoutNodes)
    const layout = internalModuleLayout(group)
    group.nodeOffsets = layout.offsets
    group.width = layout.width
    group.height = layout.height
  }
  assignModuleRanks(groups)
  return [...groups.values()].sort(compareLayoutGroups)
}

function assignModuleRanks(groups) {
  const pendingIncoming = new Map([...groups.values()].map(group => [group.id, group.incoming.size]))
  const queue = [...groups.values()]
    .filter(group => group.incoming.size === 0)
    .sort(compareLayoutGroups)
  while (queue.length) {
    const group = queue.shift()
    for (const targetId of group.outgoing) {
      const target = groups.get(targetId)
      if (!target) continue
      target.rank = Math.max(target.rank, group.rank + 1)
      pendingIncoming.set(targetId, (pendingIncoming.get(targetId) || 0) - 1)
      if (pendingIncoming.get(targetId) === 0) queue.push(target)
    }
    queue.sort(compareLayoutGroups)
  }
  const maxRank = Math.max(0, ...[...groups.values()].map(group => group.rank))
  for (const group of groups.values()) {
    if (pendingIncoming.get(group.id) > 0) {
      group.rank = group.incoming.size || group.outgoing.size ? maxRank + 1 : maxRank + 2
    }
  }
}

function moduleFirstLayout(groups) {
  const ranks = new Map()
  for (const group of groups) {
    if (!ranks.has(group.rank)) ranks.set(group.rank, [])
    ranks.get(group.rank).push(group)
  }
  const positions = new Map()
  const groupPositions = new Map()
  const horizontalGap = 92
  const verticalGap = 132
  let y = 0
  for (const rankKey of [...ranks.keys()].sort((a, b) => a - b)) {
    const rankGroups = ranks.get(rankKey)
      .sort((a, b) => moduleParentAnchor(a, groupPositions) - moduleParentAnchor(b, groupPositions) || compareLayoutGroups(a, b))
    const totalWidth = rankGroups.reduce((sum, group) => sum + group.width, 0) + Math.max(0, rankGroups.length - 1) * horizontalGap
    let x = -totalWidth / 2
    let rankHeight = 0
    for (const group of rankGroups) {
      const anchor = moduleParentAnchor(group, groupPositions)
      const naturalX = Number.isFinite(anchor) ? anchor - group.width / 2 : x
      const adjustedX = Math.max(x, blend(x, naturalX, 0.44))
      const offset = layoutOffset(group.id)
      groupPositions.set(group.id, {
        x: adjustedX + offset.x * 0.35,
        y: y + offset.y * 0.45,
        group,
      })
      x = adjustedX + group.width + horizontalGap
      rankHeight = Math.max(rankHeight, group.height)
    }
    y += rankHeight + verticalGap
  }

  for (const group of groups) {
    const origin = groupPositions.get(group.id) || { x: 0, y: 0 }
    placeNodesInsideModule(group, origin, positions)
  }
  return positions
}

function placeNodesInsideModule(group, origin, positions) {
  const left = 28
  const top = 44
  for (const node of group.nodes) {
    const offset = group.nodeOffsets.get(node.id) || { x: 0, y: 0 }
    positions.set(node.id, {
      x: origin.x + left + offset.x,
      y: origin.y + top + offset.y,
    })
  }
}

function internalModuleLayout(group) {
  const nodeWidth = 254
  const nodeHeight = 126
  const paddingX = 56
  const paddingY = 68
  const offsets = group.internalEdges.length
    ? internalCallLayout(group, nodeWidth, nodeHeight)
    : internalScatterLayout(group.nodes, nodeWidth, nodeHeight)
  relaxInternalCollisions(group.nodes, offsets, nodeWidth, nodeHeight)

  const bounds = [...offsets.values()].reduce((box, point) => ({
    minX: Math.min(box.minX, point.x),
    minY: Math.min(box.minY, point.y),
    maxX: Math.max(box.maxX, point.x + nodeWidth),
    maxY: Math.max(box.maxY, point.y + nodeHeight),
  }), {
    minX: Number.POSITIVE_INFINITY,
    minY: Number.POSITIVE_INFINITY,
    maxX: Number.NEGATIVE_INFINITY,
    maxY: Number.NEGATIVE_INFINITY,
  })

  for (const point of offsets.values()) {
    point.x -= bounds.minX
    point.y -= bounds.minY
  }
  return {
    offsets,
    width: Math.max(304, bounds.maxX - bounds.minX + paddingX),
    height: Math.max(206, bounds.maxY - bounds.minY + paddingY),
  }
}

function internalCallLayout(group, nodeWidth, nodeHeight) {
  const graph = internalAdjacency(group)
  const rank = internalRanks(group, graph.incoming, graph.outgoing)
  const ranks = new Map()
  for (const node of group.nodes) {
    const value = rank.get(node.id) || 0
    if (!ranks.has(value)) ranks.set(value, [])
    ranks.get(value).push(node)
  }

  const offsets = new Map()
  const verticalGap = nodeHeight + 104
  const rankKeys = [...ranks.keys()].sort((a, b) => a - b)
  for (const rankKey of rankKeys) {
    const nodes = ranks.get(rankKey).sort((a, b) => {
      const anchorDiff = parentAnchor(a, graph.incoming, offsets) - parentAnchor(b, graph.incoming, offsets)
      return anchorDiff || compareLayoutNodes(a, b)
    })
    const centerShift = rankKey % 2 === 0 ? 0 : nodeWidth * 0.42
    nodes.forEach((node, index) => {
      const seed = stableHash(node.id)
      const anchor = parentAnchor(node, graph.incoming, offsets)
      const fan = organicFanOffset(index, nodes.length, nodeWidth)
      offsets.set(node.id, {
        x: anchor + fan + centerShift + ((seed % 53) - 26) * 1.2,
        y: rankKey * verticalGap + ((Math.floor(seed / 53) % 43) - 21) * 1.1 + (index % 2) * 34,
      })
    })
  }
  return offsets
}

function internalAdjacency(group) {
  const incoming = new Map(group.nodes.map(node => [node.id, []]))
  const outgoing = new Map(group.nodes.map(node => [node.id, []]))
  for (const edge of group.internalEdges) {
    if (edge.source === edge.target) continue
    if (!incoming.has(edge.target) || !outgoing.has(edge.source)) continue
    incoming.get(edge.target).push(edge.source)
    outgoing.get(edge.source).push(edge.target)
  }
  return { incoming, outgoing }
}

function internalRanks(group, incoming, outgoing) {
  const pending = new Map(group.nodes.map(node => [node.id, (incoming.get(node.id) || []).length]))
  const rank = new Map()
  const queue = group.nodes
    .filter(node => (pending.get(node.id) || 0) === 0)
    .sort(compareLayoutNodes)
  for (const node of queue) rank.set(node.id, 0)
  while (queue.length) {
    const node = queue.shift()
    const base = rank.get(node.id) || 0
    for (const target of outgoing.get(node.id) || []) {
      rank.set(target, Math.max(rank.get(target) || 0, base + 1))
      pending.set(target, (pending.get(target) || 0) - 1)
      if (pending.get(target) === 0) {
        const targetNode = group.nodes.find(item => item.id === target)
        if (targetNode) queue.push(targetNode)
      }
    }
    queue.sort(compareLayoutNodes)
  }
  const fallbackRank = Math.max(0, ...rank.values()) + 1
  for (const node of group.nodes) {
    if (!rank.has(node.id)) rank.set(node.id, fallbackRank)
  }
  return rank
}

function parentAnchor(node, incoming, offsets) {
  const anchors = (incoming.get(node.id) || [])
    .map(id => offsets.get(id)?.x)
    .filter(value => Number.isFinite(value))
  if (!anchors.length) return 0
  return anchors.reduce((sum, value) => sum + value, 0) / anchors.length
}

function organicFanOffset(index, count, nodeWidth) {
  if (count <= 1) return 0
  const lane = Math.ceil((index + 1) / 2)
  const direction = index % 2 === 0 ? -1 : 1
  const spread = nodeWidth * 0.92 + 74
  return direction * lane * spread
}

function internalScatterLayout(nodes, nodeWidth, nodeHeight) {
  const offsets = new Map()
  const angle = Math.PI * (3 - Math.sqrt(5))
  nodes.forEach((node, index) => {
    const seed = stableHash(node.id)
    const radius = Math.sqrt(index) * (nodeWidth * 0.72)
    const theta = index * angle + (seed % 41) / 41
    offsets.set(node.id, {
      x: Math.cos(theta) * radius + (index % 3 - 1) * nodeWidth * 0.28,
      y: Math.sin(theta) * radius * 0.72 + index * 29 + ((seed % 31) - 15),
    })
  })
  return offsets
}

function relaxInternalCollisions(nodes, offsets, nodeWidth, nodeHeight) {
  const gapX = 38
  const gapY = 28
  for (let pass = 0; pass < 7; pass += 1) {
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = offsets.get(nodes[i].id)
        const b = offsets.get(nodes[j].id)
        if (!a || !b) continue
        const overlapX = nodeWidth + gapX - Math.abs(a.x - b.x)
        const overlapY = nodeHeight + gapY - Math.abs(a.y - b.y)
        if (overlapX <= 0 || overlapY <= 0) continue
        if (overlapX < overlapY) {
          const push = overlapX / 2
          if (a.x <= b.x) {
            a.x -= push
            b.x += push
          } else {
            a.x += push
            b.x -= push
          }
        } else {
          const push = overlapY / 2
          if (a.y <= b.y) {
            a.y -= push
            b.y += push
          } else {
            a.y += push
            b.y -= push
          }
        }
      }
    }
  }
}

function compareLayoutGroups(a, b) {
  return b.nodes.length - a.nodes.length
    || Math.min(...a.nodes.map(node => node.line || 0)) - Math.min(...b.nodes.map(node => node.line || 0))
    || String(a.id).localeCompare(String(b.id))
}

function groupCenter(group, position) {
  return position.x + group.width / 2
}

function moduleParentAnchor(group, positions) {
  const anchors = [...group.incoming]
    .map(id => {
      const parent = positions.get(id)
      const parentGroup = parent?.group
      return parent && parentGroup ? groupCenter(parentGroup, parent) : null
    })
    .filter(value => Number.isFinite(value))
    .sort((a, b) => a - b)
  if (!anchors.length) return Number.POSITIVE_INFINITY
  return anchors[Math.floor(anchors.length / 2)]
}

function layoutOffset(id) {
  const seed = stableHash(id)
  return {
    x: ((seed % 97) - 48) * 0.85,
    y: (((Math.floor(seed / 97) % 43) - 21) * 0.7),
  }
}

function stableHash(value) {
  let hash = 0
  for (const char of String(value || '')) {
    hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0
  }
  return Math.abs(hash)
}

function blend(a, b, ratio) {
  return a * (1 - ratio) + b * ratio
}

function compareLayoutNodes(a, b) {
  const kindOrder = { project: 0, static_resolved: 0, builtin_call: 1, external_member_call: 2, unresolved: 3 }
  return (kindOrder[a.kind] ?? 4) - (kindOrder[b.kind] ?? 4)
    || (nodeDegree.value.get(b.id) || 0) - (nodeDegree.value.get(a.id) || 0)
    || String(a.file || '').localeCompare(String(b.file || ''))
    || (a.line || 0) - (b.line || 0)
    || String(a.name || '').localeCompare(String(b.name || ''))
}

function isEdgeRelatedToSelectedNode(edge) {
  const rawEdge = edge?.data?.raw || edge
  return selected.value?.type === 'node'
    && (rawEdge.source === selected.value.item?.id || rawEdge.target === selected.value.item?.id)
}

function isEdgeSelectedOrRelated(edge) {
  const rawEdge = edge?.data?.raw || edge
  return isEdgeRelatedToSelectedNode(rawEdge)
    || (selected.value?.type === 'edge' && selected.value.item?.id === rawEdge.id)
}

function edgePath(edge) {
  return getBezierPath({
    sourceX: edge.sourceX,
    sourceY: edge.sourceY,
    sourcePosition: edge.sourcePosition,
    targetX: edge.targetX,
    targetY: edge.targetY,
    targetPosition: edge.targetPosition,
  })
}

function edgeLabelStyle(edge) {
  const [, labelX, labelY] = edgePath(edge)
  return {
    transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
  }
}

function edgeCallLabelStyle(edge, index, total) {
  if (total <= 1) return edgeLabelStyle(edge)
  const t = (index + 1) / (total + 1)
  const dx = edge.targetX - edge.sourceX
  const dy = edge.targetY - edge.sourceY
  const length = Math.max(1, Math.hypot(dx, dy))
  const perpendicularX = -dy / length
  const perpendicularY = dx / length
  const offset = (index % 2 === 0 ? -1 : 1) * Math.min(44, 18 + total * 4)
  const x = edge.sourceX + dx * t + perpendicularX * offset
  const y = edge.sourceY + dy * t + perpendicularY * offset
  return {
    transform: `translate(-50%, -50%) translate(${x}px, ${y}px)`,
  }
}

function highlightedCallLine(edge) {
  const raw = edge?.data?.raw || edge
  const line = raw?.line_text || raw?.call_text || ''
  const escaped = escapeHtml(line)
  return escaped
    .replace(/\b(async|await|return|if|else|for|while|switch|case|catch|try|const|let|var|function|def|class|public|private|static|new|import|from)\b/g, '<span class="tok tok--kw">$1</span>')
    .replace(/(&quot;.*?&quot;|&#39;.*?&#39;)/g, '<span class="tok tok--str">$1</span>')
    .replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok tok--num">$1</span>')
    .replace(/([A-Za-z_$][A-Za-z0-9_$.]*)(?=\s*\()/g, '<span class="tok tok--fn">$1</span>')
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function eventElement(payload, fallback) {
  return payload?.node || payload?.edge || fallback || payload
}

function onNodeClick(payload, node) {
  const item = eventElement(payload, node)
  selected.value = { type: 'node', item: item?.data?.raw || item }
}

function onEdgeClick(payload, edge) {
  const item = eventElement(payload, edge)
  selected.value = { type: 'edge', item: item?.data?.raw || item }
}

function selectSelfEdge(edge) {
  selected.value = { type: 'edge', item: edge }
}

function onNodeDrag(payload) {
  const moved = payload?.nodes?.length ? payload.nodes : [payload?.node].filter(Boolean)
  if (!moved.length) return
  const next = { ...nodePositions.value }
  for (const node of moved) {
    if (node.type !== 'functionBlock' || !node.position) continue
    next[node.id] = { x: node.position.x, y: node.position.y }
  }
  nodePositions.value = next
}
</script>

<template>
  <section class="structure-panel">
    <header class="structure-panel__head">
      <div>
        <p class="structure-panel__eyebrow">Programmatic map</p>
        <h1>Code Structure</h1>
      </div>
      <div class="structure-panel__actions">
        <span class="structure-panel__workspace">{{ workspace?.name || workspace?.path || 'No workspace' }}</span>
        <button type="button" :disabled="loading" @click="refresh">
          {{ loading ? 'Scanning' : 'Refresh' }}
        </button>
      </div>
    </header>

    <div class="structure-panel__toolbar">
      <input v-model="search" type="search" placeholder="Search function, file, language..." />
      <label>
        <input v-model="showBuiltin" type="checkbox" />
        Builtins
      </label>
      <label>
        <input v-model="showExternal" type="checkbox" />
        External
      </label>
      <label>
        <input v-model="showUnresolved" type="checkbox" />
        Unresolved
      </label>
      <label>
        <input v-model="semanticMode" true-value="lsp" false-value="fast" type="checkbox" />
        LSP semantic
      </label>
      <span
        v-if="semanticBadge"
        class="structure-panel__semantic-status"
        :class="`is-${semanticBadge.kind}`"
        :title="semanticBadge.detail"
      >
        <span>{{ semanticBadge.label }}</span>
        <button
          v-if="semanticBadge.kind === 'warn'"
          type="button"
          @click="emit('open-lsp')"
        >
          Open LSP
        </button>
      </span>
      <div class="structure-panel__stats">
        <span>{{ stats.functions }} functions</span>
        <span>{{ stats.calls }} calls</span>
        <span>{{ stats.visibleNodes }} nodes shown</span>
      </div>
    </div>

    <div v-if="error" class="structure-panel__notice structure-panel__notice--error">{{ error }}</div>
    <div v-else-if="loading" class="structure-panel__notice">Scanning workspace symbols and call sites...</div>

    <div v-else class="structure-panel__body">
      <div class="structure-panel__canvas">
        <VueFlow
          :nodes="flowNodes"
          :edges="flowEdges"
          fit-view-on-init
          :nodes-draggable="true"
          :elements-selectable="true"
          @node-click="onNodeClick"
          @node-drag="onNodeDrag"
          @edge-click="onEdgeClick"
        >
          <template #node-moduleGroup="{ data }">
            <div class="module-group">
              <div class="module-group__label">
                <span>{{ data.label }}</span>
                <strong>{{ data.count }} functions</strong>
              </div>
            </div>
          </template>
          <template #node-functionBlock="{ data, selected: isSelected }">
            <div class="function-node" :class="{ 'function-node--selected': isSelected }">
              <div class="function-node__stripe" />
              <div class="function-node__top">
                <span class="function-node__shape" />
                <span class="function-node__kind">{{ data.raw.kind }}</span>
                <span class="function-node__lang">{{ data.raw.language || 'external' }}</span>
              </div>
              <button
                v-if="data.selfEdges.length"
                class="function-node__self-call nodrag"
                type="button"
                :title="`${data.selfEdges.length} self call${data.selfEdges.length > 1 ? 's' : ''}`"
                @click.stop="selectSelfEdge(data.selfEdges[0])"
              >
                ↻ {{ data.selfEdges.length }}
              </button>
              <h3>{{ data.raw.name }}</h3>
              <p class="function-node__signature">{{ data.raw.signature }}</p>
              <p v-if="data.summary" class="function-node__summary">{{ data.summary }}</p>
              <div class="function-node__meta">
                <span>{{ data.location }}</span>
                <span>{{ data.degree }} links</span>
              </div>
            </div>
          </template>
          <template #edge-callEdge="edgeProps">
            <BaseEdge
              :id="edgeProps.id"
              :path="edgePath(edgeProps)[0]"
              :marker-end="edgeProps.markerEnd"
              :interaction-width="18"
            />
            <EdgeLabelRenderer v-if="edgeProps.data.raw.calls.length > 1 || isEdgeSelectedOrRelated(edgeProps.data.raw)">
              <template v-if="isEdgeSelectedOrRelated(edgeProps.data.raw)">
                <div
                  v-for="(call, index) in edgeProps.data.raw.calls"
                  :key="call.id"
                  class="call-edge-label"
                  :style="edgeCallLabelStyle(edgeProps, index, edgeProps.data.raw.calls.length)"
                >
                  <span class="call-edge-label__order">#{{ call.order }}</span>
                  <code v-html="highlightedCallLine(call)" />
                </div>
              </template>
              <div v-else class="call-edge-label call-edge-label--compact" :style="edgeLabelStyle(edgeProps)">
                x{{ edgeProps.data.raw.calls.length }}
              </div>
            </EdgeLabelRenderer>
          </template>
        </VueFlow>
      </div>

      <aside class="structure-panel__details">
        <template v-if="selected?.type === 'node'">
          <p class="structure-panel__detail-kicker">Function</p>
          <h2>{{ selected.item.name }}</h2>
          <details class="structure-panel__section" open>
            <summary>Signature</summary>
            <code>{{ selected.item.signature }}</code>
          </details>

          <details class="structure-panel__section" open>
            <summary>Documentation</summary>
            <template v-if="hasDoc(selected.item.doc)">
              <p v-if="selected.item.doc.summary" class="structure-panel__doc-summary">{{ selected.item.doc.summary }}</p>
              <p v-if="selected.item.doc.description" class="structure-panel__doc-body">{{ selected.item.doc.description }}</p>
            </template>
            <p v-else class="structure-panel__empty">No function comment found.</p>
          </details>

          <details class="structure-panel__section" open>
            <summary>Inputs</summary>
            <ul v-if="selected.item.doc?.params?.length" class="structure-panel__param-list">
              <li v-for="param in selected.item.doc.params" :key="param.name">
                <strong>{{ param.name }}</strong>
                <span v-if="param.type">{{ param.type }}</span>
                <em v-if="param.default">default {{ param.default }}</em>
                <p>{{ param.description || 'No parameter description.' }}</p>
              </li>
            </ul>
            <p v-else class="structure-panel__empty">No explicit parameters.</p>
          </details>

          <details class="structure-panel__section" open>
            <summary>Output</summary>
            <p v-if="selected.item.doc?.returns?.type || selected.item.doc?.returns?.description">
              <strong v-if="selected.item.doc.returns.type">{{ selected.item.doc.returns.type }}</strong>
              {{ selected.item.doc.returns.description || 'No return description.' }}
            </p>
            <p v-else class="structure-panel__empty">No return annotation or comment.</p>
          </details>

          <details v-if="selected.item.doc?.raw" class="structure-panel__section">
            <summary>Raw comment</summary>
            <pre>{{ selected.item.doc.raw }}</pre>
          </details>

          <details class="structure-panel__section">
            <summary>Metadata</summary>
            <dl>
              <dt>Location</dt>
              <dd>{{ selected.item.file || '-' }}{{ selected.item.line ? `:${selected.item.line}` : '' }}</dd>
              <dt>Language</dt>
              <dd>{{ selected.item.language }}</dd>
              <dt>Kind</dt>
              <dd>{{ selected.item.kind }}</dd>
              <dt>Provenance</dt>
              <dd>{{ selected.item.provenance?.join(' + ') || '-' }}</dd>
            </dl>
          </details>
        </template>

        <template v-else-if="selected?.type === 'edge'">
          <p class="structure-panel__detail-kicker">Call edge</p>
          <h2>
            {{ selected.item.calls?.length > 1 ? `${selected.item.calls.length} calls` : selected.item.line_text || selected.item.call_text }}
          </h2>
          <details class="structure-panel__section" open>
            <summary>Calls</summary>
            <ol class="structure-panel__call-list">
              <li v-for="call in selected.item.calls || [selected.item]" :key="call.id">
                <span>#{{ call.order }}</span>
                <code>{{ call.line_text || call.call_text }}</code>
                <small>{{ call.file }}:{{ call.range?.start_line || call.line || '-' }}</small>
              </li>
            </ol>
          </details>
          <details class="structure-panel__section" open>
            <summary>Metadata</summary>
            <dl>
              <dt>Kind</dt>
              <dd>{{ selected.item.kind }}</dd>
              <dt>Confidence</dt>
              <dd>{{ Math.round((selected.item.confidence || 0) * 100) }}%</dd>
              <dt>Provenance</dt>
              <dd>{{ selected.item.provenance?.join(' + ') || '-' }}</dd>
            </dl>
          </details>
        </template>

        <template v-else>
          <p class="structure-panel__detail-kicker">Graph</p>
          <h2>Function calls</h2>
          <p class="structure-panel__hint">Select a function block or a call edge to inspect location, order, confidence, and provenance.</p>
          <ul v-if="diagnostics.length" class="structure-panel__diagnostics">
            <li v-for="item in diagnostics.slice(0, 5)" :key="`${item.file}:${item.message}`">
              {{ item.level }}: {{ item.message }}
            </li>
          </ul>
        </template>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.structure-panel {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
  background: #f5f8fc;
}

.structure-panel__head,
.structure-panel__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid #dce5ef;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(14px);
}

.structure-panel__head {
  min-height: 70px;
  padding: 0 24px;
}

.structure-panel__eyebrow {
  margin: 0 0 3px;
  color: #68809b;
  font: 10px/1 var(--mono);
  text-transform: uppercase;
}

.structure-panel__head h1 {
  margin: 0;
  color: var(--text-h);
  font: 580 22px/1.1 var(--heading);
}

.structure-panel__actions,
.structure-panel__stats {
  display: flex;
  align-items: center;
  gap: 10px;
}

.structure-panel__workspace {
  max-width: 360px;
  overflow: hidden;
  color: #637891;
  font: 10px/1 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.structure-panel__actions button {
  height: 32px;
  padding: 0 12px;
  border: 1px solid #cfdbe8;
  border-radius: 6px;
  color: #173553;
  background: linear-gradient(180deg, #ffffff 0%, #f4f8fd 100%);
  box-shadow: 0 1px 2px rgba(31, 47, 70, 0.06);
  cursor: pointer;
}

.structure-panel__actions button:hover {
  border-color: var(--accent-border);
  background: var(--accent-bg);
}

.structure-panel__toolbar {
  min-height: 48px;
  padding: 0 18px;
  box-shadow: 0 8px 20px rgba(35, 55, 78, 0.04);
}

.structure-panel__toolbar input[type="search"] {
  width: min(360px, 34vw);
  height: 32px;
  padding: 0 11px;
  border: 1px solid #cfdbe8;
  border-radius: 6px;
  color: var(--text-h);
  background: #f8fbff;
  box-shadow: inset 0 1px 2px rgba(31, 47, 70, 0.04);
}

.structure-panel__toolbar label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #637891;
  font-size: 11px;
  white-space: nowrap;
}

.structure-panel__toolbar input[type="checkbox"] {
  accent-color: #315f9c;
}

.structure-panel__semantic-status {
  display: inline-flex;
  height: 24px;
  align-items: center;
  gap: 7px;
  padding: 0 8px;
  border: 1px solid #d4deea;
  border-radius: 999px;
  color: #526a84;
  background: #f8fbff;
  font: 10px/1 var(--mono);
  white-space: nowrap;
}

.structure-panel__semantic-status button {
  height: 18px;
  padding: 0 6px;
  border: 1px solid currentColor;
  border-radius: 999px;
  color: inherit;
  background: rgba(255, 255, 255, 0.62);
  font: 9px/1 var(--mono);
  cursor: pointer;
}

.structure-panel__semantic-status button:hover {
  background: rgba(255, 255, 255, 0.9);
}

.structure-panel__semantic-status.is-ok {
  border-color: #b9dcc8;
  color: #25734a;
  background: #eef8f2;
}

.structure-panel__semantic-status.is-warn {
  border-color: #ead1a8;
  color: #8a5d19;
  background: #fff7e8;
}

.structure-panel__semantic-status.is-idle {
  border-color: #d4deea;
  color: #66798d;
  background: #f3f6fa;
}

.structure-panel__stats {
  margin-left: auto;
  color: #637891;
  font: 10px/1 var(--mono);
}

.structure-panel__stats span {
  padding: 4px 7px;
  border: 1px solid #dce5ef;
  border-radius: 999px;
  background: #f8fbff;
}

.structure-panel__notice {
  margin: 18px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  background: #ffffff;
  font-size: 12px;
}

.structure-panel__notice--error {
  border-color: var(--err-border);
  color: var(--err);
  background: var(--err-bg);
}

.structure-panel__body {
  display: grid;
  min-height: 0;
  flex: 1;
  grid-template-columns: minmax(0, 1fr) 320px;
  background:
    linear-gradient(rgba(76, 98, 122, 0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(76, 98, 122, 0.06) 1px, transparent 1px),
    radial-gradient(circle at 20% 15%, rgba(63, 111, 189, 0.08), transparent 30%),
    #f4f8fc;
  background-size: 28px 28px, 28px 28px, auto, auto;
}

.structure-panel__canvas {
  min-width: 0;
  min-height: 0;
  position: relative;
}

.structure-panel__canvas::before {
  position: absolute;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  content: "";
  background:
    linear-gradient(90deg, rgba(244, 248, 252, 0.94), transparent 8%, transparent 92%, rgba(244, 248, 252, 0.94)),
    linear-gradient(rgba(244, 248, 252, 0.9), transparent 10%, transparent 90%, rgba(244, 248, 252, 0.9));
}

.structure-panel__canvas :deep(.vue-flow) {
  background: transparent;
}

.structure-panel__details {
  min-width: 0;
  overflow-y: auto;
  padding: 18px;
  border-left: 1px solid #dce5ef;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: -14px 0 28px rgba(35, 55, 78, 0.04);
}

.structure-panel__detail-kicker {
  margin: 0 0 6px;
  color: #68809b;
  font: 10px/1 var(--mono);
  text-transform: uppercase;
}

.structure-panel__details h2 {
  margin: 0 0 16px;
  color: var(--text-h);
  font: 600 17px/1.2 var(--heading);
}

.structure-panel__details dl {
  display: grid;
  gap: 10px;
  margin: 0;
}

.structure-panel__details dt {
  color: var(--text-muted);
  font: 10px/1 var(--mono);
}

.structure-panel__details dd {
  min-width: 0;
  margin: -6px 0 0;
  overflow-wrap: anywhere;
  color: var(--text);
  font-size: 11px;
}

.structure-panel__section {
  margin: 0 0 10px;
  border: 1px solid #d9e2ef;
  border-radius: 7px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbfe 100%);
  box-shadow: 0 1px 2px rgba(31, 47, 70, 0.04);
}

.structure-panel__section summary {
  display: flex;
  min-height: 34px;
  align-items: center;
  padding: 0 11px;
  color: #173553;
  font: 600 11px/1 var(--mono);
  cursor: pointer;
}

.structure-panel__section summary::marker {
  color: var(--text-muted);
}

.structure-panel__section > code,
.structure-panel__section > p,
.structure-panel__section > pre,
.structure-panel__section > dl,
.structure-panel__param-list {
  margin: 0;
  padding: 0 11px 12px;
}

.structure-panel__section code,
.structure-panel__section pre {
  display: block;
  overflow-x: auto;
  color: #18324f;
  font: 10px/1.5 var(--mono);
  white-space: pre-wrap;
}

.structure-panel__doc-summary {
  color: var(--text-h);
  font-size: 12px;
  line-height: 1.45;
}

.structure-panel__doc-body,
.structure-panel__empty {
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
}

.structure-panel__param-list {
  display: grid;
  gap: 8px;
  list-style: none;
}

.structure-panel__param-list li {
  display: grid;
  gap: 4px;
}

.structure-panel__param-list strong,
.structure-panel__section p strong {
  color: #18324f;
  font: 600 11px/1.2 var(--mono);
}

.structure-panel__param-list span,
.structure-panel__param-list em {
  width: fit-content;
  padding: 2px 6px;
  border-radius: 999px;
  color: #42526b;
  background: #eef3f8;
  font: 9px/1.2 var(--mono);
  font-style: normal;
}

.structure-panel__param-list p {
  margin: 0;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.45;
}

.structure-panel__hint,
.structure-panel__diagnostics {
  color: var(--text-muted);
  font-size: 11px;
}

.structure-panel__diagnostics {
  margin: 16px 0 0;
  padding-left: 16px;
}

:deep(.structure-flow-node) {
  width: 254px;
  border: 0;
  border-radius: 7px;
  color: var(--text-h);
  background: transparent;
  box-shadow: 0 16px 30px rgba(28, 44, 64, 0.11);
  font: 10px/1.35 var(--mono);
}

:deep(.structure-module-group) {
  width: auto;
  pointer-events: none;
  background: transparent;
  box-shadow: none;
}

.module-group {
  --module-lang-color: #5277b8;
  width: 100%;
  height: 100%;
  min-width: 304px;
  min-height: 206px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--module-lang-color) 24%, #cfdbe8);
  border-radius: 8px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--module-lang-color) 8%, #ffffff) 0 33px, transparent 33px),
    color-mix(in srgb, var(--module-lang-color) 5%, rgba(255, 255, 255, 0.52));
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.82),
    0 18px 34px rgba(31, 47, 70, 0.05);
}

.module-group__label {
  display: flex;
  height: 33px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 12px;
  border-bottom: 1px solid color-mix(in srgb, var(--module-lang-color) 16%, #dce5ef);
  color: color-mix(in srgb, var(--module-lang-color) 58%, #173553);
  font: 10px/1 var(--mono);
}

.module-group__label span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.module-group__label strong {
  flex: 0 0 auto;
  color: #6c7f96;
  font: 9px/1 var(--mono);
}

:deep(.structure-module-group--lang-python) .module-group {
  --module-lang-color: #3f6fbd;
}

:deep(.structure-module-group--lang-csharp) .module-group {
  --module-lang-color: #2f8f9d;
}

:deep(.structure-module-group--lang-rust) .module-group {
  --module-lang-color: #a85f2b;
}

:deep(.structure-module-group--lang-javascript) .module-group,
:deep(.structure-module-group--lang-typescript) .module-group {
  --module-lang-color: #b88716;
}

:deep(.structure-module-group--lang-vue) .module-group {
  --module-lang-color: #278f68;
}

:deep(.structure-module-group--lang-generic) .module-group {
  --module-lang-color: #7d6fa5;
}

.function-node {
  --function-lang-color: #5277b8;
  position: relative;
  min-height: 126px;
  overflow: hidden;
  padding: 11px 12px 10px 17px;
  border: 1px solid rgba(164, 180, 199, 0.78);
  border-radius: 7px;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.78), rgba(255, 255, 255, 0)),
    linear-gradient(180deg, #ffffff 0%, #f6faff 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.82);
  transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
}

.function-node:hover {
  border-color: color-mix(in srgb, var(--function-lang-color) 42%, #cfdbe8);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 14px 30px rgba(28, 44, 64, 0.12);
  transform: translateY(-1px);
}

.function-node--selected {
  outline: 2px solid color-mix(in srgb, var(--function-lang-color) 38%, transparent);
  outline-offset: 2px;
}

.function-node__stripe {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 5px;
  background: var(--function-lang-color);
  box-shadow: 0 0 18px color-mix(in srgb, var(--function-lang-color) 45%, transparent);
}

.function-node__top,
.function-node__meta {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.function-node__top {
  margin-bottom: 8px;
}

.function-node__shape {
  width: 10px;
  height: 10px;
  flex: 0 0 auto;
  border: 2px solid var(--function-lang-color);
  border-radius: 50%;
  background: #ffffff;
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--function-lang-color) 13%, transparent);
}

.function-node__kind,
.function-node__lang {
  max-width: 92px;
  overflow: hidden;
  padding: 2px 6px;
  border-radius: 999px;
  color: #42526b;
  background: rgba(238, 243, 248, 0.9);
  font: 9px/1.2 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.function-node__lang {
  margin-left: auto;
  color: color-mix(in srgb, var(--function-lang-color) 68%, #17243a);
  background: color-mix(in srgb, var(--function-lang-color) 13%, #ffffff);
}

.function-node__self-call {
  position: absolute;
  top: 41px;
  right: 10px;
  z-index: 2;
  height: 22px;
  padding: 0 7px;
  border: 1px solid color-mix(in srgb, var(--function-lang-color) 34%, #cfdbe8);
  border-radius: 999px;
  color: color-mix(in srgb, var(--function-lang-color) 76%, #14243a);
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 6px 14px rgba(31, 47, 70, 0.1);
  font: 10px/1 var(--mono);
  cursor: pointer;
}

.function-node__self-call:hover {
  background: color-mix(in srgb, var(--function-lang-color) 12%, #ffffff);
}

.function-node h3 {
  margin: 0 0 5px;
  padding-right: 45px;
  overflow: hidden;
  color: #14243a;
  font: 650 14px/1.2 var(--heading);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.function-node__signature,
.function-node__summary {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  -webkit-box-orient: vertical;
}

.function-node__signature {
  min-height: 30px;
  color: #455b73;
  font: 10px/1.45 var(--mono);
  -webkit-line-clamp: 2;
}

.function-node__summary {
  min-height: 16px;
  margin-top: 6px;
  color: #31516f;
  font-size: 10px;
  line-height: 1.45;
  -webkit-line-clamp: 1;
}

.function-node__meta {
  position: absolute;
  right: 12px;
  bottom: 9px;
  left: 17px;
  justify-content: space-between;
  color: #6c7f96;
  font: 9px/1 var(--mono);
}

.function-node__meta span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.structure-flow-node--documented) .function-node {
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.78), rgba(255, 255, 255, 0)),
    linear-gradient(180deg, #ffffff 0%, #f1f8f5 100%);
}

:deep(.structure-flow-node--builtin_call) .function-node {
  border-color: #ecd68c;
  background: #fff9e6;
}

:deep(.structure-flow-node--builtin_call) .function-node__stripe,
:deep(.structure-flow-node--builtin_call) .function-node__shape {
  border-color: #b88716;
  background: #b88716;
}

:deep(.structure-flow-node--external_member_call) .function-node {
  border-color: #d8dee8;
  background: #fbfcfe;
  opacity: 0.82;
}

:deep(.structure-flow-node--external_member_call) .function-node__stripe,
:deep(.structure-flow-node--external_member_call) .function-node__shape {
  border-color: #74859b;
  background: #74859b;
}

:deep(.structure-flow-node--unresolved) .function-node {
  border-style: dashed;
  color: var(--text-muted);
  background: #f7f8fa;
}

:deep(.structure-flow-node--unresolved) .function-node__stripe,
:deep(.structure-flow-node--unresolved) .function-node__shape {
  border-color: #8993a3;
  background: #8993a3;
}

:deep(.structure-flow-node--lang-python) .function-node {
  --function-lang-color: #3f6fbd;
}

:deep(.structure-flow-node--lang-csharp) .function-node {
  --function-lang-color: #2f8f9d;
}

:deep(.structure-flow-node--lang-rust) .function-node {
  --function-lang-color: #a85f2b;
}

:deep(.structure-flow-node--lang-javascript) .function-node,
:deep(.structure-flow-node--lang-typescript) .function-node {
  --function-lang-color: #b88716;
}

:deep(.structure-flow-node--lang-vue) .function-node {
  --function-lang-color: #278f68;
}

:deep(.structure-flow-node--lang-generic) .function-node {
  --function-lang-color: #7d6fa5;
}

:deep(.vue-flow__edge-text) {
  font: 9px/1 var(--mono);
}

:deep(.structure-flow-edge .vue-flow__edge-path) {
  stroke: #7a8798;
  stroke-width: 1.8;
  filter: drop-shadow(0 1px 1px rgba(31, 47, 70, 0.14));
}

:deep(.structure-flow-edge--static_resolved .vue-flow__edge-path) {
  stroke: #587da8;
}

:deep(.structure-flow-edge--builtin_call .vue-flow__edge-path) {
  stroke: #b88716;
  stroke-dasharray: 4 3;
}

:deep(.structure-flow-edge--external_member_call .vue-flow__edge-path) {
  stroke: #74859b;
  stroke-dasharray: 3 5;
}

:deep(.structure-flow-edge--unresolved .vue-flow__edge-path) {
  stroke: #9aa3ae;
  stroke-dasharray: 6 4;
}

:deep(.structure-flow-edge--mixed .vue-flow__edge-path),
:deep(.structure-flow-edge--multi .vue-flow__edge-path) {
  stroke-width: 2.4;
}

:deep(.structure-flow-edge--active .vue-flow__edge-path) {
  stroke: #315f9c;
  stroke-width: 2.8;
}

.call-edge-label {
  position: absolute;
  display: flex;
  max-width: 420px;
  align-items: center;
  gap: 7px;
  padding: 7px 9px;
  border: 1px solid #b7cbe4;
  border-radius: 7px;
  color: #1d2d44;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 14px 34px rgba(31, 47, 70, 0.18);
  backdrop-filter: blur(10px);
  pointer-events: all;
}

.call-edge-label--compact {
  padding: 4px 7px;
  border-color: #c9d8ea;
  border-radius: 999px;
  color: #315f9c;
  background: rgba(244, 248, 253, 0.96);
  box-shadow: 0 8px 18px rgba(31, 47, 70, 0.12);
  font: 10px/1 var(--mono);
}

.call-edge-label__order {
  flex: 0 0 auto;
  padding: 2px 5px;
  border-radius: 999px;
  color: #315f9c;
  background: #e9f1fb;
  font: 9px/1 var(--mono);
}

.call-edge-label code {
  min-width: 0;
  overflow: hidden;
  font: 10px/1.35 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.call-edge-label :deep(.tok--kw) {
  color: #8d4b9f;
  font-weight: 650;
}

.call-edge-label :deep(.tok--fn) {
  color: #1f6f78;
}

.call-edge-label :deep(.tok--str) {
  color: #9a5b13;
}

.call-edge-label :deep(.tok--num) {
  color: #315f9c;
}

.structure-panel__call-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0 11px 12px;
  list-style: none;
}

.structure-panel__call-list li {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 8px;
  padding: 8px;
  border: 1px solid #dce5ef;
  border-radius: 6px;
  background: #f8fbff;
}

.structure-panel__call-list span {
  color: #315f9c;
  font: 10px/1.4 var(--mono);
}

.structure-panel__call-list code {
  min-width: 0;
  overflow-wrap: anywhere;
  color: #18324f;
  font: 10px/1.4 var(--mono);
}

.structure-panel__call-list small {
  grid-column: 2;
  color: #6c7f96;
  font: 9px/1.2 var(--mono);
}

@media (max-width: 900px) {
  .structure-panel__body {
    grid-template-columns: 1fr;
  }

  .structure-panel__details {
    max-height: 220px;
    border-top: 1px solid var(--border);
    border-left: 0;
  }
}

@media (max-width: 620px) {
  .structure-panel__head,
  .structure-panel__toolbar {
    align-items: stretch;
    flex-direction: column;
    padding: 12px;
  }

  .structure-panel__toolbar input[type="search"] {
    width: 100%;
  }

  .structure-panel__stats {
    margin-left: 0;
    flex-wrap: wrap;
  }
}
</style>
