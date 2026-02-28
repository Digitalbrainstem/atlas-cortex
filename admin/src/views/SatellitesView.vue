<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const router = useRouter()
const satellites = ref([])
const announcedCount = ref(0)
const loading = ref(true)
const error = ref('')
const success = ref('')

// Add dialog
const showAddDialog = ref(false)
const addForm = ref({
  ip_address: '',
  mode: 'dedicated',
  ssh_username: 'atlas',
  ssh_password: 'atlas-setup',
  service_port: 5110,
})
const addLoading = ref(false)

// Scanning
const scanning = ref(false)
const scanResults = ref([])

const columns = [
  { key: 'status_icon', label: '' },
  { key: 'display_name', label: 'Name' },
  { key: 'platform', label: 'Platform' },
  { key: 'room', label: 'Room' },
  { key: 'ip_address', label: 'IP Address' },
  { key: 'mode_badge', label: 'Mode' },
  { key: 'status', label: 'Status' },
]

const tableData = computed(() =>
  satellites.value.map(s => ({
    ...s,
    status_icon: s.status === 'online' ? '●' : s.status === 'announced' ? '🆕' : s.status === 'offline' ? '○' : '◌',
    mode_badge: s.mode === 'shared' ? '🔗 Shared' : '',
    platform: s.platform || '—',
    room: s.room || 'Unassigned',
  }))
)

onMounted(() => fetchSatellites())

async function fetchSatellites() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/admin/satellites')
    satellites.value = data.satellites || []
    announcedCount.value = data.announced_count || 0
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function scanNow() {
  scanning.value = true
  error.value = ''
  try {
    const data = await api.post('/admin/satellites/discover')
    scanResults.value = data.found || []
    success.value = `Scan complete: ${data.count} device(s) found`
    await fetchSatellites()
  } catch (e) {
    error.value = `Scan failed: ${e.message}`
  } finally {
    scanning.value = false
  }
}

async function addSatellite() {
  addLoading.value = true
  error.value = ''
  try {
    const sat = await api.post('/admin/satellites/add', addForm.value)
    success.value = `Added satellite: ${sat.id}`
    showAddDialog.value = false
    addForm.value = { ip_address: '', mode: 'dedicated', ssh_username: 'atlas', ssh_password: 'atlas-setup', service_port: 5110 }
    await fetchSatellites()
  } catch (e) {
    error.value = `Failed to add: ${e.message}`
  } finally {
    addLoading.value = false
  }
}

function viewSatellite(row) {
  router.push({ name: 'satellite-detail', params: { id: row.id } })
}

async function identifySatellite(id) {
  try {
    await api.post(`/admin/satellites/${id}/identify`)
    success.value = 'Identify command sent — LEDs blinking for 10s'
  } catch (e) {
    error.value = e.message
  }
}

