<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api.js'

const activeTab = ref('config')
const error = ref('')
const success = ref('')

// Config
const codingConfig = ref(null)
const loadingConfig = ref(true)

// Errors
const errors = ref(null)
const loadingErrors = ref(true)

// Known bugs
const bugs = ref(null)
const loadingBugs = ref(true)

// Review
const reviewCode = ref('')
const reviewLang = ref('python')
const reviewResult = ref(null)
const reviewLoading = ref(false)

// Stats
const codingStats = ref(null)
const loadingStats = ref(true)

async function fetchConfig() {
  loadingConfig.value = true
  try { codingConfig.value = await api.get('/admin/coding/config') }
  catch (e) { error.value = e.message }
  finally { loadingConfig.value = false }
}

async function fetchErrors() {
  loadingErrors.value = true
  try { errors.value = await api.get('/admin/coding/errors') }
  catch (e) { error.value = e.message }
  finally { loadingErrors.value = false }
}

async function fetchBugs() {
  loadingBugs.value = true
  try { bugs.value = await api.get('/admin/coding/known-bugs') }
  catch (e) { error.value = e.message }
  finally { loadingBugs.value = false }
}

async function fetchStats() {
  loadingStats.value = true
  try { codingStats.value = await api.get('/admin/coding/stats') }
  catch (e) { error.value = e.message }
  finally { loadingStats.value = false }
}

async function submitReview() {
  if (!reviewCode.value.trim()) return
  reviewLoading.value = true
  reviewResult.value = null
  error.value = ''
  try {
    reviewResult.value = await api.post('/admin/coding/review', {
      code: reviewCode.value,
      language: reviewLang.value,
    })
    success.value = 'Code review completed'
  } catch (e) { error.value = e.message }
  finally { reviewLoading.value = false }
}

