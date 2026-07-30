<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

const GRAPH_ROW_HEIGHT = 58
const GRAPH_ROW_CENTER = GRAPH_ROW_HEIGHT / 2
const props = defineProps({
  workspaceKey: { type: [String, Number], default: '' },
})

const loading = ref(false)
const actionLoading = ref('')
const actionResult = ref(null)
const commitTitle = ref('')
const commitDescription = ref('')
const error = ref('')
const git = ref(null)
let fetchTimer

const localBranches = computed(() => (git.value?.branches || []).filter(item => !item.remote))
const remoteBranches = computed(() => (git.value?.branches || []).filter(item => item.remote))
const files = computed(() => git.value?.status?.files || [])
const changeSummary = computed(() => {
  const counts = { new: 0, staged: 0, modified: 0, mixed: 0, conflict: 0 }
  for (const file of files.value) counts[fileStatus(file)] += 1
  return Object.entries(counts)
    .map(([key, count]) => ({ key, count, ...statusMeta[key] }))
    .filter(item => item.count)
})
const graphRows = computed(() => buildGraphRows(git.value?.commits || []))
const graphWidth = computed(() => Math.max(96, (graphRows.value.lanes || 1) * 22 + 26))
const graphHeight = computed(() => Math.max(1, graphRows.value.rows.length) * GRAPH_ROW_HEIGHT)
const syncPeak = computed(() => Math.max(1, git.value?.status?.ahead || 0, git.value?.status?.behind || 0))
const hasChanges = computed(() => files.value.length > 0)
const hasStash = computed(() => (git.value?.stashes || []).length > 0)
const canGenerateCommit = computed(() => hasChanges.value || hasStash.value)
const primaryAction = computed(() => {
  if (!git.value?.is_repo) return { action: '', label: 'No repo', icon: '·', disabled: true, tone: 'muted' }
  if (actionLoading.value) return { action: '', label: actionLabel(actionLoading.value), icon: '⌁', disabled: true, tone: 'busy' }
  if (git.value.status.counts.conflicted) return { action: '', label: 'Resolve conflicts', icon: '!', disabled: true, tone: 'danger' }
  if (hasChanges.value) return { action: 'commit', label: 'Commit', icon: '✓', disabled: !commitTitle.value.trim(), tone: 'commit' }
  if (git.value.status.behind) return { action: 'pull', label: `Pull ${git.value.status.behind}`, icon: '↙', disabled: false, tone: 'pull' }
  if (git.value.status.ahead) return { action: 'push', label: `Push ${git.value.status.ahead}`, icon: '↑', disabled: false, tone: 'push' }
  return { action: '', label: 'Up to date', icon: '✓', disabled: true, tone: 'clean' }
})

const statusMeta = {
  new: { label: 'New', icon: '+', color: '#12846f', soft: '#e8f7f2' },
  staged: { label: 'Staged', icon: 'S', color: '#2f6edb', soft: '#e8f0ff' },
  modified: { label: 'Modified', icon: 'M', color: '#c57716', soft: '#fff7e8' },
  mixed: { label: 'Mixed', icon: '±', color: '#8f45d8', soft: '#f0e7fb' },
  conflict: { label: 'Conflict', icon: '!', color: '#c44747', soft: '#fdeaea' },
}

const commitMeta = {
  feat: { label: 'Feature', icon: '✦', color: '#2f6edb', soft: '#e8f0ff' },
  fix: { label: 'Fix', icon: '◆', color: '#c44747', soft: '#fdeaea' },
  refactor: { label: 'Refactor', icon: '◇', color: '#8f45d8', soft: '#f0e7fb' },
  chore: { label: 'Chore', icon: '•', color: '#536675', soft: '#e9eef2' },
  docs: { label: 'Docs', icon: '¶', color: '#12846f', soft: '#e8f7f2' },
  test: { label: 'Test', icon: '✓', color: '#0f766e', soft: '#e3f5f0' },
  perf: { label: 'Perf', icon: '↟', color: '#c57716', soft: '#fff7e8' },
  build: { label: 'Build', icon: '▣', color: '#6f5bd8', soft: '#efecff' },
  ci: { label: 'CI', icon: '⌁', color: '#0d7e9a', soft: '#e6f6fa' },
  style: { label: 'Style', icon: '◐', color: '#cf4d78', soft: '#fae6ee' },
  revert: { label: 'Revert', icon: '↺', color: '#9a5c20', soft: '#f9eddc' },
  commit: { label: 'Commit', icon: '●', color: '#536675', soft: '#e9eef2' },
}

async function loadGit(silent = false) {
  if (!silent) loading.value = true
  error.value = ''
  try {
    const response = await fetch('/api/git/status')
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data.error || `Git request failed (${response.status})`)
    git.value = data
  } catch (err) {
    if (!silent) error.value = err.message || 'Git request failed'
  } finally {
    if (!silent) loading.value = false
  }
}

