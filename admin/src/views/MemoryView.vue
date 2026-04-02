<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const activeTab = ref('knowledge-tree')
const error = ref('')
const success = ref('')

// Knowledge tree
const treeStats = ref(null)
const treeData = ref(null)
const loadingTree = ref(true)

// Memory index
const indexStats = ref(null)
const loadingIndex = ref(true)

// Sessions
const sessions = ref([])
const sessionsPage = ref(1)
const sessionsTotal = ref(0)
const loadingSessions = ref(true)
const sessionColumns = [
  { key: 'id', label: 'ID' },
  { key: 'user_id', label: 'User' },
  { key: 'room', label: 'Room' },
  { key: 'status', label: 'Status' },
]

// Compaction
const compactionStats = ref(null)
const loadingCompaction = ref(true)

// CAG usage
const cagUsage = ref([])
const loadingCag = ref(true)
const cagColumns = [
  { key: 'bank_id', label: 'Bank' },
  { key: 'query', label: 'Query' },
  { key: 'mode', label: 'Mode' },
  { key: 'tokens_saved', label: 'Tokens Saved' },
  { key: 'latency_ms', label: 'Latency (ms)' },
]

async function fetchTree() {
  loadingTree.value = true
  try {
    const data = await api.get('/admin/memory/knowledge-tree')
    treeStats.value = data.stats || {}
    treeData.value = data.tree || {}
  } catch (e) { error.value = e.message }
  finally { loadingTree.value = false }
}

async function fetchIndex() {
  loadingIndex.value = true
  try {
    indexStats.value = await api.get('/admin/memory/index-stats')
  } catch (e) { error.value = e.message }
  finally { loadingIndex.value = false }
}

async function fetchSessions() {
  loadingSessions.value = true
  try {
    const data = await api.get(`/admin/memory/sessions?page=${sessionsPage.value}`)
    sessions.value = data.sessions || []
    sessionsTotal.value = data.total || 0
  } catch (e) { error.value = e.message }
  finally { loadingSessions.value = false }
}

async function fetchCompaction() {
  loadingCompaction.value = true
  try {
    compactionStats.value = await api.get('/admin/memory/compaction')
  } catch (e) { error.value = e.message }
  finally { loadingCompaction.value = false }
}

async function fetchCag() {
  loadingCag.value = true
  try {
    const data = await api.get('/admin/memory/cag-usage')
    cagUsage.value = data.usage || []
  } catch (e) { error.value = e.message }
  finally { loadingCag.value = false }
}

async function triggerCompaction() {
  error.value = ''
  success.value = ''
  try {
    await api.post('/admin/memory/compaction/trigger')
    success.value = 'Compaction triggered successfully'
    fetchCompaction()
  } catch (e) { error.value = e.message }
}

