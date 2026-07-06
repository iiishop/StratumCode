<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { gsap } from 'gsap'

const props = defineProps({
  text: { type: String, required: true },
  duration: { type: Number, default: 0.7 },
  stagger: { type: Number, default: 0.055 },
  characters: { type: String, default: 'STRATUMCODE01<>/{}' },
})

const displayed = ref(props.text)
let tween

function shuffle() {
  tween?.kill()
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    displayed.value = props.text
    return
  }

  const target = [...props.text]
  const state = { progress: 0 }
  const totalDuration = props.duration + Math.max(0, target.length - 1) * props.stagger

  tween = gsap.to(state, {
    progress: 1,
    duration: totalDuration,
    ease: 'power3.out',
    onUpdate() {
      const elapsed = state.progress * totalDuration
      displayed.value = target.map((character, index) => {
        const localProgress = Math.max(0, Math.min(1, (elapsed - index * props.stagger) / props.duration))
        if (character === ' ' || localProgress > 0.78) return character
        const randomIndex = Math.floor(Math.random() * props.characters.length)
        return props.characters[randomIndex]
      }).join('')
    },
    onComplete() {
      displayed.value = props.text
    },
  })
}

watch(() => props.text, shuffle)
onMounted(shuffle)
onUnmounted(() => tween?.kill())
</script>

<template>
  <span class="shuffle-text" :aria-label="text" @mouseenter="shuffle">
    <span aria-hidden="true">{{ displayed }}</span>
  </span>
</template>

<style scoped>
.shuffle-text {
  display: block;
  width: 100%;
  overflow: hidden;
  color: #fff;
  font: 750 17px/1 "IBM Plex Mono", "Cascadia Code", monospace;
  letter-spacing: -0.055em;
  text-align: left;
  text-overflow: clip;
  white-space: nowrap;
  text-shadow: 0 1px 0 rgba(255, 255, 255, .08);
}
</style>
