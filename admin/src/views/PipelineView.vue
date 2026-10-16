<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const activeTab = ref('models')
const error = ref('')

// Models
const modelState = ref(null)
const loadingModels = ref(true)

// Routing
const routing = ref(null)
const loadingRouting = ref(true)
const routingColumns = [
  { key: 'id', label: 'ID' },
  { key: 'message', label: 'Message' },
  { key: 'matched_layer', label: 'Layer' },
  { key: 'llm_model', label: 'Model' },
  { key: 'response_time_ms', label: 'Latency (ms)' },
  { key: 'created_at', label: 'Time' },
]

// Thinking
const thinkingStats = ref(null)
const loadingThinking = ref(true)

// Queue
const queueState = ref(null)
const loadingQueue = ref(true)

// Crash handler
const crashState = ref(null)
const loadingCrash = ref(true)

// Pipeline stats
const pipelineStats = ref(null)
const loadingStats = ref(true)

async function fetchModels() {
  loadingModels.value = true
  try { modelState.value = await api.get('/admin/pipeline/models') }
  catch (e) { error.value = e.message }
  finally { loadingModels.value = false }
}

async function fetchRouting() {
  loadingRouting.value = true
  try { routing.value = await api.get('/admin/pipeline/routing') }
  catch (e) { error.value = e.message }
  finally { loadingRouting.value = false }
}

async function fetchThinking() {
  loadingThinking.value = true
  try { thinkingStats.value = await api.get('/admin/pipeline/thinking') }
  catch (e) { error.value = e.message }
  finally { loadingThinking.value = false }
}

async function fetchQueue() {
  loadingQueue.value = true
  try { queueState.value = await api.get('/admin/pipeline/queue') }
  catch (e) { error.value = e.message }
  finally { loadingQueue.value = false }
}

async function fetchCrash() {
  loadingCrash.value = true
  try { crashState.value = await api.get('/admin/pipeline/crash-handler') }
  catch (e) { error.value = e.message }
  finally { loadingCrash.value = false }
}

async function fetchStats() {
  loadingStats.value = true
  try { pipelineStats.value = await api.get('/admin/pipeline/stats') }
  catch (e) { error.value = e.message }
  finally { loadingStats.value = false }
}

