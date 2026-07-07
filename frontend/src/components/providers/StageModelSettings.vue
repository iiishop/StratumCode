<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { animate } from 'animejs'

const props = defineProps({ providers: { type: Array, required: true } })
const rows = reactive({
  default: { provider_id: '', model_id: '', models: [], loading: false, saved: false },
  evidence: { provider_id: '', model_id: '', models: [], loading: false, saved: false },
})
const error = ref('')

const providerName = id => props.providers.find(provider => provider.id === Number(id))?.name || ''
const defaultLabel = computed(() => {
  const row = rows.default
  return row.provider_id && row.model_id ? `${providerName(row.provider_id)} / ${row.model_id}` : 'Not configured'
})

async function request(path, body) {
  const response = await fetch(`/api${path}`, body ? {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  } : {})
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`)
  return data
}

async function load() {
  error.value = ''
  try {
    const settings = await request('/model-settings')
    for (const setting of settings) {
      if (!rows[setting.stage]) continue
      rows[setting.stage].provider_id = String(setting.provider_id)
      rows[setting.stage].model_id = setting.model_id
    }
  } catch (reason) {
    error.value = reason.message
  }
}

async function loadModels(stage) {
  const row = rows[stage]
  row.models = []
  row.saved = false
  if (!row.provider_id) return
  row.loading = true
  error.value = ''
  try {
    const result = await request('/providers/models', { provider_id: Number(row.provider_id) })
    row.models = result.models || []
  } catch (reason) {
    error.value = reason.message
  } finally {
    row.loading = false
  }
}

async function save(stage, event) {
  const row = rows[stage]
  if (!row.provider_id || !row.model_id.trim()) return
  error.value = ''
  try {
    await request('/model-settings/save', {
      stage,
      provider_id: Number(row.provider_id),
      model_id: row.model_id,
    })
    row.saved = true
    animate(event.currentTarget, { scale: [1, .92, 1], duration: 360, ease: 'outBack' })
  } catch (reason) {
    error.value = reason.message
  }
}

async function clearEvidence() {
  try {
    await request('/model-settings/delete', { stage: 'evidence' })
    Object.assign(rows.evidence, { provider_id: '', model_id: '', models: [], saved: false })
  } catch (reason) {
    error.value = reason.message
  }
}

onMounted(load)
</script>

<template>
  <section class="stage-models">
    <header>
      <div>
        <span class="stage-models__eyebrow">Agent routing</span>
        <h2>Models by stage</h2>
        <p>An unset stage inherits the global default.</p>
      </div>
      <span class="stage-models__default">{{ defaultLabel }}</span>
    </header>

    <p v-if="error" class="stage-models__error">{{ error }}</p>

    <div v-for="stage in ['default', 'evidence']" :key="stage" class="stage-row">
      <div class="stage-row__identity">
        <span>{{ stage === 'default' ? '00' : '01' }}</span>
        <div>
          <strong>{{ stage === 'default' ? 'Global default' : 'Gather evidence' }}</strong>
          <small v-if="stage === 'evidence' && !rows.evidence.provider_id">Inherits {{ defaultLabel }}</small>
          <small v-else>{{ stage === 'default' ? 'Fallback for every stage' : 'Hypothesis verification loop' }}</small>
        </div>
      </div>

      <label>
        <span>Provider</span>
        <select v-model="rows[stage].provider_id" @change="loadModels(stage)">
          <option value="">{{ stage === 'evidence' ? 'Use global default' : 'Select provider' }}</option>
          <option v-for="provider in providers" :key="provider.id" :value="String(provider.id)">{{ provider.name }}</option>
        </select>
      </label>

      <label>
        <span>Model</span>
        <input
          v-model="rows[stage].model_id"
          :list="`models-${stage}`"
          :disabled="!rows[stage].provider_id"
          :placeholder="rows[stage].loading ? 'Loading models…' : 'Model id'"
        />
        <datalist :id="`models-${stage}`">
          <option v-for="model in rows[stage].models" :key="model" :value="model" />
        </datalist>
      </label>

      <div class="stage-row__actions">
        <button type="button" :disabled="!rows[stage].provider_id || !rows[stage].model_id" @click="save(stage, $event)">
          {{ rows[stage].saved ? 'Saved' : 'Save' }}
        </button>
        <button v-if="stage === 'evidence' && rows.evidence.provider_id" type="button" class="stage-row__clear" @click="clearEvidence">Inherit</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stage-models { margin: 0 0 26px; overflow: hidden; border: 1px solid #cbd9eb; border-radius: 13px; background: linear-gradient(135deg, rgba(235,243,255,.9), #fff 45%); box-shadow: 0 12px 30px rgba(39,72,119,.07); }
.stage-models header { display: flex; align-items: center; justify-content: space-between; padding: 16px 18px; border-bottom: 1px solid #d7e2f0; }
.stage-models__eyebrow { color: #1756d1; font: 800 8px/1 var(--mono); letter-spacing: .12em; text-transform: uppercase; }.stage-models h2 { margin: 4px 0 2px; color: #1e3a5a; font-size: 14px; }.stage-models header p { margin: 0; color: #7b8da4; font-size: 10px; }
.stage-models__default { max-width: 280px; overflow: hidden; padding: 6px 9px; border: 1px solid #c6d9f5; border-radius: 6px; color: #1756d1; background: #edf4ff; font: 8.5px/1 var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.stage-models__error { margin: 0; padding: 8px 18px; color: #a43d3d; background: #fff0ef; font-size: 10px; }
.stage-row { display: grid; grid-template-columns: 1.2fr 1fr 1.4fr auto; align-items: end; gap: 10px; padding: 13px 16px; border-bottom: 1px solid #e0e8f3; }.stage-row:last-child { border: 0; }
.stage-row__identity { display: flex; min-width: 0; align-items: center; gap: 9px; }.stage-row__identity > span { display: grid; width: 28px; height: 28px; place-items: center; border-radius: 8px; color: #fff; background: #1756d1; font: 800 9px/1 var(--mono); }.stage-row:nth-of-type(3) .stage-row__identity > span { color: #654d00; background: #f5c642; }.stage-row__identity div { display: grid; min-width: 0; gap: 3px; }.stage-row__identity strong { color: #29435f; font-size: 10.5px; }.stage-row__identity small { overflow: hidden; color: #7e91a8; font-size: 8.5px; text-overflow: ellipsis; white-space: nowrap; }
.stage-row label { display: grid; gap: 4px; }.stage-row label > span { color: #788ba3; font: 700 8px/1 var(--mono); text-transform: uppercase; }.stage-row select,.stage-row input { min-width: 0; height: 31px; padding: 0 8px; border: 1px solid #cbd8e8; border-radius: 7px; color: #29445f; background: #fff; font: 9px/1 var(--mono); outline: none; }.stage-row select:focus,.stage-row input:focus { border-color: #1756d1; box-shadow: 0 0 0 2px rgba(23,86,209,.1); }.stage-row input:disabled { opacity: .55; }
.stage-row__actions { display: flex; gap: 4px; }.stage-row__actions button { height: 31px; padding: 0 11px; border: 0; border-radius: 7px; color: #fff; background: #1756d1; font: 700 9px/1 var(--mono); cursor: pointer; }.stage-row__actions button:disabled { opacity: .4; cursor: default; }.stage-row__actions .stage-row__clear { color: #7b6640; background: #fff1bd; }
@media (max-width: 760px) { .stage-row { grid-template-columns: 1fr 1fr; }.stage-row__identity { grid-column: 1 / -1; } }
</style>