async function runGitAction(action, options = {}) {
  if (!action) return
  actionLoading.value = action
  if (!options.silent) actionResult.value = null
  error.value = ''
  try {
    const response = await fetch('/api/git/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action,
        title: commitTitle.value,
        description: commitDescription.value,
        paths: options.paths || [],
      }),
    })
    const data = await response.json().catch(() => ({}))
    if (!options.silent) actionResult.value = data
    if (data.snapshot) git.value = data.snapshot
    if (!response.ok || !data.ok) throw new Error(data.error || `Git ${action} failed`)
    if (action === 'generate_commit') {
      commitTitle.value = data.title || ''
      commitDescription.value = data.description || ''
    } else if (action === 'commit') {
      commitTitle.value = ''
      commitDescription.value = ''
    }
  } catch (err) {
    if (!options.silent) error.value = err.message || `Git ${action} failed`
  } finally {
    actionLoading.value = ''
  }
}

function discardFile(file) {
  if (!window.confirm(`Discard changes in ${file.path}?`)) return
  runGitAction('discard', { paths: [file.path] })
}

function toggleStage(file) {
  runGitAction(file.staged ? 'unstage' : 'stage', { paths: [file.path] })
}

function buildGraphRows(commits) {
  const rowByHash = new Map(commits.map((commit, index) => [commit.hash, index]))
  const active = []
  const rows = []
  for (const commit of commits) {
    let lane = active.indexOf(commit.hash)
    if (lane < 0) {
      lane = active.findIndex(item => !item)
      if (lane < 0) lane = active.length
    }
    const visibleParents = commit.parents.filter(parent => rowByHash.has(parent))
    active[lane] = visibleParents[0] || null
    for (const parent of visibleParents.slice(1)) {
      if (!active.includes(parent)) active.push(parent)
    }
    const kind = commitKind(commit.subject)
    const refInfo = commitRefInfo(commit)
    rows.push({ ...commit, lane, kind, refInfo })
  }
  const laneByHash = new Map(rows.map(row => [row.hash, row.lane]))
  const edges = []
  rows.forEach((row, index) => {
    for (const parent of row.parents) {
      const parentIndex = rowByHash.get(parent)
      if (parentIndex === undefined) continue
      edges.push({
        id: `${row.hash}:${parent}`,
        x1: xFor(row.lane),
        y1: yFor(index),
        x2: xFor(laneByHash.get(parent) ?? row.lane),
        y2: yFor(parentIndex),
        lane: row.lane,
        kind: row.kind,
        refClass: row.refInfo.className,
      })
    }
  })
  return { rows, edges, lanes: Math.max(1, ...rows.map(row => row.lane + 1)) }
}

function xFor(lane) {
  return 14 + lane * 22
}

function yFor(index) {
  return GRAPH_ROW_CENTER + index * GRAPH_ROW_HEIGHT
}

function fileStatus(file) {
  if (file.conflicted) return 'conflict'
  if (file.untracked) return 'new'
  if (file.staged && file.worktree.trim()) return 'mixed'
  if (file.staged) return 'staged'
  return 'modified'
}

function fileStatusInfo(file) {
  return statusMeta[fileStatus(file)] || statusMeta.modified
}

function commitKind(subject) {
  const match = String(subject || '').match(/^([a-z]+)(?:\([^)]+\))?!?:/i)
  const kind = match?.[1]?.toLowerCase() || 'commit'
  return commitMeta[kind] ? kind : 'commit'
}

function commitRefInfo(commit) {
  const refs = commit.refs || []
  if (refs.some(ref => ref === git.value?.head?.branch)) return { className: 'ref-head', color: '#12846f' }
  if (refs.some(ref => ref.startsWith('origin/') || ref.startsWith('remotes/'))) return { className: 'ref-remote', color: '#8f45d8' }
  if (refs.some(ref => ref.startsWith('tag:') || ref.includes('/tags/'))) return { className: 'ref-tag', color: '#c57716' }
  if (refs.length) return { className: 'ref-local', color: '#2f6edb' }
  return { className: '', color: '' }
}

function commitStyle(commit) {
  const meta = commitMeta[commit.kind] || commitMeta.commit
  return {
    '--commit-color': commit.refInfo?.color || meta.color,
    '--commit-soft': meta.soft,
  }
}

function refClass(refName) {
  if (refName === git.value?.head?.branch) return 'is-head'
  if (refName.startsWith('origin/') || refName.startsWith('remotes/')) return 'is-remote'
  if (refName.startsWith('tag:') || refName.includes('/tags/')) return 'is-tag'
  return 'is-local'
}

function actionLabel(action) {
  return {
    fetch: 'Fetching',
    pull: 'Pulling',
    push: 'Pushing',
    commit: 'Committing',
    stage: 'Staging',
    unstage: 'Unstaging',
    stash: 'Stashing',
    unstash: 'Applying stash',
    discard: 'Discarding',
    generate_commit: 'Generating',
  }[action] || 'Working'
}

function branchTone(branch) {
  if (branch.current) return '#12846f'
  return branch.remote ? '#8f45d8' : '#2f6edb'
}

function branchTrack(branch) {
  const value = String(branch.track || '').trim()
  if (!value) return ''
  return value.replace('>', '↑').replace('<', '↓')
}

