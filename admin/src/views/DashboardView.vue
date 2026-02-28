<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import StatsCard from '../components/StatsCard.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const loading = ref(true)
const error = ref('')
const stats = ref({})
const recentSafety = ref([])
const recentInteractions = ref([])
const layerDistribution = ref({ instant: 0, tool: 0, llm: 0 })

const safetyColumns = [
  { key: 'id', label: 'ID' },
  { key: 'category', label: 'Category' },
  { key: 'severity', label: 'Severity' },
  { key: 'message', label: 'Message' },
  { key: 'created_at', label: 'Time' },
]

const interactionColumns = [
  { key: 'id', label: 'ID' },
  { key: 'user_id', label: 'User' },
  { key: 'layer', label: 'Layer' },
  { key: 'intent', label: 'Intent' },
  { key: 'created_at', label: 'Time' },
]

onMounted(async () => {
  try {
    const data = await api.get('/admin/dashboard')
    stats.value = data.stats || data
    recentSafety.value = data.recent_safety_events || []
    recentInteractions.value = data.recent_interactions || []
    layerDistribution.value = data.layer_distribution || { instant: 0, tool: 0, llm: 0 }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

function layerMax() {
  const d = layerDistribution.value
  return Math.max(d.instant, d.tool, d.llm, 1)
}

function layerPct(val) {
  return (val / layerMax()) * 100
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Dashboard</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading" class="loading-text">Loading dashboard…</div>
    <template v-else>
      <div class="stats-grid">
        <StatsCard title="Total Users" :value="stats.total_users ?? 0" icon="👥" color="#646cff" />
        <StatsCard title="Interactions" :value="stats.interactions ?? 0" icon="💬" color="#42b883" />
        <StatsCard title="Safety Events" :value="stats.safety_events ?? 0" icon="⚠️" color="#ff6b6b" />
        <StatsCard title="Devices" :value="stats.devices ?? 0" icon="📱" color="#f0a500" />
        <StatsCard title="Voice Enrollments" :value="stats.voice_enrollments ?? 0" icon="🎤" color="#a855f7" />
        <StatsCard title="Command Patterns" :value="stats.command_patterns ?? 0" icon="🔄" color="#06b6d4" />
      </div>

      <div class="dashboard-section">
        <h3>Layer Distribution</h3>
        <div class="layer-bars">
          <div class="layer-bar-row" v-for="layer in ['instant', 'tool', 'llm']" :key="layer">
            <span class="layer-label">{{ layer }}</span>
            <div class="layer-bar-track">
              <div
                class="layer-bar-fill"
                :class="'layer-' + layer"
                :style="{ width: layerPct(layerDistribution[layer]) + '%' }"
              ></div>
            </div>
            <span class="layer-count">{{ layerDistribution[layer] }}</span>
          </div>
        </div>
      </div>

      <div class="dashboard-tables">
        <div class="dashboard-section">
          <h3>Recent Safety Events</h3>
          <DataTable :columns="safetyColumns" :rows="recentSafety" :loading="false" />
        </div>
        <div class="dashboard-section">
          <h3>Recent Interactions</h3>
          <DataTable :columns="interactionColumns" :rows="recentInteractions" :loading="false" />
        </div>
      </div>
    </template>
  </AppLayout>
</template>

<style scoped>
.page-title {
  margin: 0 0 1.5rem;
  font-size: 1.5rem;
  color: #eee;
}
.error-banner {
  background: rgba(220, 50, 50, 0.15);
  border: 1px solid rgba(220, 50, 50, 0.4);
  color: #ff6b6b;
  padding: 0.8rem 1rem;
  border-radius: 8px;
  margin-bottom: 1rem;
}
.loading-text {
  color: #888;
  text-align: center;
  padding: 3rem;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}
.dashboard-section {
  background: #1a1a2e;
  border-radius: 8px;
  padding: 1.2rem;
  margin-bottom: 1.5rem;
}
.dashboard-section h3 {
  margin: 0 0 1rem;
  font-size: 1rem;
  color: #ccc;
}
.dashboard-tables {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}
@media (max-width: 900px) {
  .dashboard-tables {
    grid-template-columns: 1fr;
  }
}
.layer-bars {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.layer-bar-row {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}
.layer-label {
  width: 60px;
  font-size: 0.85rem;
  color: #aaa;
  text-transform: capitalize;
}
.layer-bar-track {
  flex: 1;
  background: #16162a;
  border-radius: 4px;
  height: 20px;
  overflow: hidden;
}
.layer-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.4s ease;
}
.layer-instant { background: #646cff; }
.layer-tool { background: #42b883; }
.layer-llm { background: #f0a500; }
.layer-count {
  width: 50px;
  text-align: right;
  font-size: 0.85rem;
  color: #ccc;
  font-weight: 600;
}
</style>
