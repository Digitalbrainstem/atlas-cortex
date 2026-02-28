<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const activeTab = ref('devices')
const error = ref('')
const success = ref('')

// Devices
const devices = ref([])
const loadingDevices = ref(true)
const deviceColumns = [
  { key: 'entity_id', label: 'Entity ID' },
  { key: 'friendly_name', label: 'Name' },
  { key: 'domain', label: 'Domain' },
  { key: 'area_name', label: 'Area' },
  { key: 'state', label: 'State' },
  { key: 'aliases', label: 'Aliases' },
]

// Patterns
const patterns = ref([])
const loadingPatterns = ref(true)

onMounted(() => {
  fetchDevices()
  fetchPatterns()
})

async function fetchDevices() {
  loadingDevices.value = true
  error.value = ''
  try {
    const data = await api.get('/admin/devices')
    devices.value = (data.devices || data.items || data).map(d => ({
      ...d,
      aliases: Array.isArray(d.aliases) ? d.aliases.join(', ') : d.aliases || '—',
    }))
  } catch (e) {
    error.value = e.message
  } finally {
    loadingDevices.value = false
  }
}

async function fetchPatterns() {
  loadingPatterns.value = true
  error.value = ''
  try {
    const data = await api.get('/admin/devices/patterns')
    patterns.value = data.patterns || data.items || data
  } catch (e) {
    error.value = e.message
  } finally {
    loadingPatterns.value = false
  }
}

async function deletePattern(id) {
  if (!confirm('Delete this pattern?')) return
  error.value = ''
  success.value = ''
  try {
    await api.delete(`/admin/devices/patterns/${id}`)
    patterns.value = patterns.value.filter(p => p.id !== id)
    success.value = 'Pattern deleted'
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Devices &amp; Patterns</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <div class="tabs">
      <button
        class="tab-btn"
        :class="{ 'tab-btn--active': activeTab === 'devices' }"
        @click="activeTab = 'devices'"
      >Devices</button>
      <button
        class="tab-btn"
        :class="{ 'tab-btn--active': activeTab === 'patterns' }"
        @click="activeTab = 'patterns'"
      >Patterns</button>
    </div>

    <div v-if="activeTab === 'devices'" class="tab-content">
      <DataTable :columns="deviceColumns" :rows="devices" :loading="loadingDevices" />
    </div>

    <div v-if="activeTab === 'patterns'" class="tab-content">
      <table class="table">
        <thead>
          <tr>
            <th>Pattern</th>
            <th>Intent</th>
            <th>Source</th>
            <th>Confidence</th>
            <th>Hits</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loadingPatterns">
            <td colspan="6" class="loading-text">Loading…</td>
          </tr>
          <tr v-else-if="!patterns.length">
            <td colspan="6" class="loading-text">No patterns</td>
          </tr>
          <tr v-for="p in patterns" :key="p.id">
            <td>{{ p.pattern }}</td>
            <td>{{ p.intent || '—' }}</td>
            <td>{{ p.source || '—' }}</td>
            <td>{{ p.confidence != null ? p.confidence.toFixed(2) : '—' }}</td>
            <td>{{ p.hit_count ?? '—' }}</td>
            <td>
              <button class="btn btn-sm btn-danger" @click="deletePattern(p.id)">Delete</button>
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
}
.tab-btn:hover { color: #ccc; }
.tab-btn--active {
  color: #646cff;
  border-bottom-color: #646cff;
}
.tab-content {
  background: #1a1a2e;
  border-radius: 8px;
  padding: 1.5rem;
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
}
.btn-sm {
  padding: 0.3rem 0.6rem;
  font-size: 0.8rem;
}
.btn-danger {
  background: #dc3545;
  color: #fff;
}
.btn-danger:hover {
  background: #c82333;
}
</style>
