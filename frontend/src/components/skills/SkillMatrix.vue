<script setup>
defineProps({
  items: { type: Array, default: () => [] },
  targets: { type: Array, default: () => [] },
  assignments: { type: Object, default: () => ({}) },
  modes: { type: Object, default: () => ({}) },
})
defineEmits(['select'])

function cellState(target, skillId, assignments, modes) {
  if ((assignments[target.id] || []).includes(skillId)) return 'explicit'
  if (target.kind !== 'global' && modes[target.id] !== 'replace' && (assignments.global || []).includes(skillId)) {
    return 'inherited'
  }
  return ''
}
</script>

<template>
  <div class="sm">
    <table>
      <thead>
        <tr>
          <th>Skill</th>
          <th v-for="target in targets" :key="target.id">
            <button type="button" @click="$emit('select', target.id)">{{ target.label }}</button>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id">
          <th>{{ item.name }}</th>
          <td
            v-for="target in targets"
            :key="target.id"
            :class="`is-${cellState(target, item.id, assignments, modes) || 'empty'}`"
          >
            {{ cellState(target, item.id, assignments, modes) === 'explicit' ? '●' : cellState(target, item.id, assignments, modes) === 'inherited' ? '○' : '' }}
          </td>
        </tr>
      </tbody>
    </table>
    <p><span>● explicit</span><span>○ inherited from Global</span></p>
  </div>
</template>

<style scoped>
.sm { max-height: 560px; overflow: auto; }
.sm table { width: max-content; min-width: 100%; border-collapse: collapse; }
.sm th, .sm td { height: 38px; padding: 5px 9px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); text-align: center; font: 9px var(--mono); }
.sm th:first-child { position: sticky; left: 0; z-index: 1; min-width: 180px; color: var(--text-h); background: var(--bg-raised); text-align: left; }
.sm thead th { position: sticky; top: 0; z-index: 2; background: var(--bg-raised); }
.sm thead th:first-child { z-index: 3; }
.sm th button { border: 0; color: var(--text-h); background: transparent; font: inherit; cursor: pointer; }
.sm td.is-explicit { color: var(--accent); }
.sm td.is-inherited { color: var(--text-muted); }
.sm > p { display: flex; gap: 18px; margin: 0; padding: 9px 12px; color: var(--text-muted); font: 8px var(--mono); }
</style>
