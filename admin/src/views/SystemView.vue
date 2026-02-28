<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const error = ref('')

// Hardware
const hardware = ref(null)
const loadingHardware = ref(true)

// Models
const models = ref([])
const loadingModels = ref(true)
const modelColumns = [
  { key: 'name', label: 'Name' },
  { key: 'type', label: 'Type' },
  { key: 'path', label: 'Path' },
  { key: 'size', label: 'Size' },
  { key: 'loaded', label: 'Loaded' },
]

// Services
const services = ref([])
const loadingServices = ref(true)

// Backups
const backups = ref([])
const loadingBackups = ref(true)
const backupColumns = [
  { key: 'id', label: 'ID' },
  { key: 'filename', label: 'File' },
  { key: 'size', label: 'Size' },
  { key: 'created_at', label: 'Created' },
  { key: 'type', label: 'Type' },
]

onMounted(() => {
  fetchHardware()
  fetchModels()
  fetchServices()
  fetchBackups()
})

async function fetchHardware() {
  loadingHardware.value = true
  try {
    hardware.value = await api.get('/admin/system/hardware')
  } catch (e) {
    error.value = e.message
  } finally {
    loadingHardware.value = false
  }
}

async function fetchModels() {
  loadingModels.value = true
  try {
    const data = await api.get('/admin/system/models')
    models.value = data.models || data.items || data
  } catch (e) {
    error.value = e.message
  } finally {
    loadingModels.value = false
  }
}

async function fetchServices() {
  loadingServices.value = true
  try {
    const data = await api.get('/admin/system/services')
    services.value = data.services || data.items || data
  } catch (e) {
    error.value = e.message
  } finally {
    loadingServices.value = false
  }
}

async function fetchBackups() {
  loadingBackups.value = true
  try {
    const data = await api.get('/admin/system/backups')
    backups.value = data.backups || data.items || data
  } catch (e) {
    error.value = e.message
  } finally {
    loadingBackups.value = false
  }
}

function healthColor(status) {
  if (status === 'healthy' || status === 'running' || status === 'up') return '#42b883'
  if (status === 'degraded' || status === 'warning') return '#f0a500'
  return '#ff6b6b'
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return '—'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB'
  return (bytes / 1073741824).toFixed(1) + ' GB'
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">System</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>

    <!-- Hardware -->
    <div class="section">
      <h3>Hardware</h3>
      <div v-if="loadingHardware" class="loading-text">Loading hardware info…</div>
      <div v-else-if="hardware" class="hw-grid">
        <div class="hw-card">
          <div class="hw-icon">🖥️</div>
          <div class="hw-info">
            <div class="hw-label">CPU</div>
            <div class="hw-value">{{ hardware.cpu?.model || hardware.cpu_model || '—' }}</div>
            <div class="hw-detail">
              Cores: {{ hardware.cpu?.cores || hardware.cpu_cores || '—' }}
              | Usage: {{ hardware.cpu?.usage_percent ?? hardware.cpu_usage ?? '—' }}%
            </div>
          </div>
        </div>
        <div class="hw-card">
          <div class="hw-icon">💾</div>
          <div class="hw-info">
            <div class="hw-label">RAM</div>
            <div class="hw-value">{{ formatBytes(hardware.ram?.total || hardware.ram_total) }}</div>
            <div class="hw-detail">
              Used: {{ formatBytes(hardware.ram?.used || hardware.ram_used) }}
              | {{ hardware.ram?.usage_percent ?? hardware.ram_usage ?? '—' }}%
            </div>
          </div>
        </div>
        <div class="hw-card">
          <div class="hw-icon">🎮</div>
          <div class="hw-info">
            <div class="hw-label">GPU</div>
            <div class="hw-value">{{ hardware.gpu?.model || hardware.gpu_model || 'N/A' }}</div>
            <div class="hw-detail">
              VRAM: {{ formatBytes(hardware.gpu?.vram_total || hardware.gpu_vram) }}
              | {{ hardware.gpu?.usage_percent ?? hardware.gpu_usage ?? '—' }}%
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Models -->
    <div class="section">
      <h3>Models</h3>
      <DataTable :columns="modelColumns" :rows="models" :loading="loadingModels" />
    </div>

    <!-- Services -->
    <div class="section">
      <h3>Services</h3>
      <div v-if="loadingServices" class="loading-text">Loading services…</div>
      <div v-else-if="!services.length" class="loading-text">No services discovered</div>
      <div v-else class="services-grid">
        <div v-for="svc in services" :key="svc.name || svc.id" class="service-card">
          <div class="service-status" :style="{ background: healthColor(svc.status || svc.health) }"></div>
          <div class="service-info">
            <div class="service-name">{{ svc.name || svc.id }}</div>
            <div class="service-detail">{{ svc.url || svc.endpoint || '' }}</div>
            <div class="service-health" :style="{ color: healthColor(svc.status || svc.health) }">
              {{ svc.status || svc.health || 'unknown' }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Backups -->
    <div class="section">
      <h3>Backups</h3>
      <DataTable :columns="backupColumns" :rows="backups" :loading="loadingBackups" />
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
.hw-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
}
.hw-card {
  background: #16162a;
  border-radius: 8px;
  padding: 1.2rem;
  display: flex;
  gap: 1rem;
  align-items: flex-start;
}
.hw-icon {
  font-size: 2rem;
}
.hw-info {
  flex: 1;
}
.hw-label {
  font-size: 0.75rem;
  color: #888;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.hw-value {
  font-size: 1rem;
  font-weight: 600;
  color: #eee;
  margin: 0.2rem 0;
}
.hw-detail {
  font-size: 0.8rem;
  color: #aaa;
}
.services-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 0.8rem;
}
.service-card {
  background: #16162a;
  border-radius: 8px;
  padding: 1rem;
  display: flex;
  gap: 0.8rem;
  align-items: center;
}
.service-status {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.service-info {
  flex: 1;
  min-width: 0;
}
.service-name {
  font-weight: 600;
  color: #eee;
  font-size: 0.95rem;
}
.service-detail {
  font-size: 0.8rem;
  color: #888;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.service-health {
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: capitalize;
  margin-top: 0.2rem;
}
</style>
