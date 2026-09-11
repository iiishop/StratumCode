<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

const data = ref({ profiles: [], runs: [], workspace: '' })
const error = ref('')
const busy = ref(false)
const editing = ref(false)
const selected = ref(null)
const detail = ref(null)
const input = ref('')
const argsText = ref('[]')
const form = ref({})
let timer, disposed = false, refreshing = false
const labels = { running: '运行中', stopping: '正在停止', stopped: '已停止', exited: '已退出', succeeded: '执行成功', failed: '失败' }
const readyLabels = { unverified: '就绪未确认', log_matched: '已匹配启动日志', confirmed: '已确认就绪', not_running: '未运行' }

async function request(url, body) {
  const response = await fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {})
  const text = await response.text()
  let result
  try { result = JSON.parse(text) } catch { throw new Error(text || `HTTP ${response.status}`) }
  if (!response.ok || result.error && !result.id) throw new Error(result.error || text)
  return result
}
async function refresh() {
  if (refreshing || disposed) return
  refreshing = true
  try {
    const next = await request('/api/applications')
    if (disposed) return
    data.value = next
    if (selected.value) {
      const log = await request('/api/applications/action', { action: 'read', run_id: selected.value, workspace: next.workspace })
      if (!disposed) detail.value = log
    }
  } catch (e) { if (!disposed) error.value = e.message }
  finally { refreshing = false }
}
async function act(action, values = {}) {
  busy.value = true
  error.value = ''
  try {
    const result = await request('/api/applications/action', { action, workspace: data.value.workspace, ...values })
    if (disposed) return
    if (result.id?.startsWith('run-')) { selected.value = result.id; detail.value = result }
    await refresh()
    return result
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
function edit(profile) {
  form.value = profile ? { ...profile } : { name: '', executable: '', args: [], cwd: '.', shell: 'none', command: '', kind: 'service', visible: false, stdin: false, encoding: 'utf-8', ready_text: '' }
  argsText.value = JSON.stringify(form.value.args)
  editing.value = true
}
async function save() {
  try {
    form.value.args = JSON.parse(argsText.value)
    if (await act('save', { profile: form.value })) editing.value = false
  } catch { error.value = '参数必须是 JSON 字符串数组，例如 ["run", "--project", "MyApp"]' }
}
async function choose(run) { selected.value = run.id; await refresh() }
function active(profile) { return data.value.runs.some(r => r.profile.id === profile.id && ['running', 'stopping'].includes(r.status)) }
function remove(profile) { if (window.confirm(`删除启动配置“${profile.name}”？不会删除项目文件。`)) act('delete', { profile_id: profile.id }) }
onMounted(() => { refresh(); timer = setInterval(refresh, 2000) })
onUnmounted(() => { disposed = true; clearInterval(timer) })
</script>

<template>
  <section class="applications">
    <header><div><strong>应用运行</strong><small>CLI · 桌面程序 · 服务</small></div><button :disabled="busy || !data.workspace" @click="edit()">新建配置</button></header>
    <p class="hint">运行不代表就绪，就绪不代表验收通过。仅管理本次 StratumCode 启动的进程，不接管外部窗口。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <form v-if="editing" @submit.prevent="save" class="editor">
      <label>配置名称<input v-model="form.name" required placeholder="例如：Unity 编辑器 / CLI 转换任务"></label>
      <label>运行类型<select v-model="form.kind"><option value="service">持续运行 / 服务</option><option value="desktop">桌面应用 / 编辑器</option><option value="task">一次性任务（检查退出码）</option></select></label>
      <label>启动方式<select v-model="form.shell"><option value="none">直接执行（推荐）</option><option value="powershell">Windows PowerShell</option><option value="pwsh">PowerShell 7</option><option value="cmd">CMD</option><option value="bash">Bash</option><option value="sh">sh</option></select></label>
      <template v-if="form.shell === 'none'">
        <label>可执行文件<input v-model="form.executable" required placeholder="python / dotnet / Unity.exe 的完整路径"></label>
        <label>参数（JSON 数组，无需 Shell 转义）<textarea v-model="argsText" rows="2" placeholder='["app.py"]'></textarea></label>
      </template>
      <label v-else>命令<textarea v-model="form.command" rows="3" required></textarea></label>
      <label>工作目录（项目内）<input v-model="form.cwd" required></label>
      <label>日志编码<input v-model="form.encoding" placeholder="utf-8 / gbk"></label>
      <label>可选：就绪日志文本<input v-model="form.ready_text" placeholder="精确匹配文本；为空则等待人工确认"></label>
      <label class="check"><input type="checkbox" v-model="form.visible">显示程序窗口（桌面应用可启用）</label>
      <label class="check"><input type="checkbox" v-model="form.stdin">启用标准输入（非完整终端 / TUI）</label>
      <footer><button type="submit" :disabled="busy">保存配置</button><button type="button" @click="editing = false">取消</button></footer>
    </form>
    <p v-if="!data.profiles.length" class="hint">还没有启动配置。无需填写网页地址，可直接启动项目的可执行程序。</p>
    <article v-for="p in data.profiles" :key="p.id" class="profile">
      <strong>{{ p.name }}</strong><code>{{ p.shell === 'none' ? p.executable : p.shell }} · {{ p.cwd }}</code>
      <footer><button :disabled="busy || active(p)" @click="act('start', { profile_id: p.id })">{{ active(p) ? '正在运行' : '启动' }}</button><button :disabled="busy" @click="edit(p)">编辑</button><button :disabled="busy || active(p)" @click="remove(p)">删除</button></footer>
    </article>
    <h4 v-if="data.runs.length">本次运行记录</h4>
    <button v-for="r in data.runs" :key="r.id" class="run" :class="{ selected: selected === r.id }" @click="choose(r)">
      <span>{{ r.profile.name }} <b>{{ labels[r.status] }}</b></span><small>{{ readyLabels[r.readiness] }} · PID {{ r.pid || '—' }}<template v-if="r.exit_code !== null"> · 退出码 {{ r.exit_code }}</template></small>
    </button>
    <article v-if="detail" class="logs">
      <strong>{{ detail.profile.name }} · {{ labels[detail.status] }}</strong>
      <p class="hint">{{ detail.readiness_note || '尚无就绪证据；退出成功也不代表功能验收通过。' }}</p>
      <p v-if="detail.error" class="error">{{ detail.error }}</p>
      <pre tabindex="0">{{ detail.output || '暂无标准输出；桌面程序可能只写入自身日志文件。' }}</pre>
      <small v-if="detail.dropped_chars">较早的 {{ detail.dropped_chars }} 个字符已从日志缓冲区移除。</small>
      <footer>
        <button v-if="['running', 'stopping'].includes(detail.status)" :disabled="busy" @click="act('stop', { run_id: detail.id })">停止进程树</button>
        <button :disabled="busy || detail.status === 'stopping' || !data.profiles.some(p => p.id === detail.profile.id)" @click="act('restart', { run_id: detail.id })">重新启动</button>
      </footer>
      <form v-if="detail.status === 'running'" @submit.prevent="act('confirm_ready', { run_id: detail.id, note: input })">
        <label>观察记录 / 标准输入<textarea v-model="input" rows="2" maxlength="4096"></textarea></label>
        <footer><button :disabled="busy || !input.trim()">记录已就绪</button><button v-if="detail.profile.stdin" type="button" :disabled="busy" @click="act('input', { run_id: detail.id, text: input + '\n' })">发送输入</button><button v-if="detail.profile.stdin" type="button" :disabled="busy" @click="act('input', { run_id: detail.id, eof: true })">关闭输入</button></footer>
      </form>
    </article>
    <p class="hint">停止 / 重启会结束进程树，请先保存程序中的数据。配置会保存；运行记录和日志仅保留在当前宿主进程内。不要使用会立即退出并脱离管理的启动器。</p>
  </section>
</template>

<style scoped>
.applications { display: grid; gap: 12px; color: var(--text, #3f5274); font-size: 12px; }
header, footer { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
header div, label { display: grid; gap: 6px; } small, .hint, code { color: var(--text-muted, #71809c); font-size: 11px; overflow-wrap: anywhere; }
.hint { margin: 0; line-height: 1.6; } .error { color: #e98776; white-space: pre-wrap; }
.profile, .logs, .editor { display: grid; gap: 10px; padding: 12px; border: 1px solid #8883; border-radius: 8px; background: #88808; }
input, textarea, select, button { font: inherit; color: inherit; background: var(--bg-raised, #fff); border: 1px solid var(--border, #d9e3f5); border-radius: 5px; padding: 7px; min-width: 0; }
input, textarea, select { width: 100%; box-sizing: border-box; } textarea { resize: vertical; } button { cursor: pointer; } button:disabled { opacity: .45; cursor: default; }
.check { display: flex; align-items: center; } .check input { width: auto; }
.run { display: grid; gap: 5px; text-align: left; } .run span { display: flex; justify-content: space-between; gap: 8px; } .selected { border-color: #6aa89a; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 300px; overflow: auto; margin: 0; font: 11px/1.6 Consolas, monospace; }
code { display: block; } h4 { margin: 6px 0 0; } button:focus-visible { outline: 2px solid #6aa89a; outline-offset: 2px; }
</style>
