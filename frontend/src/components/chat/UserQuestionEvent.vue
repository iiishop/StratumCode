<script setup>
import EventFrame from './EventFrame.vue'

defineProps({ event: { type: Object, required: true } })
const emit = defineEmits(['answer'])
</script>

<template>
  <EventFrame
    kind="user-question"
    state="ask_user"
    symbol="?"
    label="User decision"
    :detail="event.question"
    status="needs input"
  >
    <div class="user-question">
      <p v-if="event.reason && event.reason !== event.question">{{ event.reason }}</p>
      <div class="user-question__options">
        <button
          v-for="option in event.options || []"
          :key="option.id || option.label"
          type="button"
          :class="{ 'is-recommended': option.recommended }"
          @click="emit('answer', {
            question_id: event.id,
            analysis_id: event.analysis_id,
            unknown_id: event.unknown_id,
            linked_unknown_id: event.linked_unknown?.id,
            blocks_next_step: event.blocks_next_step,
            origin_message: event.origin_message,
            question: event.question,
            selected_option_id: option.id,
            selected_option_label: option.label,
            response: option.value,
            send: true,
          })"
        >
          <strong>{{ option.label }}</strong>
          <small v-if="option.recommended">recommended</small>
        </button>
        <button
          v-if="event.custom_allowed"
          type="button"
          @click="emit('answer', {
            question_id: event.id,
            analysis_id: event.analysis_id,
            unknown_id: event.unknown_id,
            linked_unknown_id: event.linked_unknown?.id,
            blocks_next_step: event.blocks_next_step,
            origin_message: event.origin_message,
            question: event.question,
            response: `Question: ${event.question}\nMy answer: `,
            send: false,
          })"
        >
          <strong>Custom reply</strong>
        </button>
      </div>
      <div class="user-question__links">
        <div v-if="event.why_it_matters" class="user-question__link-row">
          <span>Why</span>
          <p>{{ event.why_it_matters }}</p>
        </div>
        <div v-if="event.linked_unknown?.id" class="user-question__link-row">
          <span>Unknown</span>
          <p>
            {{ event.linked_unknown.id }}
            <small v-if="event.linked_unknown.resolution_strategy">
              {{ event.linked_unknown.resolution_strategy }}
            </small>
            {{ event.linked_unknown.question }}
          </p>
        </div>
        <div v-if="event.related_beliefs?.length" class="user-question__link-row">
          <span>Beliefs</span>
          <ul>
            <li v-for="belief in event.related_beliefs" :key="belief.id || belief.statement">
              <small v-if="belief.status">{{ belief.status }}</small>
              {{ belief.statement }}
            </li>
          </ul>
        </div>
        <div v-if="event.related_open_questions?.length" class="user-question__link-row">
          <span>Open</span>
          <ul>
            <li v-for="item in event.related_open_questions" :key="item">{{ item }}</li>
          </ul>
        </div>
        <div v-if="event.patch_planning_context_refs?.length" class="user-question__link-row">
          <span>Patch</span>
          <ul>
            <li v-for="item in event.patch_planning_context_refs" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>
    </div>
  </EventFrame>
</template>

<style scoped>
.user-question {
  display: grid;
  gap: 10px;
}
.user-question p {
  margin: 0;
  color: #566d88;
  font-size: 11px;
  line-height: 1.5;
}
.user-question__options {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.user-question__options button {
  display: grid;
  gap: 3px;
  min-height: 34px;
  padding: 7px 9px;
  border: 1px solid #d8e2ef;
  border-radius: 7px;
  color: #29445f;
  background: #fff;
  text-align: left;
  cursor: pointer;
}
.user-question__options button:hover,
.user-question__options button.is-recommended {
  border-color: #8fb0e8;
  background: #f3f7ff;
}
.user-question__options strong {
  font-size: 10px;
}
.user-question__options small {
  color: #1756d1;
  font: 700 8px/1 var(--mono);
  text-transform: uppercase;
}
.user-question__links {
  display: grid;
  gap: 7px;
  padding-top: 2px;
}
.user-question__link-row {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
}
.user-question__link-row > span {
  color: #6f7f91;
  font: 700 8px/1.4 var(--mono);
  text-transform: uppercase;
}
.user-question__link-row p,
.user-question__link-row ul {
  margin: 0;
  min-width: 0;
  color: #344f69;
  font-size: 10px;
  line-height: 1.45;
}
.user-question__link-row ul {
  display: grid;
  gap: 4px;
  padding: 0;
  list-style: none;
}
.user-question__link-row small {
  margin-right: 5px;
  color: #6b7890;
  font: 700 8px/1 var(--mono);
  text-transform: uppercase;
}
</style>
