<script setup>
import { Handle, Position, VueFlow } from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'
import { computed, onMounted, ref, watch } from 'vue'
import FileReference from '../FileReference.vue'
import { languageFromPath } from '../../lib/fileRefs'
import { parseBlock } from '../../lib/markdown'

const props = defineProps({
  workspace: { type: Object, default: null },
})

const loading = ref(false)
const error = ref('')
const records = ref([])
const graph = ref({ nodes: [], edges: [] })
const resolvedFileRefs = ref(new Map())
const selectedId = ref('')
const filters = ref({ scope: 'all', status: 'active', freshness: 'all', query: '' })

const filteredRecords = computed(() => {
  const query = filters.value.query.trim().toLowerCase()
  return records.value.filter(record => {
    if (filters.value.scope !== 'all' && record.scope !== filters.value.scope) return false
    if (filters.value.status === 'active' && record.status === 'reverted') return false
    if (filters.value.status !== 'active' && filters.value.status !== 'all' && record.status !== filters.value.status) return false
    if (filters.value.freshness !== 'all' && record.freshness !== filters.value.freshness) return false
    if (!query) return true
    return [record.statement, record.subject_key, record.kind, record.source].join(' ').toLowerCase().includes(query)
  })
})

const selectedRecord = computed(() => records.value.find(record => record.id === selectedId.value) || filteredRecords.value[0] || null)
const memoryStats = computed(() => {
  const active = records.value.filter(record => record.status !== 'reverted')
  return {
    active: active.length,
    project: active.filter(record => record.scope === 'project').length,
    stale: active.filter(record => record.freshness === 'stale').length,
    conflicts: graph.value.edges.filter(edge => edge.label === 'conflicts').length,
  }
})
const selectedSourceIds = computed(() => {
  const ids = selectedRecord.value?.source_record_ids
  return Array.isArray(ids) ? ids.filter(Boolean) : []
})
const selectedPayloadRows = computed(() => {
  const payload = selectedRecord.value?.payload
  if (!payload || typeof payload !== 'object') return []
  return Object.entries(payload)
    .filter(([, value]) => value !== '' && value != null)
    .slice(0, 8)
    .map(([key, value]) => ({ key, value: typeof value === 'object' ? JSON.stringify(value) : String(value) }))
})
const graphLayout = computed(() => layoutMemoryGraph(graph.value.nodes, graph.value.edges))
const flowNodes = computed(() => graph.value.nodes.map((node, index) => ({
  id: node.id,
  label: node.label,
  type: flowNodeType(node.type),
  position: graphLayout.value.get(node.id) || fallbackNodePosition(index, node.type),
  data: node,
  sourcePosition: sourcePosition(),
  targetPosition: targetPosition(),
  class: ['memory-node', `memory-node--${node.type}`, node.status && `is-${node.status}`, node.freshness && `is-${node.freshness}`].filter(Boolean).join(' '),
  zIndex: node.type === 'memory' ? 5 : 1,
})))
const flowEdges = computed(() => graph.value.edges.map(edge => visualEdge(edge)))

