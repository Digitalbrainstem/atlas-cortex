<script setup>
import NavBar from './NavBar.vue';
</script>

<template>
  <div class="app-layout">
    <NavBar />
    <main class="main-content">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
}

.main-content {
  flex: 1;
  padding: 2rem;
  overflow-y: auto;
  min-width: 0;
}

@media (max-width: 768px) {
  .main-content {
    padding: 3.5rem 1rem 1rem;
  }
}
</style>
