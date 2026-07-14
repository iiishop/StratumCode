<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { gsap } from 'gsap'
import { animate, stagger as animeStagger } from 'animejs'
import EventFrame from './EventFrame.vue'

const props = defineProps({ event: { type: Object, required: true } })
const emit = defineEmits(['answer'])
const customText = ref('')
const showContext = ref(false)
const submitted = ref(false)
const selectedLabel = ref('')
const selectedOptionId = ref('')
const rootRef = ref(null)
const optionsRef = ref(null)

watch(
  () => [
    props.event.answer_status,
    props.event.selected_option_id,
    props.event.selected_option_label,
    props.event.response,
  ],
  () => {
    const answered = props.event.answer_status === 'submitted'
    submitted.value = answered
    selectedOptionId.value = answered ? (props.event.selected_option_id || '') : ''
    selectedLabel.value = answered ? (props.event.selected_option_label || props.event.response || '') : ''
  },
  { immediate: true },
)

function onContextEnter(el, done) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { done(); return }
  gsap.fromTo(el, { height: 0, autoAlpha: 0 }, { height: el.scrollHeight, autoAlpha: 1, duration: 0.28, ease: 'power2.out', onComplete: () => { gsap.set(el, { height: 'auto' }); done() } })
}

function onContextLeave(el, done) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { done(); return }
  gsap.to(el, { height: 0, autoAlpha: 0, duration: 0.2, ease: 'power2.in', onComplete: done })
}

function submitOption(option) {
  if (submitted.value) return
  selectedLabel.value = option.label
  selectedOptionId.value = option.id
  submitted.value = true
  animateSubmit(() => {
    emit('answer', {
      question_id: props.event.id,
      checkpoint_id: props.event.checkpoint_id,
      analysis_id: props.event.analysis_id,
      unknown_id: props.event.unknown_id,
      linked_unknown_id: props.event.linked_unknown?.id,
      blocks_next_step: props.event.blocks_next_step,
      origin_message: props.event.origin_message,
      resume_state: props.event.resume_state,
      checkpoint_phase: props.event.checkpoint_phase,
      analysis: props.event.analysis,
      patch_plan: props.event.patch_plan,
      design_plan: props.event.design_plan,
      investigation: props.event.investigation,
      validation_result: props.event.validation_result,
      changed_files: props.event.changed_files,
      question: props.event.question,
      selected_option_id: option.id,
      selected_option_label: option.label,
      response: option.value || option.label,
      send: true,
    })
  })
}

function submitCustom() {
  const text = customText.value.trim()
  if (!text || submitted.value) return
  selectedLabel.value = text
  submitted.value = true
  animateSubmit(() => {
    emit('answer', {
      question_id: props.event.id,
      checkpoint_id: props.event.checkpoint_id,
      analysis_id: props.event.analysis_id,
      unknown_id: props.event.unknown_id,
      linked_unknown_id: props.event.linked_unknown?.id,
      blocks_next_step: props.event.blocks_next_step,
      origin_message: props.event.origin_message,
      resume_state: props.event.resume_state,
      checkpoint_phase: props.event.checkpoint_phase,
      analysis: props.event.analysis,
      patch_plan: props.event.patch_plan,
      design_plan: props.event.design_plan,
      investigation: props.event.investigation,
      validation_result: props.event.validation_result,
      changed_files: props.event.changed_files,
      question: props.event.question,
      response: text,
      custom: true,
      send: true,
    })
  })
}

async function animateSubmit(onDone) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    onDone()
    return
  }
  await nextTick()

  const buttons = optionsRef.value?.querySelectorAll('.uq__option:not(.is-selected)')
  if (buttons?.length) {
    animate(buttons, {
      translateY: [0, -8],
      opacity: [1, 0],
      scale: [1, 0.95],
      delay: animeStagger(35),
      duration: 280,
      ease: 'inCubic',
    })
  }

  const custom = optionsRef.value?.querySelector('.uq__custom')
  if (custom) {
    animate(custom, {
      translateY: [0, -6],
      opacity: [1, 0],
      duration: 220,
      ease: 'inCubic',
    })
  }

  const question = rootRef.value?.querySelector('.uq__question')
  if (question) {
    animate(question, {
      translateY: [0, -6],
      opacity: [1, 0],
      duration: 240,
      ease: 'inCubic',
    })
  }

  const contextToggle = rootRef.value?.querySelector('.uq__context-toggle')
  if (contextToggle) {
    animate(contextToggle, {
      opacity: [1, 0],
      duration: 180,
      ease: 'inCubic',
    })
  }

  const contextEl = rootRef.value?.querySelector('.uq__context')
  if (contextEl) {
    animate(contextEl, {
      opacity: [1, 0],
      duration: 180,
      ease: 'inCubic',
    })
  }

  await new Promise(r => setTimeout(r, 320))

  const options = rootRef.value?.querySelector('.uq__options')
  if (options) {
    gsap.to(options, {
      height: 0,
      paddingTop: 0,
      paddingBottom: 0,
      marginTop: 0,
      marginBottom: 0,
      gap: 0,
      opacity: 0,
      duration: 0.35,
      ease: 'power3.in',
    })
  }

  const selected = optionsRef.value?.querySelector('.uq__option.is-selected')
  if (selected) {
    gsap.to(selected, {
      paddingTop: 0,
      paddingBottom: 0,
      height: 0,
      borderWidth: 0,
      opacity: 0,
      duration: 0.35,
      ease: 'power3.in',
    })
  }

  await new Promise(r => setTimeout(r, 350))
  onDone()
}

