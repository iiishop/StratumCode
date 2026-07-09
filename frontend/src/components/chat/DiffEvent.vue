<script setup>
import { nextTick, onUnmounted, ref } from 'vue'
import { animate, createTimeline } from 'animejs'
import { highlightCode } from '../../lib/highlight'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })
const frameRef = ref(null)
let decisionTimeline

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

function spawnPixels(surface, accepted) {
  const bounds = surface.getBoundingClientRect()
  const colors = accepted
    ? ['#00a878', '#39e6b2', '#f5c842', '#ffffff']
    : ['#e11d74', '#ff4fa0', '#ffb000', '#ffffff']

  for (let index = 0; index < 26; index++) {
    const pixel = document.createElement('i')
    const size = 3 + Math.floor(Math.random() * 5)
    const angle = (Math.PI * 2 * index) / 26 + (Math.random() - .5) * .28
    const distance = 34 + Math.random() * 76
    const color = colors[index % colors.length]
    Object.assign(pixel.style, {
      position: 'fixed',
      zIndex: '9999',
      left: `${bounds.left + bounds.width * (.28 + Math.random() * .44)}px`,
      top: `${bounds.top + bounds.height * (.25 + Math.random() * .5)}px`,
      width: `${size}px`,
      height: `${size}px`,
      borderRadius: index % 3 === 0 ? '50%' : '1px',
      background: color,
      boxShadow: `0 0 ${size * 2}px ${color}`,
      pointerEvents: 'none',
    })
    document.body.appendChild(pixel)
    animate(pixel, {
      translateX: [0, Math.cos(angle) * distance],
      translateY: [0, Math.sin(angle) * distance + 18],
      rotate: [0, (Math.random() - .5) * 260],
      scale: [0, 1.35, .2],
      opacity: [0, 1, 0],
      delay: Math.random() * 65,
      duration: 520 + Math.random() * 240,
      ease: 'outExpo',
      onComplete: () => pixel.remove(),
    })
  }
}

async function impact(surface, accepted) {
  props.event.accepted = accepted
  await nextTick()
  spawnPixels(surface, accepted)
  animate(surface, accepted ? {
    translateY: [0, 3, -7, 0],
    scale: [1, .965, 1.035, 1],
    rotate: [0, -.35, .2, 0],
    duration: 680,
    ease: 'outElastic(1, .48)',
  } : {
    translateX: [0, -9, 8, -6, 4, -2, 0],
    scale: [1, .97, 1.018, 1],
    rotate: [0, -.6, .55, -.35, .2, 0],
    duration: 560,
    ease: 'outQuint',
  })
}

function decide(accepted, button) {
  if (props.event.accepted !== null) return
  const surface = frameRef.value?.$el?.querySelector('.event-frame__surface')
  if (!surface || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    props.event.accepted = accepted
    return
  }

  decisionTimeline?.revert()
  decisionTimeline = createTimeline({ defaults: { ease: 'outExpo' } })
    .add(button, { scale: .8, translateY: 2, duration: 95, ease: 'inQuad' })
    .add({ duration: 85 })
    .call(() => impact(surface, accepted))
    .add(button, {
      scale: [0.8, 1.18, .96, 1],
      translateY: [2, -3, 0],
      duration: 460,
      ease: 'outElastic(1, .42)',
    })
}

onUnmounted(() => decisionTimeline?.revert())
</script>

<template>
  <EventFrame
    ref="frameRef"
    kind="diff"
    :symbol="event.accepted === true ? '✓' : event.accepted === false ? '×' : '±'"
    label="Proposed patch"
    :detail="event.path"
    :status="event.accepted === true ? 'accepted' : event.accepted === false ? 'rejected' : 'review'"
    :state="event.accepted === true ? 'accepted' : event.accepted === false ? 'rejected' : 'pending'"
  >
    <div class="event-diff__stats">
      <span class="event-diff__add">+{{ count('add') }}</span>
      <span class="event-diff__remove">−{{ count('remove') }}</span>
    </div>
    <pre class="event-diff__code"><code><template v-for="(hunk, hi) in event.hunks" :key="hi"><span v-for="(line, li) in hunk.lines" :key="li" class="event-diff__line" :class="`event-diff__line--${lineType(hunk, line)}`"><span class="event-diff__marker">{{ prefix(hunk, line) }}</span><span v-html="highlightCode(content(line), event.path)"></span></span></template></code></pre>
    <footer class="event-diff__footer">
      <template v-if="event.accepted === null">
        <button type="button" class="event-diff__accept" @click="decide(true, $event.currentTarget)">Accept</button>
        <button type="button" class="event-diff__reject" @click="decide(false, $event.currentTarget)">Reject</button>
      </template>
      <strong v-else :class="event.accepted ? 'is-accepted' : 'is-rejected'">{{ event.accepted ? 'Accepted' : 'Rejected' }}</strong>
    </footer>
  </EventFrame>
</template>

<style scoped>
.event-diff__stats {
  display: flex;
  min-height: 20px;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin: 0 2px 6px;
  padding-top: 2px;
  font: 700 10px/16px var(--mono, monospace);
}

.event-diff__add { color: #00a878; }
.event-diff__remove { color: #e11d74; }

.event-diff__code {
  margin: 0;
  padding: 8px 0;
  overflow: auto;
  border: 1px solid rgba(23, 86, 209, .1);
  border-radius: 9px;
  background: #f8fafd;
  color: #304863;
  font: var(--font-code, 12px)/1.6 var(--mono, monospace);
}

.event-diff__line {
  display: block;
  padding: 0 12px;
  white-space: pre;
  transition: background .12s ease;
}

.event-diff__line--add {
  background: linear-gradient(90deg, rgba(0, 168, 120, .08), rgba(0, 168, 120, .04));
}

.event-diff__line--remove {
  background: linear-gradient(90deg, rgba(225, 29, 116, .07), rgba(225, 29, 116, .03));
}

.event-diff__marker {
  display: inline-block;
  width: 18px;
  color: #8291a5;
  user-select: none;
}

.event-diff__footer {
  display: flex;
  min-height: 34px;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 10px;
}

.event-diff__footer button {
  padding: 6px 14px;
  border-radius: 7px;
  font: 650 10.5px/1 var(--mono, monospace);
  cursor: pointer;
  transition: filter .18s ease, transform .18s ease, box-shadow .18s ease;
}

.event-diff__footer button:hover {
  filter: brightness(1.06);
  transform: translateY(-1px);
}

.event-diff__accept {
  border: 1px solid #00a878;
  color: #fff;
  background: #00a878;
  box-shadow: 0 4px 14px rgba(0, 168, 120, .22);
}

.event-diff__reject {
  border: 1px solid #e8a0c4;
  color: #d41468;
  background: #fff;
}

.event-diff__reject:hover {
  box-shadow: 0 4px 14px rgba(225, 29, 116, .12);
}

.is-accepted,
.is-rejected {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  padding: 0 10px;
  border-radius: 6px;
  font: 750 9px/1 var(--mono, monospace);
  letter-spacing: .07em;
  text-transform: uppercase;
  animation: decision-in .34s cubic-bezier(.22, 1, .36, 1);
}

.is-accepted { color: #007b59; background: #dcfff4; }
.is-rejected { color: #bd145d; background: #ffe6f1; }

@keyframes decision-in { from { opacity: 0; transform: translateY(5px) scale(.9); } }

@media (prefers-reduced-motion: reduce) {
  .is-accepted, .is-rejected { animation: none; }
}
</style>
