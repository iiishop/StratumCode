<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

const data = ref({ workspace: '', requests: [], history: [] })
const blank = () => ({ name: '', method: 'GET', url: '', body_type: 'none', body: '', bearer_env: '', timeout_seconds: 20, max_response_bytes: 32768 })
const form = ref(blank())
const selectedId = ref('')
const headers = ref([]), query = ref([]), checks = ref([])
const result = ref(null), busy = ref(false), error = ref(''), responseTab = ref('body')
let disposed = false, timer, refreshing = false
const prettyBody = computed(() => {
  if (!result.value) return ''
  try { return JSON.stringify(JSON.parse(result.value.body), null, 2) } catch { return result.value.body }
})
async function api(body) {
  const response = await fetch(body ? '/api/http/action' : '/api/http', body ? {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, workspace: data.value.workspace }),
  } : {})
  const text = await response.text()
  let value
  try { value = JSON.parse(text) } catch { throw new Error(`HTTP ${response.status}: ${text.slice(0, 200)}`) }
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`)
  return value
}
async function refresh() {
  if (refreshing || disposed) return
  refreshing = true
  try { const value = await api(); if (!disposed) data.value = value }
  catch (e) { if (!disposed) error.value = e.message }
  finally { refreshing = false }
}
function edit(p) {
  const value = p ? JSON.parse(JSON.stringify(p)) : blank()
  selectedId.value = value.id || ''
  delete value.id
  headers.value = value.headers || []
  query.value = value.query || []
  checks.value = (value.assertions || []).map(c => ({ ...c, expectedText: JSON.stringify(c.expected) }))
  delete value.headers; delete value.query; delete value.assertions
  form.value = value
  error.value = ''
}
function requestValue() {
  return { ...form.value, headers: headers.value, query: query.value,
    assertions: checks.value.map(c => {
      const item = { kind: c.kind, expected: JSON.parse(c.expectedText) }
      if (c.kind === 'header') item.name = c.name || ''
      if (c.kind === 'json_pointer') item.pointer = c.pointer || ''
      return item
    }),
  }
}
async function act(action, extra = {}) {
  busy.value = true; error.value = ''
  try {
    const body = { action, ...extra }
    if (action === 'send' || action === 'save') body.request = requestValue()
    if (action === 'save') body.request_id = selectedId.value
    const value = await api(body)
    if (disposed) return
    if (action === 'send' || action === 'read') result.value = value
    if (action === 'save') selectedId.value = value.id
    if (action === 'delete') edit()
    await refresh()
  } catch (e) { if (!disposed) error.value = e.message }
  finally { busy.value = false }
}
function remove() {
  if (window.confirm('删除此保存请求？不会发送 HTTP 请求。')) act('delete', { request_id: selectedId.value })
}
onMounted(() => { refresh(); timer = setInterval(refresh, 4000) })
onUnmounted(() => { disposed = true; clearInterval(timer) })
</script>

<template>
  <section class="http-panel">
    <header><div><strong>API 请求台</strong><small>HTTPX · 实际响应与契约检查</small></div><button :disabled="busy" @click="edit()">新建</button></header>
    <p class="hint">请求由 StratumCode 后端发送，可访问本机服务。不会自动重试或跟随重定向；发送前确认目标和数据操作已获授权。</p>
    <label>保存的请求<select :value="selectedId" :disabled="busy" @change="edit(data.requests.find(p => p.id === $event.target.value))"><option value="">未保存请求</option><option v-for="p in data.requests" :key="p.id" :value="p.id">{{ p.method }} · {{ p.name || p.url }}</option></select></label>
    <form @submit.prevent="act('send')">
      <fieldset :disabled="busy || !data.workspace">
        <label>名称<input v-model="form.name" placeholder="例如：创建阅读标注"></label>
        <div class="request-line"><select v-model="form.method" aria-label="HTTP 方法"><option v-for="m in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']" :key="m">{{ m }}</option></select><input v-model="form.url" aria-label="请求 URL" required placeholder="http://localhost:8000/api/papers"></div>
        <details v-for="[title, items] in [['Query 参数', query], ['Headers', headers]]" :key="title">
          <summary>{{ title }} <small>{{ items.length }}</small></summary>
          <div v-for="(pair, i) in items" :key="i" class="pair"><input v-model="pair.name" aria-label="参数名" placeholder="name"><input v-model="pair.value" aria-label="参数值" placeholder="value"><button type="button" aria-label="删除参数" @click="items.splice(i, 1)">×</button></div>
          <button type="button" @click="items.push({ name: '', value: '' })">添加</button>
        </details>
        <label>Body<select v-model="form.body_type"><option value="none">无</option><option value="json">JSON</option><option value="text">Text</option><option value="form">Form URL encoded</option></select></label>
        <textarea v-if="form.body_type !== 'none'" v-model="form.body" rows="6" aria-label="请求体" :placeholder="form.body_type === 'form' ? '[{&quot;name&quot;:&quot;title&quot;,&quot;value&quot;:&quot;paper&quot;}]' : '请求正文'"></textarea>
        <details><summary>认证与限制</summary>
          <label>Bearer 环境变量名称<input v-model="form.bearer_env" placeholder="API_TOKEN（不是 token 值）"></label>
          <label>总超时（秒）<input v-model.number="form.timeout_seconds" type="number" min="1" max="120"></label>
          <label>响应上限（字节）<input v-model.number="form.max_response_bytes" type="number" min="256" max="262144"></label>
          <p class="hint">TLS 校验开启，不继承系统代理或 Cookie。二进制、压缩或非 UTF-8 响应以 Base64 展示。</p>
        </details>
        <details open><summary>断言 <small>{{ checks.length }}</small></summary>
          <div v-for="(c, i) in checks" :key="i" class="assertion">
            <div class="pair"><select v-model="c.kind" aria-label="断言类型"><option value="status">状态码相等</option><option value="header">响应头相等</option><option value="json_pointer">JSON 值相等</option><option value="body_contains">正文包含</option></select><button type="button" aria-label="删除断言" @click="checks.splice(i, 1)">×</button></div>
            <input v-if="c.kind === 'header'" v-model="c.name" placeholder="content-type" aria-label="响应头名称">
            <input v-if="c.kind === 'json_pointer'" v-model="c.pointer" placeholder="/data/0/id（空值表示整个 JSON）" aria-label="JSON pointer">
            <input v-model="c.expectedText" aria-label="期望 JSON 值" placeholder='期望值：200 / true / "ok"'>
          </div>
          <button type="button" @click="checks.push({ kind: 'status', expectedText: '200' })">添加断言</button>
          <p class="hint">期望值使用 JSON 格式，字符串需要双引号。无断言表示未检查，不是通过。</p>
        </details>
        <footer><button type="submit" class="send">{{ busy ? '处理中…' : '发送请求' }}</button><button type="button" @click="act('save')">保存</button><button v-if="selectedId" type="button" @click="remove">删除</button></footer>
      </fieldset>
    </form>
    <p class="hint">保存内容为明文，请勿在 URL、参数、正文或自定义头中保存秘密。响应可能含敏感内容；Agent 调用结果也会进入对话。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <section v-if="result" class="response" aria-live="polite">
      <header><strong>{{ result.status_code ?? '无 HTTP 响应' }}</strong><span>{{ result.elapsed_ms }} ms · {{ result.captured_bytes ?? 0 }} B</span></header>
      <p class="response-url">{{ result.method }} {{ result.request_url }}</p>
      <p :class="result.assertions_passed === false ? 'error' : 'hint'">{{ result.assertions_passed === null ? '未设置断言' : result.assertions_passed ? '所有声明的断言通过（不代表整体验收）' : '断言失败或未能执行' }}</p>
      <p v-if="result.error" class="error">{{ result.error }}</p>
      <p v-if="result.truncated" class="error">响应已截断，正文断言未执行。</p>
      <nav><button v-for="t in ['body', 'headers', 'assertions']" :key="t" :aria-pressed="responseTab === t" @click="responseTab = t">{{ t }}</button></nav>
      <small v-if="responseTab === 'body'">{{ result.body_encoding }}</small>
      <pre tabindex="0">{{ responseTab === 'body' ? prettyBody : JSON.stringify(result[responseTab], null, 2) }}</pre>
    </section>
    <details open><summary>最近请求 <small>{{ data.history.length }}</small></summary>
      <p class="hint">Agent 和手动请求共用记录；最近 100 条响应保留在内存，重启后清空。保存的请求仍保留。</p>
      <button v-for="r in data.history" :key="r.id" class="history" :disabled="busy" @click="act('read', { run_id: r.id })"><strong>{{ r.method }} · {{ r.name || r.id }}</strong><small>{{ r.status_code ?? '网络失败' }} · {{ r.elapsed_ms }} ms · {{ new Date(r.created_at * 1000).toLocaleTimeString() }}</small></button>
    </details>
  </section>
</template>

<style scoped>
.http-panel { display: grid; gap: 12px; color: var(--text); font-size: 12px; min-width: 0; }
header, footer, .request-line, .pair, nav { display: flex; gap: 8px; align-items: center; }
header { justify-content: space-between; } header small, .history small { display: block; margin-top: 4px; color: var(--text-muted); }
label { display: grid; gap: 5px; } fieldset { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; gap: 12px; }
input, select, textarea, button { font: inherit; color: var(--text); background: var(--bg-raised); border: 1px solid var(--border); border-radius: 5px; padding: 7px; box-sizing: border-box; min-width: 0; }
input, textarea { width: 100%; } textarea, pre { font-family: Consolas, monospace; } textarea { resize: vertical; }
button { cursor: pointer; } button:disabled { opacity: .5; cursor: default; } .send { background: #126957; color: white; }
button[aria-pressed="true"] { border-color: #12846f; } .request-line input { flex: 1; } .pair { margin: 6px 0; } .pair input { flex: 1; } .pair select { flex: 1; }
summary { cursor: pointer; padding: 7px 0; } details { border-top: 1px solid var(--border); } details label { margin: 8px 0; }
.hint { color: var(--text-muted); line-height: 1.6; margin: 0; } .error { color: #cc4545; overflow-wrap: anywhere; }
.response-url { overflow-wrap: anywhere; color: var(--text-muted); }
.assertion { display: grid; gap: 5px; margin-bottom: 10px; } .response { border: 1px solid var(--border); border-radius: 7px; padding: 10px; min-width: 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 420px; overflow: auto; font-size: 11px; line-height: 1.5; }
.history { display: block; width: 100%; text-align: left; margin: 5px 0; overflow-wrap: anywhere; }
</style>
