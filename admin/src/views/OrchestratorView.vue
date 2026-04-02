<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const activeTab = ref('routing')
const error = ref('')
const success = ref('')

// Routing
const routingStats = ref(null)
const loadingRouting = ref(true)

// Agents
const agents = ref(null)
const loadingAgents = ref(true)

// Decisions
const decisions = ref([])
const loadingDecisions = ref(true)
const decisionColumns = [
  { key: 'id', label: 'ID' },
  { key: 'user_id', label: 'User' },
  { key: 'message', label: 'Message' },
  { key: 'matched_layer', label: 'Layer' },
  { key: 'intent', label: 'Intent' },
  { key: 'response_time_ms', label: 'Latency (ms)' },
  { key: 'created_at', label: 'Time' },
]

// Voice stats
const voiceStats = ref(null)
const loadingVoice = ref(true)

// Wake word
const wakeWord = ref(null)
const loadingWake = ref(true)

async function fetchRouting() {
  loadingRouting.value = true
  try { routingStats.value = await api.get('/admin/orchestrator/routing') }
  catch (e) { error.value = e.message }
  finally { loadingRouting.value = false }
}

async function fetchAgents() {
  loadingAgents.value = true
  try { agents.value = await api.get('/admin/orchestrator/agents') }
  catch (e) { error.value = e.message }
  finally { loadingAgents.value = false }
}

async function fetchDecisions() {
  loadingDecisions.value = true
  try {
    const data = await api.get('/admin/orchestrator/decisions')
    decisions.value = data.decisions || []
  } catch (e) { error.value = e.message }
  finally { loadingDecisions.value = false }
}

async function fetchVoice() {
  loadingVoice.value = true
  try { voiceStats.value = await api.get('/admin/orchestrator/voice-stats') }
  catch (e) { error.value = e.message }
  finally { loadingVoice.value = false }
}

async function fetchWakeWord() {
  loadingWake.value = true
  try { wakeWord.value = await api.get('/admin/orchestrator/wake-word') }
  catch (e) { error.value = e.message }
  finally { loadingWake.value = false }
}

async function toggleWakeWord() {
  if (!wakeWord.value) return
  error.value = ''
  success.value = ''
  try {
    wakeWord.value = await api.put('/admin/orchestrator/wake-word', {
      enabled: !wakeWord.value.enabled,
    })
    success.value = `Wake word ${wakeWord.value.enabled ? 'enabled' : 'disabled'}`
  } catch (e) { error.value = e.message }
}

