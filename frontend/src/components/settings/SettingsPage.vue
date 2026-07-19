<script setup>
const props = defineProps({
  settings: { type: Object, required: true },
  saving: { type: Boolean, default: false },
})
const emit = defineEmits(['save'])

function select(language) {
  if (language !== props.settings.output_language) emit('save', 'output_language', language)
}

function setScale(value) {
  emit('save', 'font_scale', value)
}

function setRoundLimit(key, value) {
  const parsed = Number.parseInt(value, 10)
  emit('save', key, Number.isFinite(parsed) ? Math.max(0, parsed) : 0)
}

const scaleSteps = [0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.3]
function scaleLabel(v) {
  return `${Math.round(v * 100)}%`
}

function roundLabel(value) {
  return Number(value || 0) <= 0 ? 'unlimited' : `${value}`
}
</script>

<template>
  <main class="settings-page">
    <section class="settings-panel">
      <div class="settings-panel__head">
        <h1>Settings</h1>
        <p>Control user-visible agent output without changing tool calls or code evidence.</p>
      </div>

      <div class="settings-row">
        <div>
          <strong>Output language</strong>
          <span>Summaries, questions, beliefs, and visible reasoning text.</span>
        </div>
        <div class="language-picker" role="group" aria-label="Output language">
          <button
            v-for="language in settings.languages || []"
            :key="language.id"
            type="button"
            :class="{ 'is-active': language.id === settings.output_language }"
            :disabled="saving"
            @click="select(language.id)"
          >
            {{ language.label }}
          </button>
        </div>
      </div>

      <div class="settings-row">
        <div>
          <strong>Font scale</strong>
          <span>Adjust the entire interface proportionally from the current size.</span>
        </div>
        <div class="scale-picker">
          <label class="scale-slider">
            <input
              type="range"
              :min="scaleSteps[0]"
              :max="scaleSteps[scaleSteps.length - 1]"
              :step="0.05"
              :value="settings.font_scale || 1"
              :disabled="saving"
              @change="setScale(parseFloat($event.target.value))"
            />
          </label>
          <span class="scale-value">{{ scaleLabel(settings.font_scale || 1) }}</span>
        </div>
      </div>

      <div class="settings-row settings-row--stack">
        <div>
          <strong>Loop limits</strong>
          <span>Set any value to 0 for unlimited model/tool rounds.</span>
        </div>
        <div class="round-grid">
          <label
            v-for="item in settings.round_limits || []"
            :key="item.key"
            class="round-limit"
          >
            <span>
              <strong>{{ item.label }}</strong>
              <em>{{ item.description }}</em>
            </span>
            <input
              type="number"
              min="0"
              step="1"
              :value="item.value || 0"
              :disabled="saving"
              @change="setRoundLimit(item.key, $event.target.value)"
            />
            <b>{{ roundLabel(item.value) }}</b>
          </label>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.settings-page {
  max-width: 760px;
  margin: 0 auto;
  padding: 40px 48px;
}
.settings-panel {
  display: grid;
  gap: 22px;
}
.settings-panel__head h1 {
  margin: 0;
  color: var(--text-h);
  font-size: 18px;
  font-weight: 600;
}
.settings-panel__head p {
  margin: 7px 0 0;
  color: var(--text-muted);
  font-size: 12px;
}
.settings-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 20px;
  align-items: center;
  padding: 16px;
  border: 1px solid #dbe5f0;
  border-radius: 8px;
  background: #fff;
}
.settings-row--stack {
  grid-template-columns: 1fr;
  align-items: stretch;
}
.settings-row strong,
.settings-row span {
  display: block;
}
.settings-row strong {
  color: #25435f;
  font-size: 12px;
}
.settings-row span {
  margin-top: 4px;
  color: #6e8197;
  font-size: 10px;
  line-height: 1.4;
}
.language-picker {
  display: flex;
  gap: 6px;
}
.language-picker button {
  min-width: 76px;
  padding: 8px 10px;
  border: 1px solid #d7e2ef;
  border-radius: 7px;
  color: #29445f;
  background: #fff;
  font: 700 10px/1 var(--mono);
  cursor: pointer;
}
.language-picker button:hover,
.language-picker button.is-active {
  border-color: #7ca5e8;
  background: #f2f6ff;
  color: #1756d1;
}
.language-picker button:disabled {
  cursor: wait;
  opacity: .65;
}
.scale-picker {
  display: flex;
  align-items: center;
  gap: 10px;
}
.scale-slider input {
  width: 160px;
  height: 5px;
  accent-color: #1756d1;
  cursor: pointer;
}
.scale-slider input:disabled {
  cursor: wait;
  opacity: .5;
}
.scale-value {
  min-width: 36px;
  color: #1756d1;
  font: 700 11px/1 var(--mono);
  text-align: right;
}
.round-grid {
  display: grid;
  gap: 8px;
}
.round-limit {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px 70px;
  gap: 10px;
  align-items: center;
  padding: 10px 0;
  border-top: 1px solid #edf2f8;
}
.round-limit:first-child {
  border-top: 0;
}
.round-limit em {
  display: block;
  margin-top: 3px;
  color: #6e8197;
  font-style: normal;
  font-size: 10px;
  line-height: 1.35;
}
.round-limit input {
  width: 72px;
  padding: 7px 8px;
  border: 1px solid #d7e2ef;
  border-radius: 7px;
  color: #25435f;
  font: 700 11px/1 var(--mono);
}
.round-limit input:disabled {
  cursor: wait;
  opacity: .55;
}
.round-limit b {
  color: #1756d1;
  font: 700 10px/1 var(--mono);
  text-align: right;
}
@media (max-width: 720px) {
  .settings-page { padding: 24px 18px; }
  .settings-row { grid-template-columns: 1fr; }
  .language-picker { flex-wrap: wrap; }
  .round-limit { grid-template-columns: 1fr 72px; }
  .round-limit b { grid-column: 2; }
}
</style>
