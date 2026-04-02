<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const activeTab = ref('sandbox')
const error = ref('')

// Sandbox
const sandbox = ref(null)
const loadingSandbox = ref(true)

// Functions
const functions = ref(null)
const loadingFunctions = ref(true)
const functionColumns = [
  { key: 'name', label: 'Name' },
  { key: 'description', label: 'Description' },
  { key: 'parameters', label: 'Parameters' },
]

// Doc cache
const docCache = ref(null)
const loadingDoc = ref(true)

// Plugin registry
const plugins = ref([])
const loadingPlugins = ref(true)
const pluginColumns = [
  { key: 'id', label: 'ID' },
  { key: 'display_name', label: 'Name' },
  { key: 'plugin_type', label: 'Type' },
  { key: 'is_active', label: 'Active' },
  { key: 'health_status', label: 'Health' },
  { key: 'pattern_count', label: 'Patterns' },
]

async function fetchSandbox() {
  loadingSandbox.value = true
  try { sandbox.value = await api.get('/admin/tools/sandbox') }
  catch (e) { error.value = e.message }
  finally { loadingSandbox.value = false }
}

async function fetchFunctions() {
  loadingFunctions.value = true
  try { functions.value = await api.get('/admin/tools/functions') }
  catch (e) { error.value = e.message }
  finally { loadingFunctions.value = false }
}

async function fetchDoc() {
  loadingDoc.value = true
  try { docCache.value = await api.get('/admin/tools/doc-cache') }
  catch (e) { error.value = e.message }
  finally { loadingDoc.value = false }
}

async function fetchPlugins() {
  loadingPlugins.value = true
  try {
    const data = await api.get('/admin/tools/plugin-registry')
    plugins.value = data.plugins || []
  } catch (e) { error.value = e.message }
  finally { loadingPlugins.value = false }
}

onMounted(() => {
  fetchSandbox()
  fetchFunctions()
  fetchDoc()
  fetchPlugins()
})
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Tools</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="tabs">
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'sandbox' }" @click="activeTab = 'sandbox'">Code Sandbox</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'functions' }" @click="activeTab = 'functions'">Functions</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'doc-cache' }" @click="activeTab = 'doc-cache'">Doc Cache</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'registry' }" @click="activeTab = 'registry'">Plugin Registry</button>
    </div>

    <!-- Code Sandbox -->
    <div v-if="activeTab === 'sandbox'" class="tab-content">
      <div v-if="loadingSandbox" class="loading-text">Loading…</div>
      <template v-else-if="sandbox">
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">Enabled</div>
            <div class="stat-value">{{ sandbox.enabled ? 'Yes' : 'No' }}</div>
          </div>
          <div class="stat-card" v-for="(val, key) in (sandbox.stats || {})" :key="key">
            <div class="stat-label">{{ key }}</div>
            <div class="stat-value">{{ val }}</div>
          </div>
        </div>
        <h4 class="section-title" v-if="sandbox.executions?.length">Recent Executions</h4>
        <div class="exec-item" v-for="(ex, i) in (sandbox.executions || [])" :key="i">
          <pre>{{ typeof ex === 'string' ? ex : JSON.stringify(ex, null, 2) }}</pre>
        </div>
        <p v-if="!sandbox.executions?.length" class="empty-text">No executions recorded yet.</p>
      </template>
    </div>

    <!-- Functions -->
    <div v-if="activeTab === 'functions'" class="tab-content">
      <div v-if="loadingFunctions" class="loading-text">Loading…</div>
      <template v-else-if="functions">
        <div class="stats-grid" style="margin-bottom: 1rem">
          <div class="stat-card">
            <div class="stat-label">Registered Functions</div>
            <div class="stat-value">{{ functions.total ?? 0 }}</div>
          </div>
        </div>
        <div v-if="functions.functions?.length">
          <div class="func-card" v-for="fn in functions.functions" :key="fn.name || fn">
            <div class="func-name">{{ typeof fn === 'string' ? fn : fn.name }}</div>
            <div class="func-desc" v-if="fn.description">{{ fn.description }}</div>
          </div>
        </div>
        <p v-else class="empty-text">No functions registered.</p>
      </template>
    </div>

    <!-- Doc Cache -->
    <div v-if="activeTab === 'doc-cache'" class="tab-content">
      <div v-if="loadingDoc" class="loading-text">Loading…</div>
      <template v-else-if="docCache">
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">Enabled</div>
            <div class="stat-value">{{ docCache.enabled ? 'Yes' : 'No' }}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Cache Entries</div>
            <div class="stat-value">{{ docCache.entries ?? 0 }}</div>
          </div>
          <div class="stat-card" v-for="(val, key) in (docCache.cache || {})" :key="key">
            <div class="stat-label">{{ key }}</div>
            <div class="stat-value">{{ val }}</div>
          </div>
        </div>
      </template>
    </div>

    <!-- Plugin Registry -->
    <div v-if="activeTab === 'registry'" class="tab-content">
      <DataTable :columns="pluginColumns" :rows="plugins" :loading="loadingPlugins" />
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
.exec-item { background: #16162a; border-radius: 6px; padding: 0.8rem; margin-bottom: 0.4rem; }
.exec-item pre { color: #ccc; font-size: 0.8rem; white-space: pre-wrap; margin: 0; }
.func-card { background: #16162a; border-radius: 6px; padding: 0.8rem 1rem; margin-bottom: 0.4rem; }
.func-name { color: #eee; font-family: monospace; font-weight: 600; }
.func-desc { color: #888; font-size: 0.85rem; margin-top: 0.2rem; }
</style>
