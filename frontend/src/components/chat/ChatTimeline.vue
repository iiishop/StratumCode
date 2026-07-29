<script setup>
import { computed, reactive } from 'vue'
import { groupChatEvents } from '../../lib/chatTimeline'
import ChatEvent from './ChatEvent.vue'

const props = defineProps({
  events: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
})
defineEmits(['answer'])

const expandedGroups = reactive({})
const segments = computed(() => groupChatEvents(props.events))

function isActiveGroup(segment) {
  return props.running && segment.endIndex === props.events.length - 1
}

function isGroupOpen(segment) {
  if (typeof expandedGroups[segment.key] === 'boolean') return expandedGroups[segment.key]
  return isActiveGroup(segment)
}

function toggleGroup(segment) {
  expandedGroups[segment.key] = !isGroupOpen(segment)
}

function eventLabel(count) {
  return `${count} ${count === 1 ? 'event' : 'events'}`
}
</script>

<template>
  <TransitionGroup name="timeline-segment" tag="div" class="chat-timeline">
    <div v-for="segment in segments" :key="segment.key" class="chat-timeline__segment">
      <ChatEvent
        v-if="segment.kind === 'primary'"
        :event="segment.event"
        :events="events"
        @answer="$emit('answer', $event)"
      />

      <section v-else class="process-group" :class="{ 'is-open': isGroupOpen(segment), 'is-active': isActiveGroup(segment) }">
        <button
          type="button"
          class="process-group__toggle"
          :aria-expanded="isGroupOpen(segment)"
          @click="toggleGroup(segment)"
        >
          <span class="process-group__icon" aria-hidden="true">
            <i></i><i></i><i></i>
          </span>
          <span class="process-group__title">{{ isActiveGroup(segment) ? 'Working' : 'Process details' }}</span>
          <span class="process-group__count">{{ eventLabel(segment.events.length) }}</span>
          <svg class="process-group__chevron" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="m6 8 4 4 4-4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>

        <Transition name="process-expand">
          <div v-show="isGroupOpen(segment)" class="process-group__content">
            <TransitionGroup name="timeline-event" tag="div" class="process-group__events">
              <ChatEvent
                v-for="event in segment.events"
                :key="event.id"
                :event="event"
                :events="events"
                @answer="$emit('answer', $event)"
              />
            </TransitionGroup>
          </div>
        </Transition>
      </section>
    </div>
  </TransitionGroup>
</template>

<style scoped>
.chat-timeline {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chat-timeline__segment { min-width: 0; }

.process-group {
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
  border-radius: 10px;
  background: color-mix(in srgb, var(--bg) 74%, transparent);
  transition: border-color .16s ease, background .16s ease;
}

.process-group.is-open {
  border-color: color-mix(in srgb, var(--accent) 18%, var(--border));
  background: color-mix(in srgb, var(--accent) 1.5%, var(--bg));
}

.process-group__toggle {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 38px;
  padding: 8px 10px;
  border: 0;
  color: var(--text-muted);
  background: transparent;
  font-family: var(--sans);
  text-align: left;
  cursor: pointer;
}

.process-group__toggle:hover {
  color: var(--text);
  background: color-mix(in srgb, var(--accent) 3%, transparent);
}

.process-group__toggle:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent) 55%, transparent);
  outline-offset: -2px;
}

.process-group__icon {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  width: 20px;
  margin-right: 7px;
}

.process-group__icon i {
  width: 3px;
  height: 3px;
  border-radius: 999px;
  background: currentColor;
  opacity: .58;
}

.process-group.is-active .process-group__icon i {
  animation: process-pulse 1.15s ease-in-out infinite;
}

.process-group.is-active .process-group__icon i:nth-child(2) { animation-delay: .14s; }
.process-group.is-active .process-group__icon i:nth-child(3) { animation-delay: .28s; }

.process-group__title {
  color: var(--text);
  font-size: 11.5px;
  font-weight: 600;
}

.process-group__count {
  margin-left: 7px;
  font: 10px/1 var(--mono);
  color: var(--text-muted);
}

.process-group__chevron {
  width: 16px;
  height: 16px;
  margin-left: auto;
  opacity: .62;
  transition: transform .18s ease;
}

.process-group.is-open .process-group__chevron { transform: rotate(180deg); }

.process-group__content {
  overflow: hidden;
  border-top: 1px solid color-mix(in srgb, var(--border) 75%, transparent);
}

.process-group__events {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 10px 10px 8px;
}

.process-expand-enter-active,
.process-expand-leave-active {
  transition: opacity .18s ease, transform .18s cubic-bezier(.22, 1, .36, 1);
}

.process-expand-enter-from,
.process-expand-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.timeline-segment-enter-active,
.timeline-event-enter-active {
  transition: opacity .22s ease, transform .22s cubic-bezier(.22, 1, .36, 1);
}

.timeline-segment-enter-from,
.timeline-event-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

@keyframes process-pulse {
  0%, 60%, 100% { opacity: .28; transform: translateY(0); }
  30% { opacity: .9; transform: translateY(-2px); }
}

@media (prefers-reduced-motion: reduce) {
  .process-group,
  .process-group__chevron,
  .process-expand-enter-active,
  .process-expand-leave-active,
  .timeline-segment-enter-active,
  .timeline-event-enter-active {
    transition-duration: .01ms;
  }

  .process-group.is-active .process-group__icon i { animation: none; }
}
</style>
