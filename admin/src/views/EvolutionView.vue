<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const activeTab = ref('runs')
const error = ref('')
const success = ref('')

// ── Emotional Profiles ──────────────────────────────────────────
const profiles = ref([])
const loadingProfiles = ref(true)

// ── Evolution Logs ──────────────────────────────────────────────
const logs = ref([])
const loadingLogs = ref(true)
const logColumns = [
  { key: 'run_at', label: 'Run At' },
  { key: 'patterns_generated', label: 'Generated' },
  { key: 'patterns_learned', label: 'Learned' },
  { key: 'profiles_evolved', label: 'Evolved' },
]

// ── Mistakes ────────────────────────────────────────────────────
const mistakes = ref([])
const loadingMistakes = ref(true)

// ── Evolution Runs ──────────────────────────────────────────────
const runs = ref([])
const loadingRuns = ref(true)
const runColumns = [
  { key: 'id', label: 'ID' },
  { key: 'run_type', label: 'Type' },
  { key: 'status', label: 'Status' },
  { key: 'created_at', label: 'Created' },
  { key: 'completed_at', label: 'Completed' },
]

// ── Model Registry ──────────────────────────────────────────────
const models = ref([])
const loadingModels = ref(true)

// ── Drift ───────────────────────────────────────────────────────
const drift = ref(null)
const loadingDrift = ref(true)

// ── Quality Metrics ─────────────────────────────────────────────
const metrics = ref([])
const loadingMetrics = ref(true)
const metricColumns = [
  { key: 'metric_name', label: 'Metric' },
  { key: 'metric_value', label: 'Value' },
  { key: 'domain', label: 'Domain' },
  { key: 'run_type', label: 'Run Type' },
  { key: 'created_at', label: 'Recorded' },
]

// ── Analysis trigger ────────────────────────────────────────────
const analyzing = ref(false)

onMounted(() => {
  fetchRuns()
  fetchModels()
  fetchDrift()
  fetchMetrics()
  fetchProfiles()
  fetchLogs()
  fetchMistakes()
})

async function fetchRuns() {
  loadingRuns.value = true
  try {
    const data = await api.get('/admin/evolution/runs')
    runs.value = data.runs || []
  } catch (e) {
    error.value = e.message
  } finally {
    loadingRuns.value = false
  }
}

async function fetchModels() {
  loadingModels.value = true
  try {
    const data = await api.get('/admin/evolution/models')
    models.value = data.models || []
  } catch (e) {
    error.value = e.message
  } finally {
    loadingModels.value = false
  }
}

async function fetchDrift() {
  loadingDrift.value = true
  try {
    drift.value = await api.get('/admin/evolution/drift')
  } catch (e) {
    error.value = e.message
  } finally {
    loadingDrift.value = false
  }
}

async function fetchMetrics() {
  loadingMetrics.value = true
  try {
    const data = await api.get('/admin/evolution/metrics')
    metrics.value = data.metrics || []
  } catch (e) {
    error.value = e.message
  } finally {
    loadingMetrics.value = false
  }
}

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

async function triggerAnalysis() {
  analyzing.value = true
  error.value = ''
  success.value = ''
  try {
    const data = await api.post('/admin/evolution/analyze', { days: 7, min_interactions: 5 })
    success.value = `Analysis queued (run #${data.run_id})`
    fetchRuns()
  } catch (e) {
    error.value = e.message
  } finally {
    analyzing.value = false
  }
}

async function promoteModel(model) {
  error.value = ''
  success.value = ''
  try {
    await api.post(`/admin/evolution/models/${model.id}/promote`)
    model.status = 'active'
    model.promoted_at = new Date().toISOString()
    success.value = `Model "${model.model_name}" promoted`
  } catch (e) {
    error.value = e.message
  }
}

async function retireModel(model) {
  error.value = ''
  success.value = ''
  try {
    await api.post(`/admin/evolution/models/${model.id}/retire`)
    model.status = 'retired'
    success.value = `Model "${model.model_name}" retired`
  } catch (e) {
    error.value = e.message
  }
}

function rapportColor(score) {
  if (score >= 0.7) return '#42b883'
  if (score >= 0.4) return '#f0a500'
  return '#ff6b6b'
}

function driftColor(level) {
  if (level === 'low') return '#42b883'
  if (level === 'medium') return '#f0a500'
  return '#ff6b6b'
}

