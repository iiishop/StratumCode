<script setup>
defineProps({ items: { type: Array, default: () => [] } })
</script>

<template>
  <section v-if="items.length" class="follow-ups">
    <h4>Follow-up verification <span>{{ items.length }}</span></h4>
    <p class="scope-note">Outside this investigation. Not executed or counted as verified.</p>
    <article v-for="item in items" :key="item.id">
      <strong>{{ item.question }}</strong>
      <p>{{ item.reason }}</p>
      <p v-if="item.required_capability">Required: {{ item.required_capability }}</p>
      <small v-if="item.source_unknown_id || item.parent_id">
        Source: {{ item.source_unknown_id || item.id }}<template v-if="item.parent_id"> / {{ item.parent_id }}</template>
      </small>
    </article>
  </section>
</template>

<style scoped>
.follow-ups { margin: 12px 0; padding: 12px; border: 1px solid #c9ced8; border-radius: 8px; }
h4 { margin: 0; font-size: 12px; }
h4 span { font-weight: 400; opacity: .65; }
.scope-note, p, small { font-size: 11px; line-height: 1.5; opacity: .75; }
article { border-top: 1px solid #c9ced866; padding-top: 10px; margin-top: 10px; overflow-wrap: anywhere; }
strong { font-size: 12px; font-weight: 500; }
p { margin: 5px 0; }
</style>
