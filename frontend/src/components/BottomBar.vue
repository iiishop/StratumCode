<script setup>
import UpdateStatus from './updates/UpdateStatus.vue'
</script>

<template>
  <footer class="bottom-bar" aria-label="Application status">
    <div class="bottom-bar__rail">
      <UpdateStatus />
    </div>
  </footer>
</template>

<style scoped>
.bottom-bar {
  display: flex;
  min-height: 36px;
  flex: 0 0 36px;
  align-items: center;
  border-top: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.94);
}

.bottom-bar__rail {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: flex-end;
  padding: 0 16px;
}
</style>