const hasContext = computed(() =>
  (props.event.why_it_matters && props.event.why_it_matters !== props.event.question)
  || props.event.linked_unknown?.id
  || props.event.related_beliefs?.length
  || props.event.related_open_questions?.length
  || props.event.patch_planning_context_refs?.length
)
</script>

<template>
  <div ref="rootRef" class="uq-root">
    <EventFrame
      kind="user-question"
      state="ask_user"
      symbol="?"
      :label="submitted && selectedLabel ? 'Answered' : 'Needs your input'"
      :detail="submitted && selectedLabel ? selectedLabel : event.question"
      :status="submitted ? 'done' : 'needs input'"
    >
      <div class="uq">
        <div v-show="!submitted" class="uq__question">
          <p>{{ event.question }}</p>
          <small v-if="event.reason && event.reason !== event.question">{{ event.reason }}</small>
        </div>

        <div ref="optionsRef" class="uq__options">
          <button
            v-for="option in event.options || []"
            :key="option.id || option.label"
            type="button"
            class="uq__option"
            :class="{ 'is-recommended': option.recommended, 'is-selected': submitted && selectedOptionId === option.id }"
            @click="submitOption(option)"
          >
            <span class="uq__option-radio">
              <i v-if="option.recommended">&#9733;</i>
            </span>
            <span class="uq__option-body">
              <strong>{{ option.label }}</strong>
              <small v-if="option.value !== option.label">{{ option.value }}</small>
            </span>
          </button>

          <div v-if="event.custom_allowed" class="uq__custom">
            <textarea
              v-model="customText"
              rows="2"
              placeholder="Or write your own answer..."
              @keydown.enter.exact.prevent="submitCustom"
            ></textarea>
            <button type="button" class="uq__custom-send" :disabled="!customText.trim()" @click="submitCustom">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
            </button>
          </div>
        </div>

        <div v-if="submitted && selectedLabel" class="uq__answered">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M20 6 9 17l-5-5"/></svg>
          <div class="uq__answered-body">
            <p>{{ event.question }}</p>
            <svg class="uq__answered-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
            <strong>{{ selectedLabel }}</strong>
          </div>
        </div>

        <button v-if="hasContext && !submitted" type="button" class="uq__context-toggle" :class="{ 'is-open': showContext }" @click="showContext = !showContext">
          {{ showContext ? 'Hide context' : 'Show context' }}
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
        </button>

        <Transition name="uq-expand" @enter="onContextEnter" @leave="onContextLeave">
          <div v-if="showContext && hasContext && !submitted" class="uq__context">
          <div v-if="event.why_it_matters && event.why_it_matters !== event.question" class="uq__context-item">
            <span>Why</span>
            <p>{{ event.why_it_matters }}</p>
          </div>
          <div v-if="event.linked_unknown?.id" class="uq__context-item">
            <span>Unknown</span>
            <p>{{ event.linked_unknown.id }} {{ event.linked_unknown.question }}</p>
          </div>
          <div v-if="event.related_beliefs?.length" class="uq__context-item">
            <span>Beliefs</span>
            <ul>
              <li v-for="belief in event.related_beliefs" :key="belief.id || belief.statement">
                <b>{{ belief.status }}</b> {{ belief.statement }}
              </li>
            </ul>
          </div>
          <div v-if="event.related_open_questions?.length" class="uq__context-item">
            <span>Open</span>
            <ul>
              <li v-for="item in event.related_open_questions" :key="item">{{ item }}</li>
            </ul>
          </div>
        </div>
        </Transition>
      </div>
    </EventFrame>
  </div>
</template>

<style scoped>
.uq-root {
  overflow: hidden;
}

.uq {
  display: grid;
  gap: 12px;
}

.uq__question {
  display: grid;
  gap: 4px;
}