function statusBadge(status) {
  const map = { completed: 'badge--green', running: 'badge--blue', pending: 'badge--gray', failed: 'badge--red',
    active: 'badge--green', candidate: 'badge--blue', available: 'badge--gray', retired: 'badge--red' }
  return map[status] || 'badge--gray'
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">🧬 Evolution &amp; Learning</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <!-- Drift Indicator -->
    <div v-if="drift" class="drift-banner" :style="{ borderColor: driftColor(drift.drift_level) }">
      <span class="drift-label">Personality Drift:</span>
      <span class="drift-level" :style="{ color: driftColor(drift.drift_level) }">
        {{ drift.drift_level.toUpperCase() }}
      </span>
      <span class="drift-score">({{ (drift.drift_score * 100).toFixed(1) }}% · {{ drift.sample_count }} samples)</span>
    </div>

    <!-- Tabs -->
    <div class="tabs">
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'runs' }" @click="activeTab = 'runs'">Runs</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'models' }" @click="activeTab = 'models'">Models</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'metrics' }" @click="activeTab = 'metrics'">Metrics</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'profiles' }" @click="activeTab = 'profiles'">Profiles</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'logs' }" @click="activeTab = 'logs'">Logs</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'mistakes' }" @click="activeTab = 'mistakes'">Mistakes</button>
    </div>

    <!-- Evolution Runs -->
    <div v-if="activeTab === 'runs'" class="tab-content">
      <div class="section-header">
        <h3>Evolution Runs</h3>
        <button class="action-btn" :disabled="analyzing" @click="triggerAnalysis">
          {{ analyzing ? 'Analyzing…' : '▶ Run Analysis' }}
        </button>
      </div>
      <DataTable :columns="runColumns" :rows="runs" :loading="loadingRuns">
        <template #cell-status="{ value }">
          <span class="badge" :class="statusBadge(value)">{{ value }}</span>
        </template>
      </DataTable>
    </div>

    <!-- Model Registry -->
    <div v-if="activeTab === 'models'" class="tab-content">
      <div class="section">
        <h3>Model Registry</h3>
        <div v-if="loadingModels" class="loading-text">Loading models…</div>
        <table v-else class="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Type</th>
              <th>Status</th>
              <th>Eval</th>
              <th>Safety</th>
              <th>Personality</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!models.length">
              <td colspan="8" class="loading-text">No models registered</td>
            </tr>
            <tr v-for="m in models" :key="m.id">
              <td>{{ m.id }}</td>
              <td>{{ m.model_name }}</td>
              <td>{{ m.model_type }}</td>
              <td><span class="badge" :class="statusBadge(m.status)">{{ m.status }}</span></td>
              <td>{{ m.eval_score != null ? m.eval_score.toFixed(3) : '—' }}</td>
              <td>{{ m.safety_score != null ? m.safety_score.toFixed(3) : '—' }}</td>
              <td>{{ m.personality_score != null ? m.personality_score.toFixed(3) : '—' }}</td>
              <td class="action-cell">
                <button v-if="m.status !== 'active' && m.status !== 'retired'" class="sm-btn sm-btn--green" @click="promoteModel(m)">Promote</button>
                <button v-if="m.status !== 'retired'" class="sm-btn sm-btn--red" @click="retireModel(m)">Retire</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Quality Metrics -->
    <div v-if="activeTab === 'metrics'" class="tab-content">
      <div class="section">
        <h3>Quality Metrics</h3>
        <DataTable :columns="metricColumns" :rows="metrics" :loading="loadingMetrics">
          <template #cell-metric_value="{ value }">
            {{ typeof value === 'number' ? value.toFixed(4) : value }}
          </template>
        </DataTable>
      </div>
    </div>

    <!-- Emotional Profiles -->
    <div v-if="activeTab === 'profiles'" class="tab-content">
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
                    <div class="rapport-bar-fill" :style="{ width: ((p.rapport_score || 0) * 100) + '%', background: rapportColor(p.rapport_score || 0) }"></div>
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
    </div>

    <!-- Evolution Logs -->
    <div v-if="activeTab === 'logs'" class="tab-content">
      <div class="section">
        <h3>Evolution Logs</h3>
        <DataTable :columns="logColumns" :rows="logs" :loading="loadingLogs" />
      </div>
    </div>

    <!-- Mistakes -->
    <div v-if="activeTab === 'mistakes'" class="tab-content">
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
                <button class="toggle-btn" :class="{ 'toggle-on': m.resolved }" @click="toggleResolved(m)">
                  {{ m.resolved ? '✓ Resolved' : '✕ Unresolved' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
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
.drift-banner {
  background: #1a1a2e;
  border: 1px solid;
  border-radius: 8px;
  padding: 0.8rem 1.2rem;
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.drift-label { color: #aaa; font-size: 0.9rem; }
.drift-level { font-weight: 700; font-size: 1rem; }
.drift-score { color: #888; font-size: 0.8rem; }
.tabs {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
}
.tab-btn {
  background: #16162a;
  border: 1px solid #2a2a4a;
  color: #aaa;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
  transition: all 0.2s;
}
.tab-btn:hover { color: #eee; border-color: #646cff; }
.tab-btn--active { background: #646cff; color: #fff; border-color: #646cff; }
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
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}
.section-header h3 { margin: 0; color: #ccc; font-size: 1.1rem; }
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
.rapport-cell { display: flex; align-items: center; gap: 0.5rem; }
.rapport-bar-track { width: 80px; height: 8px; background: #16162a; border-radius: 4px; overflow: hidden; }
.rapport-bar-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
.rapport-value { font-size: 0.8rem; color: #ccc; min-width: 35px; }
.badge {
  display: inline-block;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
}
.badge--green { background: rgba(66,184,131,0.15); color: #42b883; }
.badge--blue { background: rgba(100,108,255,0.15); color: #646cff; }
.badge--gray { background: rgba(136,136,136,0.15); color: #888; }
.badge--red { background: rgba(255,107,107,0.15); color: #ff6b6b; }
.action-btn {
  background: #646cff;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1rem;
  cursor: pointer;
  font-size: 0.85rem;
  transition: opacity 0.2s;
}
.action-btn:hover { opacity: 0.85; }
.action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.action-cell { display: flex; gap: 0.4rem; }
.sm-btn {
  background: none;
  border: 1px solid #2a2a4a;
  border-radius: 4px;
  padding: 0.25rem 0.5rem;
  cursor: pointer;
  font-size: 0.75rem;
  transition: all 0.2s;
}
.sm-btn--green { color: #42b883; border-color: rgba(66,184,131,0.4); }
.sm-btn--green:hover { background: rgba(66,184,131,0.1); }
.sm-btn--red { color: #ff6b6b; border-color: rgba(255,107,107,0.4); }
.sm-btn--red:hover { background: rgba(255,107,107,0.1); }
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
