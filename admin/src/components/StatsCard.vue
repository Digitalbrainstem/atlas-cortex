<script setup>
defineProps({
  title: { type: String, required: true },
  value: { type: [String, Number], required: true },
  icon: { type: String, default: '📈' },
  color: { type: String, default: '#3b82f6' },
});
</script>

<template>
  <div class="stats-card">
    <div class="stats-icon" :style="{ backgroundColor: color + '20', color: color }">
      {{ icon }}
    </div>
    <div class="stats-info">
      <span class="stats-title">{{ title }}</span>
      <span class="stats-value">{{ value }}</span>
    </div>
  </div>
</template>

<style scoped>
.stats-card {
  display: flex;
  align-items: center;
  gap: 1rem;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 1.25rem;
  box-shadow: var(--shadow);
}

.stats-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: var(--radius);
  font-size: 1.4rem;
  flex-shrink: 0;
}

.stats-info {
  display: flex;
  flex-direction: column;
}

.stats-title {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.stats-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
}
</style>