onMounted(() => {
  fetchConfig()
  fetchErrors()
  fetchBugs()
  fetchStats()
})
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Coding Pipeline</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <div class="tabs">
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'config' }" @click="activeTab = 'config'">Config</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'errors' }" @click="activeTab = 'errors'">Error Memory</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'bugs' }" @click="activeTab = 'bugs'">Known Bugs</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'review' }" @click="activeTab = 'review'">Code Review</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'stats' }" @click="activeTab = 'stats'">Stats</button>
    </div>

    <!-- Config -->
    <div v-if="activeTab === 'config'" class="tab-content">
      <div v-if="loadingConfig" class="loading-text">Loading…</div>
      <template v-else-if="codingConfig">
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">Enabled</div>
            <div class="stat-value">{{ codingConfig.enabled ? 'Yes' : 'No' }}</div>
          </div>
        </div>
        <div v-if="codingConfig.config && Object.keys(codingConfig.config).length" class="config-table">
          <table class="table">
            <thead><tr><th>Key</th><th>Value</th></tr></thead>
            <tbody>
              <tr v-for="(val, key) in codingConfig.config" :key="key">
                <td><code>{{ key }}</code></td>
                <td>{{ typeof val === 'object' ? JSON.stringify(val) : val }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="empty-text">No configuration data available.</p>
      </template>
    </div>

    <!-- Error Memory -->
    <div v-if="activeTab === 'errors'" class="tab-content">
      <div v-if="loadingErrors" class="loading-text">Loading…</div>
      <template v-else-if="errors">
        <div class="stats-grid" style="margin-bottom: 1rem">
          <div class="stat-card">
            <div class="stat-label">Total Errors</div>
            <div class="stat-value">{{ errors.total ?? 0 }}</div>
          </div>
          <div class="stat-card" v-for="(val, key) in (errors.stats || {})" :key="key">
            <div class="stat-label">{{ key }}</div>
            <div class="stat-value">{{ val }}</div>
          </div>
        </div>
        <div v-if="errors.errors?.length">
          <div class="error-item" v-for="(err, i) in errors.errors" :key="i">
            <pre>{{ typeof err === 'string' ? err : JSON.stringify(err, null, 2) }}</pre>
          </div>
        </div>
        <p v-else class="empty-text">No errors recorded.</p>
      </template>
    </div>

    <!-- Known Bugs -->
    <div v-if="activeTab === 'bugs'" class="tab-content">
      <div v-if="loadingBugs" class="loading-text">Loading…</div>
      <template v-else-if="bugs">
        <div class="stats-grid" style="margin-bottom: 1rem">
          <div class="stat-card">
            <div class="stat-label">Total Bugs</div>
            <div class="stat-value">{{ bugs.total ?? 0 }}</div>
          </div>
        </div>
        <div v-if="bugs.bugs?.length">
          <div class="bug-item" v-for="(bug, i) in bugs.bugs" :key="i">
            <pre>{{ typeof bug === 'string' ? bug : JSON.stringify(bug, null, 2) }}</pre>
          </div>
        </div>
        <p v-else class="empty-text">No known bugs recorded.</p>
      </template>
    </div>

    <!-- Code Review -->
    <div v-if="activeTab === 'review'" class="tab-content">
      <div class="review-form">
        <div class="form-group">
          <label class="form-label">Language</label>
          <select v-model="reviewLang" class="form-input">
            <option value="python">Python</option>
            <option value="javascript">JavaScript</option>
            <option value="typescript">TypeScript</option>
            <option value="go">Go</option>
            <option value="rust">Rust</option>
          </select>
        </div>
        <div class="form-group" style="flex: 1">
          <label class="form-label">Code</label>
          <textarea v-model="reviewCode" class="form-input code-input" rows="8" placeholder="Paste code to review…"></textarea>
        </div>
        <button class="btn btn-primary" :disabled="reviewLoading || !reviewCode.trim()" @click="submitReview">
          {{ reviewLoading ? 'Reviewing…' : 'Submit Review' }}
        </button>
      </div>
      <div v-if="reviewResult" class="review-result">
        <h4 class="section-title">Review Result</h4>
        <pre>{{ typeof reviewResult.review === 'string' ? reviewResult.review : JSON.stringify(reviewResult.review, null, 2) }}</pre>
      </div>
    </div>

    <!-- Stats -->
    <div v-if="activeTab === 'stats'" class="tab-content">
      <div v-if="loadingStats" class="loading-text">Loading…</div>
      <div v-else-if="codingStats?.stats && Object.keys(codingStats.stats).length" class="stats-grid">
        <div class="stat-card" v-for="(val, key) in codingStats.stats" :key="key">
          <div class="stat-label">{{ key }}</div>
          <div class="stat-value">{{ val }}</div>
        </div>
      </div>
      <p v-else class="empty-text">No coding stats available.</p>
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
.table { width: 100%; border-collapse: collapse; }
.table th, .table td { padding: 0.6rem 0.8rem; text-align: left; border-bottom: 1px solid #2a2a4a; font-size: 0.9rem; }
.table th { color: #aaa; font-weight: 600; font-size: 0.8rem; text-transform: uppercase; }
.table code { background: #16162a; padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.85rem; }
.error-item { background: rgba(220,50,50,0.1); border-left: 3px solid #dc3545; padding: 0.6rem 1rem; margin-bottom: 0.4rem; }
.error-item pre { color: #ff8888; font-size: 0.8rem; white-space: pre-wrap; margin: 0; }
.bug-item { background: #16162a; border-left: 3px solid #ffc107; padding: 0.6rem 1rem; margin-bottom: 0.4rem; }
.bug-item pre { color: #ccc; font-size: 0.8rem; white-space: pre-wrap; margin: 0; }
.review-form { display: flex; flex-direction: column; gap: 1rem; }
.form-group { display: flex; flex-direction: column; gap: 0.3rem; }
.form-label { font-size: 0.8rem; color: #aaa; }
.form-input { background: #16162a; border: 1px solid #2a2a4a; border-radius: 6px; padding: 0.5rem 0.7rem; color: #eee; font-size: 0.9rem; outline: none; }
.form-input:focus { border-color: #646cff; }
.code-input { font-family: monospace; resize: vertical; }
.review-result { margin-top: 1.5rem; }
.review-result pre { background: #16162a; padding: 1rem; border-radius: 8px; color: #ccc; font-size: 0.85rem; white-space: pre-wrap; }
.btn { border: none; border-radius: 6px; padding: 0.5rem 1rem; cursor: pointer; font-size: 0.85rem; font-weight: 600; background: #2a2a4a; color: #ccc; }
.btn:hover:not(:disabled) { background: #3a3a5a; }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-primary { background: #646cff; color: #fff; }
.btn-primary:hover { background: #535bf2; }
</style>
