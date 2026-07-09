<script setup>
const props = defineProps({
  settings: { type: Object, required: true },
  saving: { type: Boolean, default: false },
})
const emit = defineEmits(['save'])

function select(language) {
  if (language !== props.settings.output_language) emit('save', language)
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
@media (max-width: 720px) {
  .settings-page { padding: 24px 18px; }
  .settings-row { grid-template-columns: 1fr; }
  .language-picker { flex-wrap: wrap; }
}
</style>
