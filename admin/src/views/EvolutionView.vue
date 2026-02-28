<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const error = ref('')
const success = ref('')

// Emotional Profiles
const profiles = ref([])
const loadingProfiles = ref(true)

// Evolution Logs
const logs = ref([])
const loadingLogs = ref(true)
const logColumns = [
  { key: 'run_at', label: 'Run At' },
  { key: 'patterns_generated', label: 'Generated' },
  { key: 'patterns_learned', label: 'Learned' },
  { key: 'profiles_evolved', label: 'Evolved' },
]

// Mistakes
const mistakes = ref([])
const loadingMistakes = ref(true)

onMounted(() => {
  fetchProfiles()
  fetchLogs()
  fetchMistakes()
})

async function fetchProfiles() {
  loadingProfiles.value = true
  try {
    const data = await api.get('/admin/evolution/profiles')
    profiles.value = (data.profiles || data.items || data).map(p => ({
      ...p,
      top_topics: Array.isArray(p.top_topics) ? p.top_topics.join(', ') : p.top_topics || '—',
    }))
  } catch (e) {
    error.value = e.message
  } finally {
    loadingProfiles.value = false
  }
}

async function fetchLogs() {
  loadingLogs.value = true
  try {
    const data = await api.get('/admin/evolution/logs')
    logs.value = data.logs || data.items || data
  } catch (e) {
    error.value = e.message
  } finally {
    loadingLogs.value = false
  }
}

async function fetchMistakes() {
  loadingMistakes.value = true
  try {
    const data = await api.get('/admin/evolution/mistakes')
    mistakes.value = data.mistakes || data.items || data
  } catch (e) {
    error.value = e.message
  } finally {
    loadingMistakes.value = false
  }
}

async function toggleResolved(mistake) {
  error.value = ''
  success.value = ''
  try {
    await api.patch(`/admin/evolution/mistakes/${mistake.id}`, {
      resolved: !mistake.resolved,
    })
    mistake.resolved = !mistake.resolved
    success.value = 'Mistake updated'
  } catch (e) {
    error.value = e.message
  }
}

function rapportColor(score) {
  if (score >= 0.7) return '#42b883'
  if (score >= 0.4) return '#f0a500'
  return '#ff6b6b'
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Evolution &amp; Learning</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <div class="section">
      <h3>Emotional Profiles</h3>
      <div v-if="loadingProfiles" class="loading-text">Loading profiles…</div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>User ID</th>
            <th>Rapport</th>
            <th>Tone</th>
            <th>Interactions</th>
            <th>Top Topics</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!profiles.length">
            <td colspan="5" class="loading-text">No profiles</td>
          </tr>
          <tr v-for="p in profiles" :key="p.user_id">
            <td>{{ p.user_id }}</td>
            <td>
              <div class="rapport-cell">
                <div class="rapport-bar-track">
                  <div
                    class="rapport-bar-fill"
                    :style="{
                      width: ((p.rapport_score || 0) * 100) + '%',
                      background: rapportColor(p.rapport_score || 0),
                    }"
                  ></div>
                </div>
                <span class="rapport-value">{{ ((p.rapport_score || 0) * 100).toFixed(0) }}%</span>
              </div>
            </td>
            <td>{{ p.preferred_tone || '—' }}</td>
            <td>{{ p.interaction_count ?? '—' }}</td>
            <td>{{ p.top_topics }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="section">
      <h3>Evolution Logs</h3>
      <DataTable :columns="logColumns" :rows="logs" :loading="loadingLogs" />
    </div>

    <div class="section">
      <h3>Mistakes</h3>
      <div v-if="loadingMistakes" class="loading-text">Loading mistakes…</div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Claim</th>
            <th>Correction</th>
            <th>Category</th>
            <th>Resolved</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!mistakes.length">
            <td colspan="5" class="loading-text">No mistakes recorded</td>
          </tr>
          <tr v-for="m in mistakes" :key="m.id">
            <td>{{ m.id }}</td>
            <td>{{ m.claim_text }}</td>
            <td>{{ m.correction_text }}</td>
            <td>{{ m.category || '—' }}</td>
            <td>
              <button
                class="toggle-btn"
                :class="{ 'toggle-on': m.resolved }"
                @click="toggleResolved(m)"
              >
                {{ m.resolved ? '✓ Resolved' : '✕ Unresolved' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
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
.success-banner {
  background: rgba(66, 184, 131, 0.15);
  border: 1px solid rgba(66, 184, 131, 0.4);
  color: #42b883;
  padding: 0.8rem 1rem;
  border-radius: 8px;
  margin-bottom: 1rem;
}
.section {
  background: #1a1a2e;
  border-radius: 8px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}
.section h3 {
  margin: 0 0 1rem;
  color: #ccc;
  font-size: 1.1rem;
}
.loading-text {
  color: #888;
  text-align: center;
  padding: 2rem;
}
.table {
  width: 100%;
  border-collapse: collapse;
}
.table th, .table td {
  padding: 0.6rem 0.8rem;
  text-align: left;
  border-bottom: 1px solid #2a2a4a;
  font-size: 0.9rem;
}
.table th {
  color: #aaa;
  font-weight: 600;
  font-size: 0.8rem;
  text-transform: uppercase;
}
.rapport-cell {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.rapport-bar-track {
  width: 80px;
  height: 8px;
  background: #16162a;
  border-radius: 4px;
  overflow: hidden;
}
.rapport-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s;
}
.rapport-value {
  font-size: 0.8rem;
  color: #ccc;
  min-width: 35px;
}
.toggle-btn {
  background: none;
  border: 1px solid #2a2a4a;
  border-radius: 4px;
  padding: 0.3rem 0.6rem;
  cursor: pointer;
  font-size: 0.8rem;
  color: #ff6b6b;
  transition: all 0.2s;
}
.toggle-on {
  color: #42b883;
  border-color: rgba(66, 184, 131, 0.4);
  background: rgba(66, 184, 131, 0.1);
}
</style>
