<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { gsap } from 'gsap'

const t1 = ref(null)
const t2 = ref(null)
const cards = ref([])

function setCard(el, i) { if (el) cards.value[i] = el }

onMounted(() => nextTick(() => {
  gsap.fromTo(t1.value,     { autoAlpha: 0, y: 20 }, { autoAlpha: 1, y: 0, duration: 0.55, ease: 'power3.out' })
  gsap.fromTo(t2.value,     { autoAlpha: 0, y: 12 }, { autoAlpha: 1, y: 0, duration: 0.45, delay: 0.1, ease: 'power2.out' })
  gsap.fromTo(cards.value.filter(Boolean), { autoAlpha: 0, y: 16 }, { autoAlpha: 1, y: 0, duration: 0.4, stagger: 0.06, delay: 0.22, ease: 'power2.out' })
}))
</script>

<template>
  <div class="home">
    <h1 ref="t1" class="home__title">StratumCode</h1>
    <p ref="t2" class="home__lead">LLM provider 连接管理 — 一键配置、测试、浏览所有 API 端点。</p>

    <div class="home__grid">
      <div v-for="(card, i) in [
        { k: 'Providers', v: '--', d: '已配置' },
        { k: 'Models',   v: '--', d: '可用模型' },
        { k: 'Online',   v: '--', d: '连通率' }
      ]" :key="card.k" :ref="(el) => setCard(el, i)" class="home__stat">
        <span class="home__stat-v">{{ card.v }}</span>
        <span class="home__stat-k">{{ card.k }}</span>
        <span class="home__stat-d">{{ card.d }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home { padding: 56px 48px; max-width: 540px; }

.home__title {
  font-size: 26px; font-weight: 700;
  color: var(--text-h); margin: 0 0 6px;
  letter-spacing: -0.02em;
}
.home__lead {
  font-size: 14px; color: var(--text-muted);
  line-height: 1.6; margin: 0 0 36px;
  max-width: 380px;
}

.home__grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }

.home__stat {
  display: flex; flex-direction: column; gap: 3px;
  padding: 18px 16px;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-raised);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.home__stat:hover { border-color: var(--accent-border); box-shadow: var(--shadow); }
.home__stat-v {
  font-size: 24px; font-weight: 700; color: var(--text-h);
  font-family: var(--mono); letter-spacing: -0.03em;
}
.home__stat-k { font-size: 12px; font-weight: 600; color: var(--text-h); }
.home__stat-d { font-size: 11px; color: var(--text-muted); }
</style>