function branchName(name) {
  return String(name || '').replace(/^remotes\//, '')
}

onMounted(() => {
  loadGit()
  fetchTimer = window.setInterval(() => {
    if (!actionLoading.value) runGitAction('fetch', { silent: true })
  }, 10 * 60 * 1000)
})

onUnmounted(() => {
  window.clearInterval(fetchTimer)
})

watch(() => props.workspaceKey, () => {
  loadGit()
})
</script>

<template>
  <section class="git-panel">
    <div class="git-panel__toolbar">
      <div>
        <strong>{{ git?.head?.branch || 'Git' }}</strong>
        <small v-if="git?.root">{{ git.root }}</small>
      </div>
      <button type="button" :disabled="loading" @click="loadGit">{{ loading ? 'Refreshing' : 'Refresh' }}</button>
    </div>

    <p v-if="error" class="git-panel__error">{{ error }}</p>
    <p v-else-if="git && !git.is_repo" class="git-panel__empty">{{ git.error }}</p>

    <template v-else-if="git?.is_repo">
      <div class="git-sync">
        <div class="git-sync__node is-local">
          <span>Local</span>
          <strong>{{ git.head.short || 'unknown' }}</strong>
        </div>
        <div class="git-sync__flow" :class="{ 'is-moving': git.status.ahead || git.status.behind }">
          <i></i>
          <b :style="{ width: `${Math.max(8, git.status.ahead / syncPeak * 100)}%` }"></b>
          <b :style="{ width: `${Math.max(8, git.status.behind / syncPeak * 100)}%` }"></b>
        </div>
        <div class="git-sync__node is-remote">
          <span>Remote</span>
          <strong>{{ git.status.upstream || 'not tracked' }}</strong>
        </div>
        <div class="git-sync__meter">
          <span>Ahead</span>
          <strong>{{ git.status.ahead }}</strong>
        </div>
        <div class="git-sync__meter">
          <span>Behind</span>
          <strong>{{ git.status.behind }}</strong>
        </div>
      </div>

      <div class="git-message-box">
        <input v-model="commitTitle" type="text" placeholder="Commit title" :disabled="!!actionLoading">
        <textarea v-model="commitDescription" rows="3" placeholder="Description" :disabled="!!actionLoading"></textarea>
        <button type="button" :disabled="!!actionLoading || !canGenerateCommit" @click="runGitAction('generate_commit')">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 3l1.5 5.5L18 9l-4.5 2.5L12 17l-1.5-5.5L6 9l4.5-1.5z"/>
          </svg>
          <span>{{ actionLoading === 'generate_commit' ? 'Generating' : 'Generate' }}</span>
        </button>
      </div>

      <div class="git-actions" :class="{ 'is-running': actionLoading }">
        <button type="button" class="git-actions__fetch" :disabled="!!actionLoading" @click="runGitAction('fetch')">
          <i>↓</i><span>{{ actionLoading === 'fetch' ? 'Fetching' : 'Fetch' }}</span>
        </button>
        <button
          type="button"
          class="git-actions__primary"
          :class="`is-${primaryAction.tone}`"
          :disabled="primaryAction.disabled"
          @click="runGitAction(primaryAction.action)"
        >
          <i>{{ primaryAction.icon }}</i><span>{{ primaryAction.label }}</span>
        </button>
        <button type="button" class="git-actions__stash" :disabled="!!actionLoading || !hasStash" @click="runGitAction('unstash')">
          <i>◰</i><span>Apply</span>
        </button>
      </div>
      <p v-if="actionResult" class="git-action-result" :class="{ 'is-ok': actionResult.ok }">
        <b>{{ actionResult.command }}</b>
        <span>{{ actionResult.stderr || actionResult.stdout || (actionResult.ok ? 'Done.' : actionResult.error) }}</span>
      </p>

      <div v-if="hasStash" class="git-stashes">
        <article v-for="stash in git.stashes.slice(0, 3)" :key="stash.ref">
          <b>{{ stash.ref }}</b>
          <span>{{ stash.subject }}</span>
          <small>{{ stash.relative_date }}</small>
        </article>
      </div>

      <div class="git-change-strip" :class="{ 'is-clean': !git.status.dirty }">
        <span>{{ git.status.dirty ? 'Working tree changed' : 'Working tree clean' }}</span>
        <div v-if="changeSummary.length" class="git-change-strip__bars">
          <i
            v-for="item in changeSummary"
            :key="item.key"
            :title="`${item.label}: ${item.count}`"
            :style="{ '--status-color': item.color, flexGrow: item.count }"
          ></i>
        </div>
        <b>{{ files.length }}</b>
      </div>

      <section class="git-section">
        <div class="git-section__head">
          <strong>Changes</strong>
          <span>{{ files.length }} files</span>
        </div>
        <div v-if="changeSummary.length" class="git-change-summary">
          <span
            v-for="item in changeSummary"
            :key="item.key"
            :style="{ '--status-color': item.color, '--status-soft': item.soft }"
          >
            <i>{{ item.icon }}</i>{{ item.count }}
          </span>
        </div>
        <article v-for="file in files.slice(0, 12)" :key="`${file.index}:${file.worktree}:${file.path}`" class="git-file" :class="`is-${fileStatus(file)}`">
          <b :style="{ '--status-color': fileStatusInfo(file).color }">{{ fileStatusInfo(file).icon }}</b>
          <span>{{ file.path }}</span>
          <div class="git-file__actions">
            <button type="button" :disabled="!!actionLoading || file.conflicted" :title="file.staged ? 'Unstage this change' : 'Stage this change'" @click="toggleStage(file)">
              {{ file.staged ? '−' : '+' }}
            </button>
            <button type="button" :disabled="!!actionLoading" title="Discard this change" @click="discardFile(file)">×</button>
          </div>
        </article>
        <p v-if="!files.length" class="git-panel__empty">No local changes.</p>
      </section>

      <section class="git-section">
        <div class="git-section__head">
          <strong>Branches</strong>
          <span>{{ localBranches.length }} local / {{ remoteBranches.length }} remote</span>
        </div>
        <div class="git-branches">
          <article v-for="branch in localBranches.slice(0, 8)" :key="branch.name" class="git-branch" :class="{ 'is-current': branch.current }" :style="{ '--branch-color': branchTone(branch) }">
            <i class="git-branch__node"></i>
            <span class="git-branch__name">{{ branchName(branch.name) }}</span>
            <small class="git-branch__meta">{{ branch.upstream || 'local only' }}</small>
            <b class="git-branch__hash">{{ branch.hash }}</b>
            <em v-if="branchTrack(branch)" class="git-branch__track">{{ branchTrack(branch) }}</em>
            <button
              v-if="!branch.current"
              class="git-branch__switch"
              type="button"
              :disabled="!!actionLoading"
              title="Switch to this branch"
              @click="runGitAction('checkout', { branch: branch.name })"
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>
              </svg>
            </button>
          </article>
          <article v-for="branch in remoteBranches.slice(0, 8)" :key="branch.name" class="git-branch is-remote" :style="{ '--branch-color': branchTone(branch) }">
            <i class="git-branch__node"></i>
            <span class="git-branch__name">{{ branchName(branch.name) }}</span>
            <small class="git-branch__meta">remote ref</small>
            <b class="git-branch__hash">{{ branch.hash }}</b>
            <button
              class="git-branch__switch"
              type="button"
              :disabled="!!actionLoading"
              title="Checkout remote branch"
              @click="runGitAction('checkout', { branch: branch.name })"
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>
              </svg>
            </button>
          </article>
        </div>
      </section>

      <section class="git-section">
        <div class="git-section__head">
          <strong>Remotes</strong>
          <span>{{ git.remotes.length }}</span>
        </div>
        <article v-for="remote in git.remotes" :key="remote.name" class="git-remote">
          <b>{{ remote.name }}</b>
          <span>{{ remote.fetch || remote.push }}</span>
        </article>
        <p v-if="!git.remotes.length" class="git-panel__empty">No remotes configured.</p>
      </section>

      <section class="git-section git-section--graph">
        <div class="git-section__head">
          <strong>Graph</strong>
          <span class="git-graph__legend">
            <i class="is-head"></i>HEAD
            <i class="is-local"></i>local
            <i class="is-remote"></i>remote
            <i class="is-tag"></i>tag
          </span>
        </div>
        <div class="git-graph" :style="{ '--graph-width': `${graphWidth}px` }">
          <svg class="git-graph__canvas" :width="graphWidth" :height="graphHeight" aria-hidden="true">
            <path
              v-for="edge in graphRows.edges"
              :key="edge.id"
              :d="`M ${edge.x1} ${edge.y1} C ${edge.x1} ${(edge.y1 + edge.y2) / 2}, ${edge.x2} ${(edge.y1 + edge.y2) / 2}, ${edge.x2} ${edge.y2}`"
              :class="[`lane-${edge.lane % 6}`, `is-${edge.kind}`, edge.refClass]"
            />
            <circle
              v-for="(row, index) in graphRows.rows"
              :key="row.hash"
              :cx="xFor(row.lane)"
              :cy="yFor(index)"
              r="5.5"
              :class="[`lane-${row.lane % 6}`, `is-${row.kind}`, row.refInfo.className]"
            />
          </svg>
          <div class="git-graph__rows">
            <article v-for="commit in graphRows.rows" :key="commit.hash" class="git-commit" :class="`is-${commit.kind}`" :style="commitStyle(commit)">
              <span class="git-commit__type">{{ commitMeta[commit.kind].icon }}</span>
              <div class="git-commit__main">
                <strong>{{ commit.subject }}</strong>
                <small>{{ commitMeta[commit.kind].label }} · {{ commit.author }} · {{ commit.relative_date }} · {{ commit.short }}</small>
              </div>
              <div v-if="commit.refs.length" class="git-commit__refs">
                <span v-for="refName in commit.refs.slice(0, 3)" :key="refName" :class="refClass(refName)">{{ branchName(refName) }}</span>
              </div>
            </article>
          </div>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.git-panel {
  display: grid;
  gap: 12px;
}

.git-panel__toolbar,
.git-section,
.git-sync,
.git-change-strip {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: #ffffff;
  box-shadow: var(--shadow-sm);
}

.git-panel__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 11px;
}

