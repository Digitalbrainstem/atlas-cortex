<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const activeTab = ref('events')
const loading = ref(true)
const error = ref('')

// Events
const events = ref([])
const eventsPage = ref(1)
const eventsTotalPages = ref(1)
const filterCategory = ref('')
const filterSeverity = ref('')

const eventColumns = [
  { key: 'id', label: 'ID' },
  { key: 'category', label: 'Category' },
  { key: 'severity', label: 'Severity' },
  { key: 'user_id', label: 'User' },
  { key: 'message', label: 'Message' },
  { key: 'created_at', label: 'Time' },
]

// Patterns
const patterns = ref([])
const loadingPatterns = ref(true)
const newPattern = ref('')

async function fetchEvents() {
  loading.value = true
  error.value = ''
  try {
    let url = `/admin/safety/events?page=${eventsPage.value}`
    if (filterCategory.value) url += `&category=${filterCategory.value}`
    if (filterSeverity.value) url += `&severity=${filterSeverity.value}`
    const data = await api.get(url)
    events.value = data.events || data.items || data
    eventsTotalPages.value = data.total_pages || data.pages || 1
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function fetchPatterns() {
  loadingPatterns.value = true
  try {
    const data = await api.get('/admin/safety/patterns')
    patterns.value = data.patterns || data.items || data
  } catch (e) {
    error.value = e.message
  } finally {
    loadingPatterns.value = false
  }
}

onMounted(() => {
  fetchEvents()
  fetchPatterns()
})

function applyFilters() {
  eventsPage.value = 1
  fetchEvents()
}

function prevPage() {
  if (eventsPage.value > 1) {
    eventsPage.value--
    fetchEvents()
  }
}

function nextPage() {
  if (eventsPage.value < eventsTotalPages.value) {
    eventsPage.value++
    fetchEvents()
  }
}

async function addPattern() {
  const pat = newPattern.value.trim()
  if (!pat) return
  error.value = ''
  try {
    await api.post('/admin/safety/patterns', { pattern: pat })
    newPattern.value = ''
    fetchPatterns()
  } catch (e) {
    error.value = e.message
  }
}

async function deletePattern(id) {
  if (!confirm('Delete this pattern?')) return
  error.value = ''
  try {
    await api.delete(`/admin/safety/patterns/${id}`)
    fetchPatterns()
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Safety &amp; Guardrails</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="tabs">
      <button
        class="tab-btn"
        :class="{ 'tab-btn--active': activeTab === 'events' }"
        @click="activeTab = 'events'"
      >Events</button>
      <button
        class="tab-btn"
        :class="{ 'tab-btn--active': activeTab === 'patterns' }"
        @click="activeTab = 'patterns'"
      >Patterns</button>
    </div>

    <div v-if="activeTab === 'events'" class="tab-content">
      <div class="filters">
        <div class="form-group">
          <label class="form-label">Category</label>
          <select v-model="filterCategory" class="form-input" @change="applyFilters">
            <option value="">All</option>
            <option value="profanity">Profanity</option>
            <option value="violence">Violence</option>
            <option value="pii">PII</option>
            <option value="jailbreak">Jailbreak</option>
            <option value="other">Other</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Severity</label>
          <select v-model="filterSeverity" class="form-input" @change="applyFilters">
            <option value="">All</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </select>
        </div>
      </div>
      <DataTable :columns="eventColumns" :rows="events" :loading="loading" />
      <div class="pagination">
        <button class="btn" :disabled="eventsPage <= 1" @click="prevPage">← Previous</button>
        <span class="page-info">Page {{ eventsPage }} of {{ eventsTotalPages }}</span>
        <button class="btn" :disabled="eventsPage >= eventsTotalPages" @click="nextPage">Next →</button>
      </div>
    </div>

    <div v-if="activeTab === 'patterns'" class="tab-content">
      <div class="pattern-add">
        <input v-model="newPattern" class="form-input" placeholder="Enter jailbreak pattern…" @keyup.enter="addPattern" />
        <button class="btn btn-primary" @click="addPattern">Add Pattern</button>
      </div>
      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Pattern</th>
            <th>Category</th>
            <th>Created</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loadingPatterns">
            <td colspan="5" class="loading-text">Loading…</td>
          </tr>
          <tr v-else-if="!patterns.length">
            <td colspan="5" class="loading-text">No patterns</td>
          </tr>
          <tr v-for="p in patterns" :key="p.id">
            <td>{{ p.id }}</td>
            <td><code>{{ p.pattern }}</code></td>
            <td>{{ p.category || '—' }}</td>
            <td>{{ p.created_at || '—' }}</td>
            <td>
              <button class="btn btn-danger btn-sm" @click="deletePattern(p.id)">Delete</button>
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
.tabs {
  display: flex;
  margin-bottom: 1.5rem;
  border-bottom: 2px solid #2a2a4a;
}
.tab-btn {
  background: none;
  border: none;
  padding: 0.7rem 1.5rem;
  color: #888;
  font-size: 0.95rem;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  transition: color 0.2s;
}
.tab-btn:hover {
  color: #ccc;
}
.tab-btn--active {
  color: #646cff;
  border-bottom-color: #646cff;
}
.tab-content {
  background: #1a1a2e;
  border-radius: 8px;
  padding: 1.5rem;
}
.filters {
  display: flex;
  gap: 1rem;
  margin-bottom: 1rem;
}
.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.form-label {
  font-size: 0.8rem;
  color: #aaa;
}
.form-input {
  background: #16162a;
  border: 1px solid #2a2a4a;
  border-radius: 6px;
  padding: 0.5rem 0.7rem;
  color: #eee;
  font-size: 0.9rem;
  outline: none;
}
.form-input:focus {
  border-color: #646cff;
}
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  margin-top: 1rem;
}
.page-info {
  color: #aaa;
  font-size: 0.9rem;
}
.pattern-add {
  display: flex;
  gap: 0.8rem;
  margin-bottom: 1.2rem;
}
.pattern-add .form-input {
  flex: 1;
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
.table code {
  background: #16162a;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  font-size: 0.85rem;
}
.loading-text {
  text-align: center;
  color: #888;
  padding: 1.5rem;
}
.btn {
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1rem;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 600;
  background: #2a2a4a;
  color: #ccc;
}
.btn:hover:not(:disabled) {
  background: #3a3a5a;
}
.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.btn-primary {
  background: #646cff;
  color: #fff;
}
.btn-primary:hover {
  background: #535bf2;
}
.btn-danger {
  background: #dc3545;
  color: #fff;
}
.btn-danger:hover {
  background: #c82333;
}
.btn-sm {
  padding: 0.3rem 0.6rem;
  font-size: 0.8rem;
}
</style>