.uq__question p {
  margin: 0;
  color: var(--text-h, #102a5c);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.5;
}

.uq__question small {
  color: var(--text-muted, #71809c);
  font-size: 10.5px;
  line-height: 1.45;
}

.uq__answered {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 4px 0;
}

.uq__answered > svg:first-child {
  flex-shrink: 0;
  margin-top: 3px;
  color: var(--ok, #11866f);
}

.uq__answered-body {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.uq__answered-body p {
  margin: 0;
  color: var(--text-h, #102a5c);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.45;
}

.uq__answered-arrow {
  color: var(--accent, #1756d1);
  margin-left: 2px;
}

.uq__answered-body strong {
  color: var(--ok, #11866f);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
}

.uq__options {
  display: grid;
  gap: 6px;
}

.uq__option {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 11px 12px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 9px;
  color: var(--text-h, #102a5c);
  background: var(--bg-raised, #ffffff);
  text-align: left;
  cursor: pointer;
  transition: border-color .14s, background .14s, transform .1s, box-shadow .14s;
}

.uq__option:hover {
  border-color: var(--accent-border, rgba(23,86,209,.32));
  background: var(--accent-bg, rgba(23,86,209,.06));
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(23,86,209,.1);
}

.uq__option:active {
  transform: translateY(0);
}

.uq__option.is-recommended {
  border-color: var(--accent-border, rgba(23,86,209,.4));
  background: linear-gradient(135deg, rgba(23,86,209,.07), rgba(23,86,209,.025));
  box-shadow: 0 1px 6px rgba(23,86,209,.08);
}

.uq__option.is-recommended:hover {
  border-color: var(--accent, #1756d1);
  box-shadow: 0 3px 14px rgba(23,86,209,.16);
}

.uq__option-radio {
  display: grid;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  place-items: center;
  margin-top: 1px;
  border: 2px solid var(--border, #d9e3f5);
  border-radius: 50%;
  color: var(--text-muted, #71809c);
  font-size: 11px;
  transition: border-color .14s, background .14s;
}

.uq__option:hover .uq__option-radio {
  border-color: var(--accent-border, rgba(23,86,209,.32));
}

.uq__option.is-recommended .uq__option-radio {
  border-color: var(--accent, #1756d1);
  background: var(--accent, #1756d1);
  color: #fff;
  font-size: 10px;
}

.uq__option-body {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.uq__option-body strong {
  color: var(--text-h, #102a5c);
  font-size: 11.5px;
  font-weight: 600;
  line-height: 1.35;
}

.uq__option-body small {
  color: var(--text-muted, #71809c);
  font-size: 10px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.uq__custom {
  display: flex;
  gap: 6px;
  align-items: flex-end;
}

.uq__custom textarea {
  flex: 1;
  min-height: 38px;
  max-height: 80px;
  padding: 8px 10px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 8px;
  color: var(--text-h, #102a5c);
  background: var(--bg-raised, #ffffff);
  font: 11px/1.5 var(--sans);
  resize: none;
  outline: none;
  transition: border-color .14s;
}

.uq__custom textarea:focus {
  border-color: var(--accent-border, rgba(23,86,209,.32));
  box-shadow: 0 0 0 3px var(--accent-bg, rgba(23,86,209,.08));
}

.uq__custom textarea::placeholder {
  color: var(--text-muted, #71809c);
}

.uq__custom-send {
  display: grid;
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  place-items: center;
  border: 1px solid var(--accent, #1756d1);
  border-radius: 8px;
  color: #fff;
  background: var(--accent, #1756d1);
  cursor: pointer;
  transition: background .14s, transform .1s;
}

.uq__custom-send:hover {
  background: var(--accent-hover, #0f43ad);
}

.uq__custom-send:active {
  transform: scale(.95);
}

.uq__custom-send:disabled {
  opacity: .35;
  cursor: default;
  transform: none;
}

.uq__context-toggle {
  display: flex;
  align-items: center;
  gap: 5px;
  justify-self: start;
  padding: 4px 0;
  border: 0;
  color: var(--text-muted, #71809c);
  background: transparent;
  font: 700 9px/1 var(--mono);
  text-transform: uppercase;
  cursor: pointer;
  transition: color .12s;
}

.uq__context-toggle:hover {
  color: var(--accent-text, #1748a3);
}

.uq__context-toggle svg {
  transition: transform .2s ease;
}

.uq__context-toggle.is-open svg {
  transform: rotate(180deg);
}

.uq__context {
  display: grid;
  gap: 7px;
  padding: 10px;
  border: 1px solid var(--border, #d9e3f5);
  border-radius: 8px;
  background: var(--code-bg, #f1f4f8);
}

.uq__context-item {
  display: grid;
  gap: 3px;
}

.uq__context-item > span {
  color: var(--text-muted, #71809c);
  font: 700 8px/1 var(--mono);
  text-transform: uppercase;
}

.uq__context-item p {
  margin: 0;
  color: var(--text, #3f5274);
  font-size: 10.5px;
  line-height: 1.45;
}

.uq__context-item ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 3px;
}

.uq__context-item li {
  color: var(--text, #3f5274);
  font-size: 10px;
  line-height: 1.4;
}

.uq__context-item li b {
  font: 700 8px/1 var(--mono);
  text-transform: uppercase;
  margin-right: 4px;
  color: var(--accent-text, #1748a3);
}

@media (prefers-reduced-motion: reduce) {
  .uq__option,
  .uq__custom-send,
  .uq__custom textarea,
  .uq__context-toggle svg {
    transition: none;
  }
}

.uq-expand-enter-active,
.uq-expand-leave-active {
  overflow: hidden;
}
</style>