.git-panel__toolbar div {
  display: grid;
  min-width: 0;
  gap: 4px;
}

.git-panel__toolbar strong {
  color: var(--text-h);
  font: 700 12px/1 var(--mono);
}

.git-panel__toolbar small {
  overflow: hidden;
  color: var(--text-muted);
  font: 9px/1.2 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-panel__toolbar button {
  height: 28px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  color: #ffffff;
  background: #2f6edb;
  font-size: 10px;
  font-weight: 700;
  cursor: pointer;
}

.git-panel__toolbar button:disabled {
  opacity: .62;
  cursor: default;
}

.git-sync {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 54px minmax(0, 1fr) 48px 48px;
  overflow: hidden;
}

.git-sync div {
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 10px 8px;
  border-right: 1px solid var(--border);
}

.git-sync__node {
  position: relative;
}

.git-sync__node::before {
  content: "";
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: currentColor;
  box-shadow: 0 0 0 4px color-mix(in srgb, currentColor 14%, transparent);
}

.git-sync__node.is-local {
  color: #2f6edb;
}

.git-sync__node.is-remote {
  color: #8f45d8;
}

.git-sync__flow {
  position: relative;
  align-content: center;
  gap: 4px;
  background: linear-gradient(90deg, #edf3ff, #f5ecff);
}

.git-sync__flow i {
  position: absolute;
  inset: 50% 8px auto;
  height: 1px;
  background: linear-gradient(90deg, #2f6edb, #8f45d8);
}

.git-sync__flow b {
  position: relative;
  display: block;
  height: 4px;
  min-width: 8px;
  border-radius: 999px;
  background: #2f6edb;
}

.git-sync__flow b:last-child {
  justify-self: end;
  background: #8f45d8;
}

.git-sync__flow.is-moving::after {
  content: "";
  position: absolute;
  top: 50%;
  left: 8px;
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: #ffffff;
  box-shadow: 0 0 0 2px #2f6edb, 0 0 12px rgba(47, 110, 219, .45);
  transform: translateY(-50%);
  animation: sync-dot 1.65s ease-in-out infinite;
}

.git-sync div:last-child {
  border-right: 0;
}

.git-sync span,
.git-section__head span {
  color: var(--text-muted);
  font: 8.5px/1 var(--mono);
  text-transform: uppercase;
}

.git-sync strong {
  overflow: hidden;
  color: var(--text-h);
  font: 800 11px/1.1 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-message-box {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: #ffffff;
  box-shadow: var(--shadow-sm);
}

.git-message-box input,
.git-message-box textarea {
  width: 100%;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--text-h);
  background: #fbfdff;
  font: 10px/1.35 var(--mono);
  outline: none;
}

.git-message-box input {
  height: 30px;
  padding: 0 8px;
}

.git-message-box textarea {
  grid-column: 1;
  resize: vertical;
  min-height: 52px;
  padding: 7px 8px;
}

.git-message-box input:focus,
.git-message-box textarea:focus {
  border-color: #2f6edb;
  box-shadow: 0 0 0 3px rgba(47, 110, 219, .12);
}

.git-message-box button {
  grid-row: 1 / span 2;
  grid-column: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 0 10px;
  border: 1px solid rgba(47, 110, 219, 0.28);
  border-radius: 7px;
  color: #ffffff;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  font: 600 10.5px/1 var(--mono);
  letter-spacing: 0.01em;
  cursor: pointer;
  transition: box-shadow 150ms, filter 150ms;
}

.git-message-box button:not(:disabled):hover {
  filter: brightness(1.1);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25);
}

.git-message-box button svg {
  flex-shrink: 0;
}

.git-message-box button:disabled {
  cursor: default;
  opacity: .48;
}

.git-actions {
  display: grid;
  grid-template-columns: .72fr 1.28fr .72fr;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background:
    linear-gradient(90deg, #e8f0ff, #e8f7f2 50%, #fff7e8);
  box-shadow: var(--shadow-sm);
}

.git-actions button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 34px;
  border: 0;
  border-right: 1px solid rgba(15, 23, 42, .09);
  color: var(--text-h);
  background: rgba(255, 255, 255, .76);
  font-size: 10px;
  font-weight: 800;
  cursor: pointer;
  transition: background .18s ease, transform .18s ease, opacity .18s ease;
}

.git-actions button:last-child {
  border-right: 0;
}

.git-actions button:hover:not(:disabled) {
  background: rgba(255, 255, 255, .94);
  transform: translateY(-1px);
}

.git-actions button:disabled {
  cursor: default;
  opacity: .58;
}

.git-actions i {
  display: grid;
  width: 18px;
  height: 18px;
  place-items: center;
  border-radius: 999px;
  color: #ffffff;
  background: #2f6edb;
  font-style: normal;
  line-height: 1;
}

.git-actions button.git-actions__primary {
  position: relative;
  isolation: isolate;
  min-height: 38px;
  color: #ffffff;
  background:
    radial-gradient(circle at 18% 20%, rgba(255, 255, 255, .28), transparent 28%),
    linear-gradient(135deg, var(--primary-a, #2f6edb), var(--primary-b, #174ea6));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .28);
  transform: none;
}

.git-actions button.git-actions__primary::before {
  content: "";
  position: absolute;
  inset: 1px;
  z-index: 0;
  border-radius: 0;
  background:
    linear-gradient(90deg, transparent, rgba(255, 255, 255, .2), transparent);
  opacity: 0;
  transform: translateX(-45%);
  transition: opacity .18s ease, transform .34s ease;
  pointer-events: none;
}

.git-actions button.git-actions__primary i,
.git-actions button.git-actions__primary span {
  position: relative;
  z-index: 1;
}

.git-actions button.git-actions__primary:hover:not(:disabled) {
  color: #ffffff;
  background:
    radial-gradient(circle at 18% 20%, rgba(255, 255, 255, .34), transparent 30%),
    linear-gradient(135deg, var(--primary-hover-a, var(--primary-a, #2f6edb)), var(--primary-hover-b, var(--primary-b, #174ea6)));
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, .36),
    0 6px 16px color-mix(in srgb, var(--primary-a, #2f6edb) 28%, transparent);
  transform: translateY(-1px);
}

.git-actions button.git-actions__primary:hover:not(:disabled)::before {
  opacity: 1;
  transform: translateX(45%);
}

.git-actions button.git-actions__primary i {
  color: var(--primary-a, #2f6edb);
  background: rgba(255, 255, 255, .9);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, .32);
}

.git-actions button.git-actions__primary.is-commit {
  --primary-a: #12846f;
  --primary-b: #0d6354;
  --primary-hover-a: #18a386;
  --primary-hover-b: #0f715f;
}

.git-actions button.git-actions__primary.is-pull {
  --primary-a: #8f45d8;
  --primary-b: #6534a3;
  --primary-hover-a: #a45cf0;
  --primary-hover-b: #7441ba;
}

.git-actions button.git-actions__primary.is-push {
  --primary-a: #c57716;
  --primary-b: #91530e;
  --primary-hover-a: #dc8a22;
  --primary-hover-b: #a86414;
}

.git-actions button.git-actions__primary.is-clean {
  --primary-a: #12846f;
  --primary-b: #e8f7f2;
  color: #126b5d;
  background: #e8f7f2;
  box-shadow: none;
}

.git-actions button.git-actions__primary.is-clean i {
  color: #ffffff;
  background: #12846f;
}

.git-actions button.git-actions__primary.is-clean:hover:not(:disabled) {
  color: #126b5d;
  background: #e8f7f2;
  box-shadow: none;
  transform: none;
}

.git-actions__stash i {
  background: #12846f;
}

.git-actions__danger {
  color: #9b2f2f;
}

.git-actions__danger i {
  background: #c44747;
}

.git-actions__fetch i {
  background: #c57716;
}

.git-actions.is-running i {
  animation: sync-spin .9s ease-in-out infinite;
}

.git-action-result {
  display: grid;
  gap: 5px;
  margin: 0;
  padding: 9px 11px;
  border: 1px solid var(--err-border);
  border-radius: var(--radius);
  color: var(--err);
  background: var(--err-bg);
  font-size: 10px;
  line-height: 1.35;
}

.git-action-result.is-ok {
  border-color: #b8e2d7;
  color: #126b5d;
  background: #e8f7f2;
}

.git-action-result b,
.git-action-result span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-action-result b {
  font: 800 9px/1 var(--mono);
}

.git-stashes {
  display: grid;
  overflow: hidden;
  border: 1px solid #d9c8ee;
  border-radius: var(--radius);
  background: #fbf7ff;
  box-shadow: var(--shadow-sm);
}

.git-stashes article {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr) auto;
  gap: 7px;
  align-items: center;
  padding: 8px 10px;
  border-bottom: 1px solid rgba(143, 69, 216, .12);
}

.git-stashes article:last-child {
  border-bottom: 0;
}

.git-stashes b,
.git-stashes small {
  color: #6534a3;
  font: 800 9px/1 var(--mono);
}

.git-stashes span {
  overflow: hidden;
  color: var(--text-h);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-change-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 11px;
  border-left: 3px solid #c57716;
  color: #7a4c0a;
  background: #fff7e8;
}

.git-change-strip.is-clean {
  border-left-color: #12846f;
  color: #126b5d;
  background: #e8f7f2;
}

.git-change-strip span {
  font-size: 10.5px;
  font-weight: 700;
}

.git-change-strip__bars {
  display: flex;
  flex: 1;
  gap: 3px;
  max-width: 96px;
  height: 8px;
  margin: 0 9px;
}

.git-change-strip__bars i {
  min-width: 8px;
  border-radius: 999px;
  background: var(--status-color);
}

.git-change-strip b {
  font: 800 12px/1 var(--mono);
}

.git-section {
  overflow: hidden;
}

.git-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 11px;
  border-bottom: 1px solid var(--border);
}

.git-section__head strong {
  color: var(--text-h);
  font-size: 11px;
}

.git-file,
.git-branch,
.git-remote,
.git-commit {
  display: grid;
  min-width: 0;
  border-bottom: 1px solid rgba(15, 23, 42, .055);
}

.git-file {
  grid-template-columns: 22px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 8px 11px;
}

.git-file b {
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  border-radius: 4px;
  color: #ffffff;
  background: var(--status-color, #7c8da3);
  font: 800 9px/1 var(--mono);
}

.git-change-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 9px 11px;
  border-bottom: 1px solid rgba(15, 23, 42, .055);
}

.git-change-summary span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 38px;
  padding: 4px 7px 4px 5px;
  border: 1px solid color-mix(in srgb, var(--status-color) 24%, transparent);
  border-radius: 999px;
  color: var(--status-color);
  background: var(--status-soft);
  font: 800 9px/1 var(--mono);
}

.git-change-summary i {
  display: grid;
  width: 15px;
  height: 15px;
  place-items: center;
  border-radius: 999px;
  color: #ffffff;
  background: var(--status-color);
  font-style: normal;
}

.git-file span,
.git-remote span {
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  font: 10px/1.25 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-file__actions {
  display: flex;
  gap: 4px;
  opacity: 0;
  transform: translateX(4px);
  transition: opacity .14s ease, transform .14s ease;
}

.git-file:hover .git-file__actions,
.git-file:focus-within .git-file__actions {
  opacity: 1;
  transform: translateX(0);
}

.git-file__actions button {
  display: grid;
  width: 21px;
  height: 21px;
  place-items: center;
  border: 1px solid rgba(15, 23, 42, .08);
  border-radius: 999px;
  color: #536675;
  background: #ffffff;
  font: 800 10px/1 var(--mono);
  cursor: pointer;
}

.git-file__actions button:last-child {
  color: #9b2f2f;
}

.git-file__actions button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 3px 8px rgba(15, 23, 42, .12);
}

.git-file__actions button:disabled {
  cursor: default;
  opacity: .45;
}

.git-branches {
  display: grid;
  background:
    linear-gradient(90deg, rgba(47, 110, 219, .05), transparent 42%),
    linear-gradient(180deg, #ffffff, #fbfdff);
}

.git-branch {
  display: grid;
  position: relative;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  gap: 3px 8px;
  align-items: center;
  min-height: 42px;
  padding: 8px 34px 8px 11px;
  border-left: 3px solid transparent;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--branch-color) 8%, transparent), transparent 58%);
  transition: background .16s ease, border-color .16s ease;
}

.git-branch.is-current {
  padding-right: 11px;
}

.git-branch:hover,
.git-branch:focus-within {
  border-left-color: var(--branch-color);
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--branch-color) 15%, transparent), #ffffff 68%);
}

.git-branch__node {
  position: relative;
  grid-row: 1 / span 2;
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: var(--branch-color);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--branch-color) 14%, transparent);
}

