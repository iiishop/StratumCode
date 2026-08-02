<script setup>
defineProps({
  workspace: { type: Object, default: null },
})
</script>

<template>
  <section class="structure-panel">
    <header class="structure-panel__head">
      <div>
        <p class="structure-panel__eyebrow">Workspace map</p>
        <h1>Code Structure</h1>
      </div>
      <div class="structure-panel__workspace">
        <span></span>
        {{ workspace?.name || workspace?.path || 'No workspace' }}
      </div>
    </header>

    <div class="structure-panel__stage">
      <div class="structure-panel__grid" aria-hidden="true">
        <div class="structure-panel__node structure-panel__node--root">
          <strong>Workspace</strong>
          <small>root</small>
        </div>
        <div class="structure-panel__node structure-panel__node--module">
          <strong>Modules</strong>
          <small>scan</small>
        </div>
        <div class="structure-panel__node structure-panel__node--symbol">
          <strong>Symbols</strong>
          <small>index</small>
        </div>
        <div class="structure-panel__node structure-panel__node--flow">
          <strong>Relations</strong>
          <small>edges</small>
        </div>
      </div>

      <div class="structure-panel__empty">
        <span class="structure-panel__mark">~</span>
        <div>
          <h2>Structure panel is ready</h2>
          <p>The programmatic workspace map will render here without touching the active work session.</p>
        </div>
      </div>
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
  background:
    linear-gradient(90deg, rgba(18, 132, 111, 0.055) 1px, transparent 1px),
    linear-gradient(rgba(197, 119, 22, 0.055) 1px, transparent 1px),
    #f8fbff;
  background-size: 44px 44px;
}

.structure-panel__head {
  display: flex;
  min-height: 76px;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 0 28px;
  border-bottom: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.84);
}

.structure-panel__eyebrow {
  margin: 0 0 3px;
  color: var(--text-muted);
  font: 10px/1 var(--mono);
}

.structure-panel__head h1 {
  margin: 0;
  color: var(--text-h);
  font: 580 22px/1.1 var(--heading);
}

.structure-panel__workspace {
  display: inline-flex;
  max-width: min(420px, 48vw);
  align-items: center;
  gap: 7px;
  overflow: hidden;
  color: var(--text-muted);
  font: 10px/1 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.structure-panel__workspace span {
  width: 6px;
  height: 6px;
  flex: 0 0 6px;
  border-radius: 50%;
  background: #12846f;
}

.structure-panel__stage {
  position: relative;
  display: grid;
  min-height: 0;
  flex: 1;
  place-items: center;
  overflow: hidden;
  padding: 32px;
}

.structure-panel__grid {
  position: absolute;
  inset: 12%;
  min-width: 520px;
  min-height: 300px;
}

.structure-panel__grid::before,
.structure-panel__grid::after {
  position: absolute;
  content: "";
  background: var(--border-strong);
  opacity: 0.72;
}

.structure-panel__grid::before {
  top: 50%;
  left: 13%;
  right: 13%;
  height: 1px;
}

.structure-panel__grid::after {
  top: 18%;
  bottom: 18%;
  left: 50%;
  width: 1px;
}

.structure-panel__node {
  position: absolute;
  display: flex;
  width: 132px;
  height: 58px;
  flex-direction: column;
  justify-content: center;
  padding: 0 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.9);
  box-shadow: var(--shadow-sm);
}

.structure-panel__node strong {
  color: var(--text-h);
  font: 650 11px/1.25 var(--mono);
}

.structure-panel__node small {
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 9px;
}

.structure-panel__node--root {
  top: calc(50% - 29px);
  left: calc(50% - 66px);
  border-color: var(--accent-border);
}

.structure-panel__node--module { top: 7%; left: 7%; }
.structure-panel__node--symbol { top: 7%; right: 7%; }
.structure-panel__node--flow { right: 7%; bottom: 7%; }

.structure-panel__empty {
  position: relative;
  z-index: 1;
  display: flex;
  width: min(520px, 100%);
  align-items: center;
  gap: 16px;
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.88);
  box-shadow: var(--shadow);
}

.structure-panel__mark {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  place-items: center;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  color: #12846f;
  background: #e3f5f0;
  font: 700 18px/1 var(--mono);
}

.structure-panel__empty h2 {
  margin: 0;
  color: var(--text-h);
  font: 600 15px/1.25 var(--heading);
}

.structure-panel__empty p {
  margin: 5px 0 0;
  color: var(--text-muted);
  font-size: 11px;
}

@media (max-width: 720px) {
  .structure-panel__head {
    align-items: flex-start;
    flex-direction: column;
    justify-content: center;
    padding: 14px 18px;
  }

  .structure-panel__workspace {
    max-width: 100%;
  }

  .structure-panel__stage {
    padding: 18px;
  }

  .structure-panel__grid {
    inset: 8%;
    min-width: 360px;
  }
}
</style>