onMounted(() => {
  fetchModels()
  fetchRouting()
  fetchThinking()
  fetchQueue()
  fetchCrash()
  fetchStats()
})
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Pipeline</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="tabs">
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'models' }" @click="activeTab = 'models'">Models</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'routing' }" @click="activeTab = 'routing'">Routing</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'thinking' }" @click="activeTab = 'thinking'">Thinking</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'queue' }" @click="activeTab = 'queue'">Queue</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'crash' }" @click="activeTab = 'crash'">Crash Handler</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'stats' }" @click="activeTab = 'stats'">Stats</button>
    </div>

    <!-- Models -->
    <div v-if="activeTab === 'models'" class="tab-content">
      <div v-if="loadingModels" class="loading-text">Loading…</div>
      <template v-else-if="modelState">
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">Status</div>
            <div class="stat-value">{{ modelState.status ?? 'unknown' }}</div>
          </div>
          <div class="stat-card" v-for="(val, key) in (modelState.vram || {})" :key="key">
            <div class="stat-label">VRAM {{ key }}</div>
            <div class="stat-value">{{ typeof val === 'number' ? (val / 1024 / 1024).toFixed(0) + ' MB' : val }}</div>
          </div>
        </div>
        <h4 class="section-title">Loaded Models</h4>
        <div v-if="modelState.models?.length">
          <div class="model-card" v-for="m in modelState.models" :key="m.name || m">
            <span class="model-name">{{ typeof m === 'string' ? m : m.name }}</span>
          </div>
        </div>
        <p v-else class="empty-text">No models currently loaded.</p>
      </template>
    </div>

    <!-- Routing -->
    <div v-if="activeTab === 'routing'" class="tab-content">
      <div v-if="routing?.layer_distribution?.length" class="stats-grid" style="margin-bottom: 1rem">
        <div class="stat-card" v-for="d in routing.layer_distribution" :key="d.matched_layer">
          <div class="stat-label">{{ d.matched_layer || 'Unknown' }}</div>
          <div class="stat-value">{{ d.count }}</div>
        </div>
      </div>
      <DataTable :columns="routingColumns" :rows="routing?.decisions || []" :loading="loadingRouting" />
    </div>

    <!-- Thinking -->
    <div v-if="activeTab === 'thinking'" class="tab-content">
      <div v-if="loadingThinking" class="loading-text">Loading…</div>
      <template v-else-if="thinkingStats">
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">Enabled</div>
            <div class="stat-value">{{ thinkingStats.enabled ? 'Yes' : 'No' }}</div>
          </div>
          <div class="stat-card" v-for="(val, key) in (thinkingStats.stats || {})" :key="key">
            <div class="stat-label">{{ key }}</div>
            <div class="stat-value">{{ val }}</div>
          </div>
        </div>
      </template>
    </div>

    <!-- Queue -->
    <div v-if="activeTab === 'queue'" class="tab-content">
      <div v-if="loadingQueue" class="loading-text">Loading…</div>
      <div v-else-if="queueState" class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">Queue Depth</div>
          <div class="stat-value">{{ queueState.depth ?? 0 }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Active</div>
          <div class="stat-value">{{ queueState.active ?? 0 }}</div>
        </div>
        <div class="stat-card" v-for="(val, key) in (queueState.stats || {})" :key="key">
          <div class="stat-label">{{ key }}</div>
          <div class="stat-value">{{ val }}</div>
        </div>
      </div>
    </div>

    <!-- Crash Handler -->
    <div v-if="activeTab === 'crash'" class="tab-content">
      <div v-if="loadingCrash" class="loading-text">Loading…</div>
      <template v-else-if="crashState">
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">State</div>
            <div class="stat-value" :class="{ 'text-danger': crashState.circuit_open }">
              {{ crashState.circuit_open ? '🔴 Open' : '🟢 Closed' }}
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Error Count</div>
            <div class="stat-value">{{ crashState.error_count ?? 0 }}</div>
          </div>
        </div>
        <h4 class="section-title" v-if="crashState.recent_errors?.length">Recent Errors</h4>
        <div class="error-item" v-for="(err, i) in (crashState.recent_errors || [])" :key="i">
          {{ typeof err === 'string' ? err : JSON.stringify(err) }}
        </div>
      </template>
    </div>

    <!-- Stats -->
    <div v-if="activeTab === 'stats'" class="tab-content">
      <div v-if="loadingStats" class="loading-text">Loading…</div>
      <div v-else-if="pipelineStats?.stats" class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">Total Interactions</div>
          <div class="stat-value">{{ pipelineStats.stats.total ?? 0 }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Avg Response (ms)</div>
          <div class="stat-value">{{ pipelineStats.stats.avg_response_ms?.toFixed(0) ?? '—' }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Min (ms)</div>
          <div class="stat-value">{{ pipelineStats.stats.min_response_ms ?? '—' }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Max (ms)</div>
          <div class="stat-value">{{ pipelineStats.stats.max_response_ms ?? '—' }}</div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.page-title { margin: 0 0 1.5rem; font-size: 1.5rem; color: #eee; }
.error-banner { background: rgba(220,50,50,0.15); border: 1px solid rgba(220,50,50,0.4); color: #ff6b6b; padding: 0.8rem 1rem; border-radius: 8px; margin-bottom: 1rem; }
.tabs { display: flex; margin-bottom: 1.5rem; border-bottom: 2px solid #2a2a4a; flex-wrap: wrap; }
.tab-btn { background: none; border: none; padding: 0.7rem 1.2rem; color: #888; font-size: 0.9rem; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: color 0.2s; }
.tab-btn:hover { color: #ccc; }
.tab-btn--active { color: #646cff; border-bottom-color: #646cff; }
.tab-content { background: #1a1a2e; border-radius: 8px; padding: 1.5rem; }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 1rem; }
.stat-card { background: #16162a; border-radius: 8px; padding: 1rem; text-align: center; }
.stat-label { font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.3rem; }
.stat-value { font-size: 1.5rem; font-weight: 700; color: #eee; }
.section-title { color: #ccc; font-size: 1rem; margin: 1.5rem 0 0.75rem; }
.loading-text { text-align: center; color: #888; padding: 1.5rem; }
.empty-text { color: #666; font-style: italic; }
.model-card { background: #16162a; border-radius: 6px; padding: 0.6rem 1rem; margin-bottom: 0.4rem; }
.model-name { color: #eee; font-family: monospace; font-size: 0.9rem; }
.text-danger { color: #ff6b6b !important; }
.error-item { background: rgba(220,50,50,0.1); border-left: 3px solid #dc3545; padding: 0.6rem 1rem; margin-bottom: 0.4rem; color: #ff8888; font-size: 0.85rem; font-family: monospace; }
.btn { border: none; border-radius: 6px; padding: 0.5rem 1rem; cursor: pointer; font-size: 0.85rem; font-weight: 600; background: #2a2a4a; color: #ccc; }
</style>