.git-branch__node::after {
  content: "";
  position: absolute;
  left: 4px;
  top: 11px;
  width: 2px;
  height: 20px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--branch-color) 22%, transparent);
}

.git-branch:last-child .git-branch__node::after {
  display: none;
}

.git-branch__name {
  min-width: 0;
  overflow: hidden;
  color: var(--text-h);
  font-size: 10.5px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-branch__meta {
  grid-column: 2;
  overflow: hidden;
  color: var(--text-muted);
  font: 9px/1.2 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-branch__hash,
.git-branch__track {
  justify-self: end;
  padding: 3px 5px;
  border-radius: 5px;
  color: var(--branch-color);
  background: color-mix(in srgb, var(--branch-color) 10%, #ffffff);
  font: 800 9px/1 var(--mono);
}

.git-branch__track {
  grid-column: 3;
  grid-row: 2;
  min-width: 24px;
  text-align: center;
  font-style: normal;
}

.git-branch.is-current {
  border-left-color: #12846f;
  background: linear-gradient(90deg, #e8f7f2, #ffffff 70%);
}

.git-branch.is-current .git-branch__name::after {
  content: " current";
  margin-left: 6px;
  padding: 2px 5px;
  border-radius: 999px;
  color: #126b5d;
  background: #d8f0e9;
  font: 800 8px/1 var(--mono);
  text-transform: uppercase;
}

.git-branch.is-remote .git-branch__name {
  color: #8f45d8;
}

.git-branch__switch {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  display: grid;
  width: 22px;
  height: 22px;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  background: var(--bg-raised);
  cursor: pointer;
  opacity: 0;
  transition: opacity 120ms, color 120ms, border-color 120ms;
}

.git-branch:hover .git-branch__switch,
.git-branch:focus-within .git-branch__switch {
  opacity: 1;
}

.git-branch__switch:hover:not(:disabled) {
  color: var(--accent);
  border-color: var(--accent-border);
}

.git-branch__switch:disabled {
  cursor: default;
  opacity: 0;
}

.git-remote {
  gap: 4px;
  padding: 9px 11px;
}

.git-remote b {
  color: var(--text-h);
  font-size: 10.5px;
}

.git-section--graph {
  background:
    linear-gradient(180deg, rgba(248, 251, 255, .96), #ffffff);
}

.git-graph {
  --graph-row-height: 58px;
  position: relative;
  display: grid;
  grid-template-columns: var(--graph-width) minmax(0, 1fr);
  min-height: 120px;
}

.git-graph__canvas {
  position: absolute;
  inset: 0 auto 0 0;
  overflow: visible;
}

.git-graph__canvas path {
  fill: none;
  stroke-width: 2;
  opacity: .76;
}

.git-graph__canvas circle {
  stroke: #ffffff;
  stroke-width: 2.4;
}

.git-graph__legend {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  text-transform: none;
}

.git-graph__legend i {
  width: 7px;
  height: 7px;
  border-radius: 999px;
}

.git-graph__legend .is-head { background: #12846f; }
.git-graph__legend .is-local { background: #2f6edb; }
.git-graph__legend .is-remote { background: #8f45d8; }
.git-graph__legend .is-tag { background: #c57716; }

.lane-0 { stroke: #2f6edb; fill: #2f6edb; }
.lane-1 { stroke: #12846f; fill: #12846f; }
.lane-2 { stroke: #c57716; fill: #c57716; }
.lane-3 { stroke: #8f45d8; fill: #8f45d8; }
.lane-4 { stroke: #cf4d78; fill: #cf4d78; }
.lane-5 { stroke: #536675; fill: #536675; }

.git-graph__canvas .is-feat { stroke: #2f6edb; fill: #2f6edb; }
.git-graph__canvas .is-fix { stroke: #c44747; fill: #c44747; }
.git-graph__canvas .is-refactor { stroke: #8f45d8; fill: #8f45d8; }
.git-graph__canvas .is-chore { stroke: #536675; fill: #536675; }
.git-graph__canvas .is-docs { stroke: #12846f; fill: #12846f; }
.git-graph__canvas .is-test { stroke: #0f766e; fill: #0f766e; }
.git-graph__canvas .is-perf { stroke: #c57716; fill: #c57716; }
.git-graph__canvas .is-build { stroke: #6f5bd8; fill: #6f5bd8; }
.git-graph__canvas .is-ci { stroke: #0d7e9a; fill: #0d7e9a; }
.git-graph__canvas .is-style { stroke: #cf4d78; fill: #cf4d78; }
.git-graph__canvas .is-revert { stroke: #9a5c20; fill: #9a5c20; }
.git-graph__canvas .ref-head {
  stroke: #12846f;
  fill: #12846f;
  stroke-width: 3;
  filter: drop-shadow(0 0 5px rgba(18, 132, 111, .35));
}
.git-graph__canvas .ref-local {
  stroke: #2f6edb;
  fill: #2f6edb;
  stroke-width: 2.6;
}
.git-graph__canvas .ref-remote {
  stroke: #8f45d8;
  fill: #8f45d8;
  stroke-width: 2.6;
}
.git-graph__canvas .ref-tag {
  stroke: #c57716;
  fill: #c57716;
  stroke-width: 2.6;
}

.git-graph__rows {
  grid-column: 2;
  display: grid;
  min-width: 0;
}

.git-commit {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  grid-template-rows: auto auto;
  box-sizing: border-box;
  height: var(--graph-row-height);
  align-items: center;
  gap: 0 5px;
  padding: 8px 10px 8px 0;
  border-left: 3px solid transparent;
  overflow: hidden;
}

.git-commit:hover {
  border-left-color: var(--commit-color);
  background: linear-gradient(90deg, var(--commit-soft), #ffffff 62%);
}

.git-commit__type {
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--commit-color) 24%, transparent);
  border-radius: 999px;
  color: var(--commit-color);
  background: var(--commit-soft);
  font: 800 10px/1 var(--mono);
  grid-row: 1 / -1;
  align-self: center;
}

.git-commit.is-feat .git-commit__type,
.git-commit.is-fix .git-commit__type,
.git-commit.is-refactor .git-commit__type {
  animation: commit-pulse 2.4s ease-in-out infinite;
}

.git-commit__main {
  display: grid;
  min-width: 0;
  gap: 4px;
  grid-column: 2;
  grid-row: 1;
}

.git-commit__main strong {
  overflow: hidden;
  color: var(--text-h);
  font-size: 10.5px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-commit__main small {
  overflow: hidden;
  color: var(--text-muted);
  font: 9px/1.2 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-commit__refs {
  grid-column: 2;
  grid-row: 2;
  display: flex;
  flex-wrap: nowrap;
  gap: 4px;
  min-width: 0;
  overflow: hidden;
}

.git-commit__refs span {
  max-width: 96px;
  overflow: hidden;
  padding: 2px 5px;
  border-radius: 4px;
  color: #174ea6;
  background: #e8f0ff;
  font: 800 8.5px/1 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-commit__refs span.is-head {
  color: #126b5d;
  background: #d8f0e9;
}

.git-commit__refs span.is-local {
  color: #174ea6;
  background: #e8f0ff;
}

.git-commit__refs span.is-remote {
  color: #6534a3;
  background: #f0e7fb;
}

.git-commit__refs span.is-tag {
  color: #91530e;
  background: #fff0d9;
}

@keyframes sync-dot {
  0%, 100% {
    transform: translate(0, -50%) scale(.86);
    opacity: .68;
  }
  50% {
    transform: translate(31px, -50%) scale(1);
    opacity: 1;
  }
}

@keyframes sync-spin {
  0% {
    transform: rotate(0deg) scale(1);
  }
  55% {
    transform: rotate(180deg) scale(1.08);
  }
  100% {
    transform: rotate(360deg) scale(1);
  }
}

@keyframes commit-pulse {
  0%, 100% {
    transform: scale(1);
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--commit-color) 24%, transparent);
  }
  50% {
    transform: scale(1.08);
    box-shadow: 0 0 0 5px transparent;
  }
}

@media (prefers-reduced-motion: reduce) {
  .git-sync__flow.is-moving::after,
  .git-actions.is-running i,
  .git-commit__type {
    animation: none;
  }
}

.git-panel__empty,
.git-panel__error {
  margin: 0;
  padding: 10px 11px;
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1.45;
}

.git-panel__error {
  border: 1px solid var(--err-border);
  border-radius: var(--radius);
  color: var(--err);
  background: var(--err-bg);
}
</style>