onMounted(() => {
  fetchRouting()
  fetchAgents()
  fetchDecisions()
  fetchVoice()
  fetchWakeWord()
})
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Orchestrator</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <div class="tabs">
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'routing' }" @click="activeTab = 'routing'">Routing</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'agents' }" @click="activeTab = 'agents'">Agents</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'decisions' }" @click="activeTab = 'decisions'">Decisions</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'voice' }" @click="activeTab = 'voice'">Voice Stats</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'wake-word' }" @click="activeTab = 'wake-word'">Wake Word</button>
    </div>

    <!-- Routing -->
    <div v-if="activeTab === 'routing'" class="tab-content">
      <div v-if="loadingRouting" class="loading-text">Loading…</div>
      <template v-else-if="routingStats">
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">Enabled</div>
            <div class="stat-value">{{ routingStats.enabled ? 'Yes' : 'No' }}</div>
          </div>
          <div class="stat-card" v-for="(val, key) in (routingStats.stats || {})" :key="key">
            <div class="stat-label">{{ key }}</div>
            <div class="stat-value">{{ val }}</div>
          </div>
        </div>
        <h4 class="section-title" v-if="routingStats.routing_rules?.length">Routing Rules</h4>
        <div v-for="(rule, i) in (routingStats.routing_rules || [])" :key="i" class="rule-card">
          {{ typeof rule === 'string' ? rule : JSON.stringify(rule) }}
        </div>
      </template>
    </div>

    <!-- Agents -->
    <div v-if="activeTab === 'agents'" class="tab-content">
      <div v-if="loadingAgents" class="loading-text">Loading…</div>
      <template v-else-if="agents">
        <div class="stats-grid" style="margin-bottom: 1rem">
          <div class="stat-card">
            <div class="stat-label">Active Agents</div>
            <div class="stat-value">{{ agents.total ?? 0 }}</div>
          </div>
        </div>
        <div v-if="agents.agents?.length">
          <div class="agent-card" v-for="(agent, i) in agents.agents" :key="i">
            <pre>{{ typeof agent === 'string' ? agent : JSON.stringify(agent, null, 2) }}</pre>
          </div>
        </div>
        <p v-else class="empty-text">No active agents.</p>
      </template>
    </div>

    <!-- Decisions -->
    <div v-if="activeTab === 'decisions'" class="tab-content">
      <DataTable :columns="decisionColumns" :rows="decisions" :loading="loadingDecisions" />
    </div>

    <!-- Voice Stats -->
    <div v-if="activeTab === 'voice'" class="tab-content">
      <div v-if="loadingVoice" class="loading-text">Loading…</div>
      <div v-else-if="voiceStats?.stats" class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">Total Interactions</div>
          <div class="stat-value">{{ voiceStats.stats.total ?? 0 }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Avg Latency (ms)</div>
          <div class="stat-value">{{ voiceStats.stats.avg_latency_ms?.toFixed(0) ?? '—' }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Filler Uses</div>
          <div class="stat-value">{{ voiceStats.stats.filler_count ?? 0 }}</div>
        </div>
      </div>
    </div>

    <!-- Wake Word -->
    <div v-if="activeTab === 'wake-word'" class="tab-content">
      <div v-if="loadingWake" class="loading-text">Loading…</div>
      <template v-else-if="wakeWord">
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">Status</div>
            <div class="stat-value" :class="{ 'text-success': wakeWord.enabled, 'text-muted': !wakeWord.enabled }">
              {{ wakeWord.enabled ? '🟢 Enabled' : '⚪ Disabled' }}
            </div>
          </div>
        </div>
        <div class="wake-keywords" v-if="wakeWord.keywords?.length">
          <h4 class="section-title">Keywords</h4>
          <span class="keyword-badge" v-for="kw in wakeWord.keywords" :key="kw">{{ kw }}</span>
        </div>
        <button class="btn" :class="wakeWord.enabled ? 'btn-danger' : 'btn-primary'" style="margin-top: 1.5rem" @click="toggleWakeWord">
          {{ wakeWord.enabled ? 'Disable Wake Word' : 'Enable Wake Word' }}
        </button>
      </template>
    </div>
  </AppLayout>
</template>

<style scoped>
.page-title { margin: 0 0 1.5rem; font-size: 1.5rem; color: #eee; }
.error-banner { background: rgba(220,50,50,0.15); border: 1px solid rgba(220,50,50,0.4); color: #ff6b6b; padding: 0.8rem 1rem; border-radius: 8px; margin-bottom: 1rem; }
.success-banner { background: rgba(66,184,131,0.15); border: 1px solid rgba(66,184,131,0.4); color: #42b883; padding: 0.8rem 1rem; border-radius: 8px; margin-bottom: 1rem; }
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
.text-success { color: #42b883 !important; }
.text-muted { color: #888 !important; }
.rule-card { background: #16162a; border-radius: 6px; padding: 0.6rem 1rem; margin-bottom: 0.4rem; color: #ccc; font-size: 0.9rem; }
.agent-card { background: #16162a; border-radius: 6px; padding: 0.8rem; margin-bottom: 0.4rem; }
.agent-card pre { color: #ccc; font-size: 0.8rem; white-space: pre-wrap; margin: 0; }
.wake-keywords { margin-top: 1rem; }
.keyword-badge { display: inline-block; background: #2a2a4a; color: #eee; padding: 0.3rem 0.8rem; border-radius: 16px; margin: 0.2rem; font-size: 0.9rem; }
.btn { border: none; border-radius: 6px; padding: 0.5rem 1rem; cursor: pointer; font-size: 0.85rem; font-weight: 600; background: #2a2a4a; color: #ccc; }
.btn:hover:not(:disabled) { background: #3a3a5a; }
.btn-primary { background: #646cff; color: #fff; }
.btn-primary:hover { background: #535bf2; }
.btn-danger { background: #dc3545; color: #fff; }
.btn-danger:hover { background: #c82333; }
</style>