async function removeSatellite(id) {
  if (!confirm('Remove this satellite?')) return
  try {
    await api.delete(`/admin/satellites/${id}`)
    success.value = 'Satellite removed'
    await fetchSatellites()
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <AppLayout>
    <div class="page-header">
      <h1>📡 Satellites</h1>
      <div class="header-actions">
        <button class="btn btn-secondary" @click="scanNow" :disabled="scanning">
          {{ scanning ? '🔄 Scanning...' : '🔍 Scan Now' }}
        </button>
        <button class="btn btn-primary" @click="showAddDialog = true">+ Add Manual</button>
      </div>
    </div>

    <div v-if="error" class="alert alert-error">{{ error }}</div>
    <div v-if="success" class="alert alert-success">{{ success }}</div>

    <div v-if="announcedCount > 0" class="alert alert-info">
      🔔 {{ announcedCount }} new satellite(s) announced — click to configure
    </div>

    <div v-if="loading" class="loading">Loading satellites...</div>
    <div v-else-if="satellites.length === 0" class="empty-state">
      <p>No satellites registered yet.</p>
      <p class="muted">Satellites will appear here when they announce via mDNS, or add one manually.</p>
    </div>
    <div v-else class="satellite-grid">
      <div
        v-for="sat in satellites"
        :key="sat.id"
        class="satellite-card"
        :class="{ online: sat.status === 'online', offline: sat.status === 'offline', announced: sat.status === 'announced' }"
        @click="viewSatellite(sat)"
      >
        <div class="card-header">
          <span class="status-dot" :class="sat.status"></span>
          <h3>{{ sat.display_name }}</h3>
          <span v-if="sat.mode === 'shared'" class="badge shared">Shared</span>
        </div>
        <div class="card-body">
          <div class="card-detail"><span class="label">Room:</span> {{ sat.room || 'Unassigned' }}</div>
          <div class="card-detail"><span class="label">Platform:</span> {{ sat.platform || 'Unknown' }}</div>
          <div class="card-detail"><span class="label">IP:</span> {{ sat.ip_address }}</div>
          <div class="card-detail"><span class="label">Status:</span> {{ sat.status }}</div>
        </div>
        <div class="card-actions" @click.stop>
          <button class="btn-sm" @click="identifySatellite(sat.id)" title="Blink LEDs">💡</button>
          <button class="btn-sm danger" @click="removeSatellite(sat.id)" title="Remove">✕</button>
        </div>
      </div>
    </div>

    <!-- Add Dialog -->
    <div v-if="showAddDialog" class="modal-overlay" @click.self="showAddDialog = false">
      <div class="modal">
        <h2>Add Satellite</h2>
        <form @submit.prevent="addSatellite">
          <div class="form-group">
            <label>Mode</label>
            <div class="radio-group">
              <label><input type="radio" v-model="addForm.mode" value="dedicated"> Dedicated — Atlas manages the device</label>
              <label><input type="radio" v-model="addForm.mode" value="shared"> Shared — Add to existing host</label>
            </div>
          </div>
          <div class="form-group">
            <label>IP Address / Hostname</label>
            <input v-model="addForm.ip_address" required placeholder="192.168.3.100" />
          </div>
          <div class="form-group">
            <label>SSH Username</label>
            <input v-model="addForm.ssh_username" placeholder="atlas" />
          </div>
          <div class="form-group">
            <label>SSH Password</label>
            <input v-model="addForm.ssh_password" type="password" placeholder="atlas-setup" />
          </div>
          <div v-if="addForm.mode === 'shared'" class="form-group">
            <label>Satellite Port</label>
            <input v-model.number="addForm.service_port" type="number" placeholder="5110" />
          </div>
          <div class="form-actions">
            <button type="button" class="btn btn-secondary" @click="showAddDialog = false">Cancel</button>
            <button type="submit" class="btn btn-primary" :disabled="addLoading">
              {{ addLoading ? 'Connecting...' : 'Connect & Detect Hardware' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.5rem;
}
.header-actions { display: flex; gap: 0.5rem; }

.satellite-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 1rem;
}

.satellite-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.satellite-card:hover {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent);
}
.satellite-card.online { border-left: 3px solid #22c55e; }
.satellite-card.offline { border-left: 3px solid #6b7280; }
.satellite-card.announced { border-left: 3px solid #f59e0b; }

.card-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.card-header h3 { margin: 0; font-size: 1rem; flex: 1; }

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.status-dot.online { background: #22c55e; }
.status-dot.offline { background: #6b7280; }
.status-dot.announced { background: #f59e0b; }
.status-dot.error { background: #ef4444; }
.status-dot.new { background: #a855f7; }
.status-dot.provisioning { background: #3b82f6; animation: pulse 1s infinite; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.badge.shared {
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  border-radius: 4px;
}

.card-body { display: flex; flex-direction: column; gap: 0.25rem; }
.card-detail { font-size: 0.85rem; color: var(--text-secondary); }
.card-detail .label { color: var(--text-muted); }

.card-actions {
  display: flex;
  gap: 0.25rem;
  margin-top: 0.75rem;
  justify-content: flex-end;
}
.btn-sm {
  padding: 0.25rem 0.5rem;
  font-size: 0.8rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 4px;
  cursor: pointer;
}
.btn-sm:hover { background: rgba(255,255,255,0.05); }
.btn-sm.danger:hover { background: rgba(239, 68, 68, 0.1); color: var(--danger); }

.empty-state {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--text-secondary);
}
.empty-state .muted { color: var(--text-muted); font-size: 0.85rem; }

.alert {
  padding: 0.75rem 1rem;
  border-radius: var(--radius);
  margin-bottom: 1rem;
  font-size: 0.9rem;
}
.alert-info { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.3); }
.alert-error { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.alert-success { background: rgba(34, 197, 94, 0.1); border: 1px solid rgba(34, 197, 94, 0.3); color: #86efac; }

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1100;
}
.modal {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.5rem;
  width: 90%;
  max-width: 500px;
}
.modal h2 { margin: 0 0 1rem; }
.form-group { margin-bottom: 1rem; }
.form-group label { display: block; margin-bottom: 0.25rem; font-size: 0.85rem; color: var(--text-secondary); }
.form-group input {
  width: 100%;
  padding: 0.5rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-primary);
}
.radio-group { display: flex; flex-direction: column; gap: 0.5rem; }
.radio-group label { font-size: 0.85rem; cursor: pointer; display: flex; align-items: center; gap: 0.5rem; }
.form-actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1.5rem; }
</style>