const GRAPH_COLUMNS = Object.freeze({
  subject: 40,
  memory: 360,
  evidence: 720,
})
const GRAPH_ROW_GAP = 34
const GRAPH_GROUP_GAP = 62
const GRAPH_NODE_HEIGHT = Object.freeze({
  subject: 82,
  memory: 136,
  evidence: 102,
})
const HASH_FILE_REF_RE = /(^|[\s([{"'`])#([A-Za-z0-9._~:/\\-]+)/g
const PLAIN_FILE_REF_RE = /(^|[\s([{"'`])((?:[A-Za-z]:)?(?:[\\/]|[A-Za-z0-9_.@~+-]+[\\/])[A-Za-z0-9_.@~+\-\\/ ]+\.[A-Za-z0-9]{1,8}(?::\d+)?)/g

async function loadMemory() {
  if (!props.workspace?.id) return
  loading.value = true
  error.value = ''
  try {
    const [snapshotRes, graphRes] = await Promise.all([
      fetch(`/api/memory/snapshot?workspace_id=${props.workspace.id}`),
      fetch(`/api/memory/graph?workspace_id=${props.workspace.id}`),
    ])
    const snapshot = await snapshotRes.json()
    const graphData = await graphRes.json()
    if (!snapshotRes.ok) throw new Error(snapshot.error || 'Failed to load memory')
    if (!graphRes.ok) throw new Error(graphData.error || 'Failed to load memory graph')
    records.value = snapshot.records || []
    graph.value = graphData || { nodes: [], edges: [] }
    await resolveGraphFileRefs(graph.value.nodes)
  } catch (reason) {
    error.value = reason.message || 'Failed to load memory'
  } finally {
    loading.value = false
  }
}

async function revert(record) {
  if (!record?.id || !props.workspace?.id) return
  await fetch('/api/memory/revert', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace_id: props.workspace.id, id: record.id }),
  })
  await loadMemory()
}

async function accept(record) {
  if (!record?.id || !props.workspace?.id) return
  await fetch('/api/memory/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace_id: props.workspace.id, id: record.id, patch: { status: 'accepted' } }),
  })
  await loadMemory()
}

function layoutMemoryGraph(nodes, edges) {
  const byId = new Map(nodes.map(node => [node.id, node]))
  const subjectIds = nodes.filter(node => node.type === 'subject').map(node => node.id).sort(compareNodeIds)
  const memoryIds = nodes.filter(node => node.type === 'memory').map(node => node.id).sort(compareMemoryIds(byId))
  const evidenceIds = nodes.filter(node => node.type === 'evidence').map(node => node.id).sort(compareNodeIds)
  const memoryBySubject = groupTargets(edges, 'has')
  const evidenceByRecord = groupSources(edges, 'supports')
  const positions = new Map()
  const placed = new Set()
  let y = 38

  for (const subjectId of subjectIds) {
    const memories = (memoryBySubject.get(subjectId) || [])
      .filter(id => byId.get(id)?.type === 'memory')
      .sort(compareMemoryIds(byId))
    if (!memories.length) continue

    const groupTop = y
    let memoryY = groupTop
    for (const memoryId of memories) {
      positions.set(memoryId, { x: GRAPH_COLUMNS.memory, y: memoryY })
      placed.add(memoryId)

      const evidences = (evidenceByRecord.get(memoryId) || [])
        .filter(id => byId.get(id)?.type === 'evidence')
        .sort(compareNodeIds)
      evidences.forEach((evidenceId, evidenceIndex) => {
        positions.set(evidenceId, {
          x: GRAPH_COLUMNS.evidence,
          y: memoryY + evidenceIndex * (GRAPH_NODE_HEIGHT.evidence + 14),
        })
        placed.add(evidenceId)
      })

      const evidenceHeight = evidences.length
        ? evidences.length * GRAPH_NODE_HEIGHT.evidence + (evidences.length - 1) * 14
        : GRAPH_NODE_HEIGHT.memory
      memoryY += Math.max(GRAPH_NODE_HEIGHT.memory, evidenceHeight) + GRAPH_ROW_GAP
    }

    const groupHeight = Math.max(GRAPH_NODE_HEIGHT.subject, memoryY - groupTop - GRAPH_ROW_GAP)
    positions.set(subjectId, {
      x: GRAPH_COLUMNS.subject,
      y: groupTop + Math.max(0, (groupHeight - GRAPH_NODE_HEIGHT.subject) / 2),
    })
    placed.add(subjectId)
    y += groupHeight + GRAPH_GROUP_GAP
  }

  for (const memoryId of memoryIds) {
    if (placed.has(memoryId)) continue
    positions.set(memoryId, { x: GRAPH_COLUMNS.memory, y })
    placed.add(memoryId)
    y += GRAPH_NODE_HEIGHT.memory + GRAPH_ROW_GAP
  }
  for (const evidenceId of evidenceIds) {
    if (placed.has(evidenceId)) continue
    positions.set(evidenceId, { x: GRAPH_COLUMNS.evidence, y })
    placed.add(evidenceId)
    y += GRAPH_NODE_HEIGHT.evidence + GRAPH_ROW_GAP
  }

  return positions
}

function groupTargets(edges, label) {
  const grouped = new Map()
  for (const edge of edges) {
    if (edge.label !== label) continue
    const values = grouped.get(edge.source) || []
    values.push(edge.target)
    grouped.set(edge.source, values)
  }
  return grouped
}

function groupSources(edges, label) {
  const grouped = new Map()
  for (const edge of edges) {
    if (edge.label !== label) continue
    const values = grouped.get(edge.target) || []
    values.push(edge.source)
    grouped.set(edge.target, values)
  }
  return grouped
}

function compareMemoryIds(byId) {
  return (left, right) => {
    const leftRecord = byId.get(left)?.record || {}
    const rightRecord = byId.get(right)?.record || {}
    return [
      leftRecord.scope || '',
      leftRecord.kind || '',
      leftRecord.subject_key || '',
      left,
    ].join('\u0000').localeCompare([
      rightRecord.scope || '',
      rightRecord.kind || '',
      rightRecord.subject_key || '',
      right,
    ].join('\u0000'))
  }
}

function compareNodeIds(left, right) {
  return String(left).localeCompare(String(right))
}

function fallbackNodePosition(index, type) {
  const column = type === 'subject' ? GRAPH_COLUMNS.subject : type === 'evidence' ? GRAPH_COLUMNS.evidence : GRAPH_COLUMNS.memory
  return { x: column, y: 38 + index * 128 }
}

function flowNodeType(type) {
  if (type === 'subject') return 'memorySubject'
  if (type === 'evidence') return 'memoryEvidence'
  return 'memoryRecord'
}

function visualEdge(edge) {
  if (edge.label === 'supports') {
    return {
      id: edge.id,
      source: edge.target,
      target: edge.source,
      label: 'evidence',
      animated: false,
      class: 'memory-edge memory-edge--supports',
    }
  }
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    animated: edge.label === 'conflicts',
    class: ['memory-edge', `memory-edge--${edge.label}`].join(' '),
  }
}

function sourcePosition() {
  return Position.Right
}

function targetPosition() {
  return Position.Left
}

function selectNode(event) {
  const record = event?.node?.data?.record
  if (record?.id) selectedId.value = record.id
  const recordId = event?.node?.data?.evidence?.record_id
  if (recordId) selectedId.value = recordId
}

function markdown(value) {
  return parseBlock(protectCodeIdentifiers(value || ''))
}

function protectCodeIdentifiers(value) {
  return String(value || '').replace(
    /(^|[^A-Za-z0-9_])__([A-Za-z_][A-Za-z0-9_]*)__([^A-Za-z0-9_]|$)/g,
    '$1\\_\\_$2\\_\\_$3',
  )
}

async function resolveGraphFileRefs(nodes) {
  const candidates = collectGraphFileCandidates(nodes)
  if (!candidates.length) {
    resolvedFileRefs.value = new Map()
    return
  }
  const response = await fetch('/api/files/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths: candidates }),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || 'Failed to resolve file references')
  const resolved = new Map()
  for (const [raw, item] of Object.entries(data.files || {})) {
    if (item?.path) resolved.set(normalizeFileRef(raw), item.path)
  }
  resolvedFileRefs.value = resolved
}

function collectGraphFileCandidates(nodes) {
  const candidates = new Set()
  for (const node of nodes || []) {
    for (const value of graphNodeTextFields(node)) {
      for (const part of tokenizePotentialFileRefs(value)) {
        if (part.type === 'candidate') candidates.add(normalizeFileRef(part.path))
      }
    }
  }
  return [...candidates]
}

function graphNodeTextFields(node) {
  return [
    node?.label,
    node?.record?.statement,
    node?.record?.subject_key,
    node?.evidence?.path,
    node?.evidence?.excerpt,
  ].filter(value => typeof value === 'string' && value.trim())
}

function graphTextParts(value) {
  return tokenizePotentialFileRefs(value || '').map(part => {
    if (part.type !== 'candidate') return part
    const path = resolvedFilePath(part.path)
    return path
      ? { type: 'file', path, lang: languageFromPath(path) }
      : { type: 'text', text: part.rawText }
  })
}

function tokenizePotentialFileRefs(text) {
  return tokenizeHashFileRefs(text).flatMap(part => {
    if (part.type === 'candidate') return [part]
    return tokenizePlainFileRefs(part.text)
  })
}

function tokenizeHashFileRefs(text) {
  const value = text || ''
  const parts = []
  let cursor = 0
  let match
  HASH_FILE_REF_RE.lastIndex = 0
  while ((match = HASH_FILE_REF_RE.exec(value)) !== null) {
    const start = match.index + match[1].length
    const rawText = `#${match[2]}`
    const path = normalizeFileRef(match[2])
    if (start > cursor) parts.push({ type: 'text', text: value.slice(cursor, start) })
    parts.push({ type: 'candidate', path, rawText })
    cursor = start + rawText.length
  }
  if (cursor < value.length) parts.push({ type: 'text', text: value.slice(cursor) })
  return parts.length ? parts : [{ type: 'text', text: value }]
}

function tokenizePlainFileRefs(text) {
  const value = text || ''
  const parts = []
  let cursor = 0
  let match
  PLAIN_FILE_REF_RE.lastIndex = 0
  while ((match = PLAIN_FILE_REF_RE.exec(value)) !== null) {
    const start = match.index + match[1].length
    const rawText = match[2]
    const path = normalizeFileRef(match[2])
    if (start > cursor) parts.push({ type: 'text', text: value.slice(cursor, start) })
    parts.push({ type: 'candidate', path, rawText })
    cursor = start + rawText.length
  }
  if (cursor < value.length) parts.push({ type: 'text', text: value.slice(cursor) })
  return parts.length ? parts : [{ type: 'text', text: value }]
}

function normalizeFileRef(path) {
  const cleaned = String(path || '').replace(/[.,;!?]+$/, '')
  const colon = cleaned.lastIndexOf(':')
  if (colon > 1 && /^\d+$/.test(cleaned.slice(colon + 1))) return cleaned.slice(0, colon)
  return cleaned
}

function isFileLike(value) {
  const text = String(value || '')
  return /[\\/]/.test(text) && /\.[A-Za-z0-9]{1,8}(?::\d+)?$/.test(text)
}

function resolvedFilePath(path) {
  return resolvedFileRefs.value.get(normalizeFileRef(path)) || ''
}

function subjectFilePath(data) {
  const kind = data?.kind || data?.record?.subject_kind
  const value = data?.record?.subject_key || data?.label || ''
  if (kind === 'file' || isFileLike(value)) return resolvedFilePath(value)
  return ''
}

function evidenceFilePath(data) {
  const value = data?.evidence?.path || ''
  return isFileLike(value) ? resolvedFilePath(value) : ''
}

function subjectKindLabel(data) {
  return data?.kind || data?.record?.subject_kind || 'subject'
}

function subjectDisplayLabel(data) {
  if (subjectKindLabel(data) === 'project') return 'Project knowledge'
  return data?.label || subjectKindLabel(data)
}

function memorySubjectLabel(data) {
  return data?.record?.subject_key || data?.record?.subject_kind || ''
}

function memorySubjectMetaLabel(data) {
  if (data?.record?.subject_kind === 'project') return 'Workspace-level'
  return memorySubjectLabel(data)
}

function graphRecordTitle(data) {
  if (data?.record?.subject_kind === 'project') return titleCase(`${data?.record?.kind || 'project'} memory`)
  const subject = subjectFilePath(data) || memorySubjectLabel(data)
  if (subject) return basename(subject)
  return data?.record?.kind || data?.kind || 'Memory record'
}

function graphRecordSummary(data) {
  return compactGraphText(data?.record?.statement || data?.label || '')
}

function graphEvidenceSummary(data) {
  return compactGraphText(data?.evidence?.excerpt || data?.label || '')
}

function compactGraphText(value) {
  const text = String(value || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/(^|\s)#{1,6}\s+/g, ' ')
    .replace(/\s+##+\s+[\s\S]*$/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return 'Open the detail panel for the full memory text.'
  return text.length > 132 ? `${text.slice(0, 129).trim()}...` : text
}

function basename(path) {
  const parts = String(path || '').split(/[\\/]/).filter(Boolean)
  return parts.at(-1) || path
}

function titleCase(value) {
  return String(value || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, char => char.toUpperCase())
}

watch(() => props.workspace?.id, loadMemory)
onMounted(loadMemory)
</script>

<template>
  <main class="memory-page">
    <header class="memory-head">
      <div>
        <h1>Memory</h1>
        <p>Review project facts, session references, stale records, and the evidence graph for this workspace.</p>
      </div>
      <button type="button" :disabled="loading" @click="loadMemory">Refresh</button>
    </header>

    <p v-if="error" class="memory-error">{{ error }}</p>

    <section class="memory-stats" aria-label="Memory totals">
      <article>
        <span>Active</span>
        <strong>{{ memoryStats.active }}</strong>
      </article>
      <article>
        <span>Project</span>
        <strong>{{ memoryStats.project }}</strong>
      </article>
      <article :class="{ 'is-warn': memoryStats.stale }">
        <span>Stale</span>
        <strong>{{ memoryStats.stale }}</strong>
      </article>
      <article :class="{ 'is-warn': memoryStats.conflicts }">
        <span>Conflicts</span>
        <strong>{{ memoryStats.conflicts }}</strong>
      </article>
    </section>

    <section class="memory-controls">
      <input v-model="filters.query" type="search" placeholder="Search memory" />
      <select v-model="filters.scope">
        <option value="all">All scopes</option>
        <option value="turn">Turn</option>
        <option value="session">Session</option>
        <option value="project">Project</option>
      </select>
      <select v-model="filters.status">
        <option value="active">Active</option>
        <option value="all">All statuses</option>
        <option value="accepted">Accepted</option>
        <option value="edited">Edited</option>
        <option value="pending">Pending</option>
        <option value="reverted">Reverted</option>
      </select>
      <select v-model="filters.freshness">
        <option value="all">All freshness</option>
        <option value="fresh">Fresh</option>
        <option value="stale">Stale</option>
        <option value="unknown">Unknown</option>
      </select>
    </section>

    <section class="memory-workbench">
      <aside class="memory-list">
        <article
          v-for="record in filteredRecords"
          :key="record.id"
          class="memory-record"
          :class="{ 'is-selected': selectedRecord?.id === record.id, 'is-stale': record.freshness === 'stale', 'is-reverted': record.status === 'reverted' }"
          @click="selectedId = record.id"
        >
          <div>
            <strong>{{ record.kind }}</strong>
            <span>{{ record.scope }}</span>
          </div>
          <div class="memory-md memory-md--compact" v-html="markdown(record.statement)"></div>
          <footer>
            <small>{{ record.subject_kind }} · {{ record.subject_key }}</small>
            <span>{{ record.confidence }} · {{ record.freshness }}</span>
          </footer>
        </article>
        <p v-if="!filteredRecords.length && !loading" class="memory-empty">No memory records match these filters.</p>
      </aside>

      <section class="memory-graph">
        <div class="memory-graph__head">
          <div>
            <strong>Memory graph</strong>
            <span>Subjects flow into records, with evidence attached on the right.</span>
          </div>
          <div class="memory-graph__legend">
            <span><i class="is-subject"></i>Subject</span>
            <span><i class="is-memory"></i>Memory</span>
            <span><i class="is-evidence"></i>Evidence</span>
          </div>
        </div>
        <VueFlow
          :nodes="flowNodes"
          :edges="flowEdges"
          :nodes-draggable="false"
          :pan-on-drag="true"
          :zoom-on-scroll="true"
          :zoom-on-pinch="true"
          fit-view-on-init
          @node-click="selectNode"
        >
          <template #node-memorySubject="{ data, selected: isSelected }">
            <div class="memory-flow-node memory-flow-node--subject" :class="{ 'is-selected': isSelected }">
              <Handle type="source" :position="Position.Right" />
              <span>{{ subjectKindLabel(data) }}</span>
              <div class="memory-flow-md memory-flow-md--subject">
                <FileReference
                  v-if="subjectFilePath(data)"
                  :path="subjectFilePath(data)"
                  :language="languageFromPath(subjectFilePath(data))"
                />
                <strong v-else-if="subjectKindLabel(data) === 'project'" class="memory-flow-subject-title">
                  {{ subjectDisplayLabel(data) }}
                </strong>
                <template v-else>
                  <template v-for="(part, index) in graphTextParts(data.label)" :key="`${data.id}-subject-${index}`">
                    <FileReference v-if="part.type === 'file'" :path="part.path" :language="part.lang" />
                    <div v-else class="memory-flow-markdown" v-html="markdown(part.text)"></div>
                  </template>
                </template>
              </div>
            </div>
          </template>

          <template #node-memoryRecord="{ data, selected: isSelected }">
            <div
              class="memory-flow-node memory-flow-node--record"
              :class="{
                'is-selected': isSelected,
                'is-stale': data.freshness === 'stale',
                'is-reverted': data.status === 'reverted',
              }"
            >
              <Handle type="target" :position="Position.Left" />
              <Handle type="source" :position="Position.Right" />
              <header>
                <span>{{ data.record?.scope || 'memory' }}</span>
                <strong>{{ data.kind || data.record?.kind || 'record' }}</strong>
              </header>
              <h3>{{ graphRecordTitle(data) }}</h3>
              <div class="memory-flow-meta" v-if="memorySubjectLabel(data)">
                <span>{{ data.record?.subject_kind || 'subject' }}</span>
                <FileReference
                  v-if="subjectFilePath(data)"
                  :path="subjectFilePath(data)"
                  :language="languageFromPath(subjectFilePath(data))"
                />
                <code v-else>{{ memorySubjectMetaLabel(data) }}</code>
              </div>
              <p class="memory-flow-summary">{{ graphRecordSummary(data) }}</p>
              <footer>
                <span>{{ data.status || data.record?.status || 'unknown' }}</span>
                <span>{{ data.freshness || data.record?.freshness || 'unknown' }}</span>
              </footer>
            </div>
          </template>

          <template #node-memoryEvidence="{ data, selected: isSelected }">
            <div class="memory-flow-node memory-flow-node--evidence" :class="{ 'is-selected': isSelected }">
              <Handle type="target" :position="Position.Left" />
              <span>{{ data.kind || data.evidence?.kind || 'evidence' }}</span>
              <div class="memory-flow-md">
                <FileReference
                  v-if="evidenceFilePath(data)"
                  :path="evidenceFilePath(data)"
                  :language="languageFromPath(evidenceFilePath(data))"
                />
                <p class="memory-flow-summary memory-flow-summary--evidence">{{ graphEvidenceSummary(data) }}</p>
              </div>
            </div>
          </template>
        </VueFlow>
      </section>

      <aside class="memory-detail">
        <template v-if="selectedRecord">
          <div class="memory-detail__top">
            <span>{{ selectedRecord.scope }} / {{ selectedRecord.kind }}</span>
            <strong>{{ selectedRecord.status }}</strong>
          </div>
          <h2>{{ selectedRecord.subject_key || selectedRecord.subject_kind }}</h2>
          <div class="memory-md memory-md--detail" v-html="markdown(selectedRecord.statement)"></div>
          <dl>
            <div><dt>Confidence</dt><dd>{{ selectedRecord.confidence }}</dd></div>
            <div><dt>Freshness</dt><dd>{{ selectedRecord.freshness }}</dd></div>
            <div><dt>Source</dt><dd>{{ selectedRecord.source || 'unknown' }}</dd></div>
            <div><dt>Session</dt><dd>{{ selectedRecord.session_id || 'project' }}</dd></div>
          </dl>
          <section v-if="selectedSourceIds.length" class="memory-detail__section">
            <h3>Source records</h3>
            <span v-for="id in selectedSourceIds" :key="id" class="memory-chip">{{ id }}</span>
          </section>
          <section v-if="selectedPayloadRows.length" class="memory-detail__section">
            <h3>Payload</h3>
            <div v-for="row in selectedPayloadRows" :key="row.key" class="memory-kv">
              <span>{{ row.key }}</span>
              <code>{{ row.value }}</code>
            </div>
          </section>
          <div class="memory-actions">
            <button v-if="selectedRecord.status !== 'reverted'" type="button" @click="revert(selectedRecord)">Revert</button>
            <button v-else type="button" @click="accept(selectedRecord)">Accept again</button>
          </div>
        </template>
        <p v-else class="memory-empty">Select a record to inspect its source and status.</p>
      </aside>
    </section>
  </main>
</template>

<style scoped>
.memory-page {
  box-sizing: border-box;
  display: grid;
  grid-template-rows: auto auto auto minmax(0, 1fr);
  gap: 14px;
  height: 100%;
  min-height: 0;
  contain: layout paint;
  overflow: hidden;
  padding: 24px 28px 30px;
  color: #0d2d5c;
  background:
    linear-gradient(90deg, rgba(47, 125, 115, .08) 1px, transparent 1px),
    linear-gradient(180deg, rgba(47, 125, 115, .06) 1px, transparent 1px),
    #f5f8fc;
  background-size: 34px 34px;
}
.memory-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  min-height: 0;
}
.memory-head h1 {
  margin: 0;
  font: 800 28px/1.05 var(--sans);
}
.memory-head p {
  max-width: 760px;
  margin: 10px 0 0;
  color: #5d7390;
  font: 13px/1.5 var(--sans);
}
.memory-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.memory-stats article {
  position: relative;
  overflow: hidden;
  min-width: 0;
  padding: 12px 14px;
  border: 1px solid #dbe6f2;
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(255,255,255,.96), rgba(236,250,247,.82));
  box-shadow: 0 10px 28px rgba(28, 54, 84, .06);
}
.memory-stats article::after {
  position: absolute;
  right: 12px;
  bottom: -18px;
  width: 54px;
  height: 54px;
  content: "";
  border: 1px solid rgba(47, 125, 115, .18);
  border-radius: 50%;
}
.memory-stats article.is-warn {
  background: linear-gradient(135deg, #fff, #fff7e6);
}
.memory-stats span {
  display: block;
  color: #667c96;
  font: 700 9px/1 var(--mono);
  text-transform: uppercase;
}
.memory-stats strong {
  display: block;
  margin-top: 8px;
  color: #0d2d5c;
  font: 800 22px/1 var(--mono);
}
.memory-head button,
.memory-actions button {
  height: 32px;
  padding: 0 12px;
  border: 1px solid #cbd9ea;
  border-radius: 8px;
  color: #0d2d5c;
  background: #fff;
  font: 700 11px/1 var(--mono);
}
.memory-controls {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) repeat(3, 150px);
  gap: 10px;
}
.memory-controls input,
.memory-controls select {
  height: 34px;
  min-width: 0;
  border: 1px solid #cbd9ea;
  border-radius: 8px;
  color: #16395f;
  background: #fff;
  font: 11px/1 var(--mono);
}
.memory-controls input {
  padding: 0 12px;
}
.memory-workbench {
  display: grid;
  grid-template-columns: minmax(300px, 390px) minmax(420px, 1fr) minmax(280px, 360px);
  gap: 14px;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
.memory-list,
.memory-graph,
.memory-detail {
  min-width: 0;
  min-height: 0;
  border: 1px solid #dbe6f2;
  border-radius: 10px;
  background: rgba(255, 255, 255, .88);
  box-shadow: 0 14px 40px rgba(28, 54, 84, .08);
}
.memory-list {
  display: grid;
  align-content: start;
  gap: 8px;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 10px;
  overscroll-behavior: contain;
}
.memory-record {
  padding: 10px;
  border: 1px solid #e0e8f3;
  border-radius: 8px;
  cursor: pointer;
  background: #fff;
}
.memory-record:hover,
.memory-record.is-selected {
  border-color: #2f7d73;
  box-shadow: inset 3px 0 0 #2f7d73;
}
.memory-record.is-stale {
  border-color: #d2a344;
}
.memory-record.is-reverted {
  opacity: .55;
}
.memory-record div {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: #6f8198;
  font: 700 9px/1 var(--mono);
  text-transform: uppercase;
}
.memory-record footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
}
.memory-record small,
.memory-record footer span {
  color: #70869f;
  font: 9px/1.2 var(--mono);
}
.memory-graph {
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 0;
}
.memory-graph__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 14px 16px;
  border-bottom: 1px solid #e1ebf5;
  background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(248,252,255,.92));
}
.memory-graph__head strong {
  display: block;
  color: #0d2d5c;
  font: 800 13px/1.2 var(--sans);
}
.memory-graph__head span {
  color: #637a94;
  font: 10px/1.35 var(--mono);
}
.memory-graph__legend {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.memory-graph__legend span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 7px;
  border: 1px solid #dce7f2;
  border-radius: 999px;
  background: #fff;
}
.memory-graph__legend i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.memory-graph__legend .is-subject {
  background: #2f7d73;
}
.memory-graph__legend .is-memory {
  background: #5264d8;
}
.memory-graph__legend .is-evidence {
  background: #c79324;
}
.memory-graph :deep(.vue-flow) {
  width: 100%;
  height: 100%;
  min-height: 0;
  background:
    radial-gradient(circle at 12px 12px, rgba(13, 45, 92, .08) 1px, transparent 1.5px),
    linear-gradient(180deg, #fbfdff, #f6fafc);
  background-size: 24px 24px, auto;
}
.memory-graph :deep(.vue-flow__pane) {
  cursor: grab;
}
.memory-graph :deep(.vue-flow__node.memory-node) {
  border: 0;
  background: transparent;
  box-shadow: none;
}
.memory-graph :deep(.vue-flow__node.selected .memory-flow-node),
.memory-flow-node.is-selected {
  box-shadow:
    0 0 0 2px rgba(82, 100, 216, .22),
    0 18px 42px rgba(28, 54, 84, .16);
}
.memory-graph :deep(.vue-flow__handle) {
  width: 7px;
  height: 7px;
  border: 2px solid #fff;
  background: #8aa0b9;
}
.memory-graph :deep(.vue-flow__edge.memory-edge path) {
  stroke: #9aadc4;
  stroke-width: 1.7;
}
.memory-graph :deep(.vue-flow__edge.memory-edge--supports path) {
  stroke: #c79324;
  stroke-dasharray: 5 5;
}
.memory-graph :deep(.vue-flow__edge.memory-edge--conflicts path) {
  stroke: #c34a3a;
  stroke-width: 2.2;
}
.memory-graph :deep(.vue-flow__edge-text) {
  fill: #5e728a;
  font: 700 9px var(--mono);
}
.memory-flow-node {
  position: relative;
  overflow: hidden;
  width: 100%;
  border: 1px solid #d7e3ef;
  border-radius: 10px;
  color: #183b61;
  background: rgba(255, 255, 255, .96);
  box-shadow: 0 12px 30px rgba(28, 54, 84, .1);
}
.memory-flow-node--subject {
  width: 230px;
  padding: 12px 13px;
  border-color: #99cac2;
  background: linear-gradient(135deg, #f2fbf9, #fff);
}
.memory-flow-node--subject::before,
.memory-flow-node--record::before,
.memory-flow-node--evidence::before {
  position: absolute;
  inset: 0 auto 0 0;
  width: 4px;
  content: "";
}
.memory-flow-node--subject::before {
  background: #2f7d73;
}
.memory-flow-node--record {
  width: 292px;
  padding: 12px 13px 11px;
  border-color: #cfd8fb;
}
.memory-flow-node--record::before {
  background: #5264d8;
}
.memory-flow-node--evidence {
  width: 250px;
  padding: 11px 12px;
  border-color: #ead49b;
  background: linear-gradient(135deg, #fffaf0, #fff);
}
.memory-flow-node--evidence::before {
  background: #c79324;
}
.memory-flow-node.is-stale {
  border-color: #d2a344;
  background: linear-gradient(135deg, #fff8e8, #fff);
}
.memory-flow-node.is-reverted {
  opacity: .5;
}
.memory-flow-node > span,
.memory-flow-node header span,
.memory-flow-node footer span {
  color: #6a7f98;
  font: 800 9px/1 var(--mono);
  text-transform: uppercase;
}
.memory-flow-node header,
.memory-flow-node footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.memory-flow-node header strong {
  min-width: 0;
  color: #203f67;
  font: 800 10px/1.1 var(--mono);
  overflow: hidden;
  text-overflow: ellipsis;
  text-transform: uppercase;
  white-space: nowrap;
}
.memory-flow-node h3 {
  margin: 9px 0 0;
  color: #0d2d5c;
  font: 800 14px/1.18 var(--sans);
  overflow-wrap: anywhere;
}
.memory-flow-node footer {
  margin-top: 9px;
  padding-top: 8px;
  border-top: 1px solid #edf2f8;
}
.memory-flow-meta {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  padding: 6px 7px;
  border: 1px solid #e3ebf5;
  border-radius: 7px;
  background: #f8fbff;
}
.memory-flow-meta span {
  flex: 0 0 auto;
  color: #6a7f98;
  font: 800 8px/1 var(--mono);
  text-transform: uppercase;
}
.memory-flow-meta code {
  min-width: 0;
  overflow: hidden;
  color: #24486a;
  background: transparent;
  font: 10px/1.2 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.memory-flow-subject-title {
  display: block;
  color: #0d4d46;
  font: 800 14px/1.2 var(--sans);
}
.memory-flow-summary {
  display: -webkit-box;
  margin: 9px 0 0;
  overflow: hidden;
  color: #345779;
  font: 12px/1.42 var(--sans);
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}
.memory-flow-summary--evidence {
  color: #5c4a22;
  font-size: 11px;
  -webkit-line-clamp: 2;
}
.memory-flow-md {
  min-width: 0;
  margin-top: 8px;
  color: #294b70;
  font: 12px/1.45 var(--sans);
  overflow-wrap: anywhere;
}
.memory-flow-md :deep(.file-reference),
.memory-flow-meta :deep(.file-reference) {
  max-width: 100%;
  height: 21px;
  border-color: #cdddf0;
  background: #f3f7fc;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.65);
}
.memory-flow-meta :deep(.file-reference) {
  min-width: 0;
  flex: 1 1 auto;
}
.memory-flow-md--subject :deep(.file-reference) {
  width: 100%;
}
.memory-flow-markdown {
  min-width: 0;
}
.memory-flow-md--statement {
  display: -webkit-box;
  max-height: 72px;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}
.memory-flow-md :deep(p) {
  margin: 0 0 6px;
}
.memory-flow-md :deep(p:last-child) {
  margin-bottom: 0;
}
.memory-flow-md :deep(ul),
.memory-flow-md :deep(ol) {
  margin: 5px 0 6px;
  padding-left: 16px;
}
.memory-flow-md :deep(li) {
  margin: 1px 0;
}
.memory-flow-md :deep(strong) {
  color: #0d2d5c;
}
.memory-flow-md :deep(:not(pre) > code) {
  padding: 1px 4px;
  border-radius: 4px;
  color: #0d5f56;
  background: rgba(47, 125, 115, .09);
  font: .88em/1.4 var(--mono);
}
.memory-flow-md :deep(pre) {
  max-height: 74px;
  margin: 6px 0;
  padding: 7px 8px;
  overflow: auto;
  border: 1px solid #dce7f2;
  border-radius: 7px;
  background: #f6f9fc;
  font: 10px/1.45 var(--mono);
}
.memory-detail {
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px;
  overscroll-behavior: contain;
}
.memory-detail__top {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: #657c96;
  font: 700 9px/1 var(--mono);
  text-transform: uppercase;
}
.memory-detail h2 {
  margin: 16px 0 8px;
  color: #0d2d5c;
  font: 800 18px/1.25 var(--sans);
}
.memory-detail dl {
  display: grid;
  gap: 8px;
  margin: 16px 0;
}
.memory-detail dl div {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid #edf2f8;
}
.memory-detail dt {
  color: #70869f;
  font: 700 9px/1.3 var(--mono);
  text-transform: uppercase;
}
.memory-detail dd {
  min-width: 0;
  margin: 0;
  color: #17395f;
  font: 11px/1.35 var(--mono);
  overflow-wrap: anywhere;
}
.memory-detail__section {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin: 16px 0 0;
  padding-top: 12px;
  border-top: 1px solid #edf2f8;
}
.memory-detail__section h3 {
  flex: 0 0 100%;
  margin: 0 0 2px;
  color: #657c96;
  font: 800 9px/1 var(--mono);
  text-transform: uppercase;
}
.memory-chip {
  max-width: 100%;
  padding: 5px 7px;
  border: 1px solid #d8e5f2;
  border-radius: 999px;
  color: #29536f;
  background: #f7fbff;
  font: 9px/1 var(--mono);
  overflow-wrap: anywhere;
}
.memory-kv {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  gap: 8px;
  width: 100%;
  padding: 7px 0;
  border-bottom: 1px solid #edf2f8;
}
.memory-kv span {
  color: #70869f;
  font: 700 9px/1.25 var(--mono);
  text-transform: uppercase;
}
.memory-kv code {
  min-width: 0;
  padding: 0;
  color: #24486a;
  background: transparent;
  font: 10px/1.4 var(--mono);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.memory-actions {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}
.memory-error,
.memory-empty {
  padding: 14px;
  border: 1px dashed #cbd9ea;
  border-radius: 8px;
  color: #71859d;
  background: #fff;
  font: 11px/1.45 var(--mono);
}
:deep(.vue-flow__node.is-reverted) {
  opacity: .45;
}
.memory-md {
  min-width: 0;
  color: #28496d;
  overflow-wrap: anywhere;
}
.memory-md--compact {
  max-height: 130px;
  margin-top: 8px;
  overflow: hidden;
  font: 12px/1.48 var(--sans);
  mask-image: linear-gradient(180deg, #000 76%, transparent);
}
.memory-md--detail {
  max-height: 360px;
  overflow: auto;
  padding: 12px;
  border: 1px solid #e1ebf5;
  border-radius: 8px;
  background: #fbfdff;
  font: 13px/1.6 var(--sans);
}
.memory-md :deep(p) {
  margin: 0 0 8px;
}
.memory-md :deep(p:last-child) {
  margin-bottom: 0;
}
.memory-md :deep(h1),
.memory-md :deep(h2),
.memory-md :deep(h3),
.memory-md :deep(h4),
.memory-md :deep(h5),
.memory-md :deep(h6) {
  margin: 12px 0 6px;
  color: #0d2d5c;
  font-weight: 750;
  line-height: 1.25;
}
.memory-md :deep(h1) { font-size: 17px; }
.memory-md :deep(h2) { font-size: 15px; }
.memory-md :deep(h3),
.memory-md :deep(h4),
.memory-md :deep(h5),
.memory-md :deep(h6) { font-size: 13px; }
.memory-md :deep(strong) {
  color: #0d2d5c;
  font-weight: 750;
}
.memory-md :deep(:not(pre) > code) {
  padding: 1px 5px;
  border-radius: 5px;
  color: #0d5f56;
  background: rgba(47, 125, 115, .09);
  font: .9em/1.5 var(--mono);
}
.memory-md :deep(pre) {
  max-width: 100%;
  margin: 9px 0;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid #d8e5f2;
  border-radius: 8px;
  background: #f5f8fc;
  font: 11px/1.55 var(--mono);
}
.memory-md :deep(ul),
.memory-md :deep(ol) {
  margin: 6px 0 8px;
  padding-left: 19px;
}
.memory-md :deep(li) {
  margin: 2px 0;
}
.memory-md :deep(blockquote) {
  margin: 9px 0;
  padding: 7px 10px;
  border-left: 3px solid #8fc6bd;
  border-radius: 0 7px 7px 0;
  color: #385a76;
  background: #f0faf8;
}
.memory-md :deep(table) {
  width: max-content;
  min-width: 100%;
  margin: 9px 0;
  border-collapse: collapse;
  font-size: 11px;
}
.memory-md :deep(th),
.memory-md :deep(td) {
  padding: 6px 8px;
  border-bottom: 1px solid #dbe6f2;
  text-align: left;
}
.memory-md :deep(th) {
  color: #0d2d5c;
  background: #eef7f5;
}
.memory-md :deep(a) {
  color: #17675e;
  text-decoration: underline;
  text-underline-offset: 2px;
}
@media (max-width: 1180px) {
  .memory-page {
    height: auto;
    max-height: none;
    overflow: auto;
    padding: 24px 18px 52px;
  }
  .memory-workbench {
    grid-template-columns: 1fr;
    height: auto;
    overflow: visible;
  }
  .memory-list,
  .memory-detail {
    max-height: 420px;
  }
  .memory-graph {
    height: 560px;
    min-height: 0;
  }
  .memory-controls {
    grid-template-columns: 1fr 1fr;
  }
  .memory-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