onMounted(() => {
  fetchTree()
  fetchIndex()
  fetchSessions()
  fetchCompaction()
  fetchCag()
})
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Memory System</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <div class="tabs">
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'knowledge-tree' }" @click="activeTab = 'knowledge-tree'">Knowledge Tree</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'index' }" @click="activeTab = 'index'">Memory Index</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'sessions' }" @click="activeTab = 'sessions'">Sessions</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'compaction' }" @click="activeTab = 'compaction'">Compaction</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'cag' }" @click="activeTab = 'cag'">CAG Usage</button>
    </div>

    <!-- Knowledge Tree -->
    <div v-if="activeTab === 'knowledge-tree'" class="tab-content">
      <div v-if="loadingTree" class="loading-text">Loading…</div>
      <div v-else-if="treeStats" class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">Total Nodes</div>
          <div class="stat-value">{{ treeStats.total_nodes ?? '—' }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Total Facts</div>
          <div class="stat-value">{{ treeStats.total_facts ?? '—' }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Tree Depth</div>
          <div class="stat-value">{{ treeStats.depth ?? '—' }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Categories</div>
          <div class="stat-value">{{ treeStats.categories ?? '—' }}</div>
        </div>
      </div>
    </div>

    <!-- Memory Index -->
    <div v-if="activeTab === 'index'" class="tab-content">
      <div v-if="loadingIndex" class="loading-text">Loading…</div>
      <template v-else-if="indexStats">
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">Total Entries</div>
            <div class="stat-value">{{ indexStats.total_entries ?? 0 }}</div>
          </div>
        </div>
        <h4 class="section-title">Operation Stats</h4>
        <table class="table" v-if="indexStats.operation_stats?.length">
          <thead><tr><th>Operation</th><th>Count</th><th>Avg Latency (ms)</th></tr></thead>
          <tbody>
            <tr v-for="op in indexStats.operation_stats" :key="op.operation">
              <td>{{ op.operation }}</td>
              <td>{{ op.count }}</td>
              <td>{{ op.avg_latency_ms?.toFixed(1) ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty-text">No operation stats recorded yet.</p>
      </template>
    </div>

    <!-- Sessions -->
    <div v-if="activeTab === 'sessions'" class="tab-content">
      <div class="section-header">
        <span class="total-badge">{{ sessionsTotal }} sessions</span>
      </div>
      <DataTable :columns="sessionColumns" :rows="sessions" :loading="loadingSessions" />
      <div class="pagination">
        <button class="btn" :disabled="sessionsPage <= 1" @click="sessionsPage--; fetchSessions()">← Previous</button>
        <span class="page-info">Page {{ sessionsPage }}</span>
        <button class="btn" @click="sessionsPage++; fetchSessions()">Next →</button>
      </div>
    </div>

    <!-- Compaction -->
    <div v-if="activeTab === 'compaction'" class="tab-content">
      <div v-if="loadingCompaction" class="loading-text">Loading…</div>
      <template v-else>
        <div class="stats-grid" v-if="compactionStats?.stats">
          <div class="stat-card" v-for="(val, key) in compactionStats.stats" :key="key">
            <div class="stat-label">{{ key }}</div>
            <div class="stat-value">{{ val }}</div>
          </div>
        </div>
        <p v-else class="empty-text">No compaction stats available.</p>
        <button class="btn btn-primary" style="margin-top: 1rem" @click="triggerCompaction">Trigger Compaction</button>
      </template>
    </div>

    <!-- CAG Usage -->
    <div v-if="activeTab === 'cag'" class="tab-content">
      <DataTable :columns="cagColumns" :rows="cagUsage" :loading="loadingCag" />
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
.stats-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
.stat-card { background: #16162a; border-radius: 8px; padding: 1rem; text-align: center; }
.stat-label { font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.3rem; }
.stat-value { font-size: 1.5rem; font-weight: 700; color: #eee; }
.section-title { color: #ccc; font-size: 1rem; margin: 1.5rem 0 0.75rem; }
.table { width: 100%; border-collapse: collapse; }
.table th, .table td { padding: 0.6rem 0.8rem; text-align: left; border-bottom: 1px solid #2a2a4a; font-size: 0.9rem; }
.table th { color: #aaa; font-weight: 600; font-size: 0.8rem; text-transform: uppercase; }
.loading-text { text-align: center; color: #888; padding: 1.5rem; }
.empty-text { color: #666; font-style: italic; padding: 0.5rem 0; }
.section-header { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
.total-badge { background: #2a2a4a; color: #aaa; padding: 0.3rem 0.8rem; border-radius: 4px; font-size: 0.85rem; }
.pagination { display: flex; align-items: center; justify-content: center; gap: 1rem; margin-top: 1rem; }
.page-info { color: #aaa; font-size: 0.9rem; }
.btn { border: none; border-radius: 6px; padding: 0.5rem 1rem; cursor: pointer; font-size: 0.85rem; font-weight: 600; background: #2a2a4a; color: #ccc; }
.btn:hover:not(:disabled) { background: #3a3a5a; }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-primary { background: #646cff; color: #fff; }
.btn-primary:hover { background: #535bf2; }
</style>
