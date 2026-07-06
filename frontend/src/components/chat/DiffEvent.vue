<script setup>
import { highlightCode } from '../../lib/highlight'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })

function lineType(hunk, line) {
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'remove'
  return ['add', 'remove'].includes(hunk.type) ? hunk.type : 'keep'
}
function prefix(hunk, line) {
  return /^[ +\-]/.test(line) ? line[0] : { add: '+', remove: '-', keep: ' ' }[lineType(hunk, line)]
}
function content(line) { return /^[ +\-]/.test(line) ? line.slice(1) : line }
function count(type) {
  return props.event.hunks.reduce((total, hunk) => total + hunk.lines.filter(line => lineType(hunk, line) === type).length, 0)
}
</script>

<template>
  <EventFrame kind="diff" symbol="±" label="Proposed patch" :detail="event.path" status="review">
    <div class="event-diff__stats">
      <span class="event-diff__add">+{{ count('add') }}</span>
      <span class="event-diff__remove">−{{ count('remove') }}</span>
    </div>
    <pre class="event-diff__code"><code><template v-for="(hunk, hi) in event.hunks" :key="hi"><span v-for="(line, li) in hunk.lines" :key="li" class="event-diff__line" :class="`event-diff__line--${lineType(hunk, line)}`"><span class="event-diff__marker">{{ prefix(hunk, line) }}</span><span v-html="highlightCode(content(line), event.path)"></span></span></template></code></pre>
    <footer class="event-diff__footer">
      <template v-if="event.accepted === null">
        <button type="button" class="event-diff__accept" @click="event.accepted = true">Accept</button>
        <button type="button" class="event-diff__reject" @click="event.accepted = false">Reject</button>
      </template>
      <strong v-else :class="event.accepted ? 'is-accepted' : 'is-rejected'">{{ event.accepted ? 'Accepted' : 'Rejected' }}</strong>
    </footer>
  </EventFrame>
</template>

<style scoped>
.event-diff__stats { display: flex; justify-content: flex-end; gap: 8px; margin: -2px 2px 6px; font: 700 9px/1 var(--mono, monospace); }
.event-diff__add { color: #1756d1; }.event-diff__remove { color: #d92d3d; }
.event-diff__code { margin: 0; padding: 7px 0; overflow: auto; border: 1px solid #dae3ef; border-radius: 8px; background: rgba(247, 249, 253, .86); color: #304863; font: 10px/1.55 var(--mono, monospace); }
.event-diff__line { display: block; padding: 0 10px; white-space: pre; }.event-diff__line--add { background: #e9f1ff; }.event-diff__line--remove { background: #fff0f1; }.event-diff__marker { display: inline-block; width: 16px; color: #8291a5; user-select: none; }
.event-diff__footer { display: flex; justify-content: flex-end; gap: 7px; padding-top: 8px; }.event-diff__footer button { padding: 5px 10px; border-radius: 6px; font: 650 10px/1 inherit; cursor: pointer; }.event-diff__accept { border: 1px solid #1756d1; background: #1756d1; color: #fff; }.event-diff__reject { border: 1px solid #e1aab0; background: #fff; color: #d92d3d; }.is-accepted { color: #1756d1; }.is-rejected { color: #d92d3d; }
</style>
