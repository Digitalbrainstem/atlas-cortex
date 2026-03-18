<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const loading = ref(true)
const error = ref('')
const success = ref('')

// ── Zones ──────────────────────────────────────────────────────
const zones = ref([])
const showCreateZone = ref(false)
const newZone = ref({ name: '', description: '', satellite_ids: '' })
const creating = ref(false)
const editingZone = ref(null)

const zoneColumns = [
  { key: 'id', label: 'ID' },
  { key: 'name', label: 'Name' },
  { key: 'description', label: 'Description' },
  { key: 'satellite_count', label: 'Satellites' },
  { key: 'created_at', label: 'Created' },
]

// ── Calls ──────────────────────────────────────────────────────
const activeCalls = ref([])
const callColumns = [
  { key: 'id', label: 'ID' },
  { key: 'caller_satellite', label: 'Caller' },
  { key: 'callee_satellite', label: 'Callee' },
  { key: 'status', label: 'Status' },
  { key: 'started_at', label: 'Started' },
]

// ── Log ────────────────────────────────────────────────────────
const logs = ref([])
const logColumns = [
  { key: 'id', label: 'ID' },
  { key: 'action', label: 'Action' },
  { key: 'source_room', label: 'Source' },
  { key: 'target', label: 'Target' },
  { key: 'message', label: 'Message' },
  { key: 'created_at', label: 'Time' },
]

// ── Broadcast ──────────────────────────────────────────────────
const broadcastMsg = ref('')
const broadcastZone = ref('')
const broadcasting = ref(false)

async function fetchAll() {
  loading.value = true
  error.value = ''
  try {
    const [zData, cData, lData] = await Promise.all([
      api.get('/admin/intercom/zones'),
      api.get('/admin/intercom/calls'),
      api.get('/admin/intercom/log'),
    ])
    zones.value = (zData.zones || []).map(z => ({
      ...z,
      satellite_count: (z.satellite_ids || []).length,
    }))
    activeCalls.value = cData.calls || []
    logs.value = lData.log || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(fetchAll)

async function createZone() {
  creating.value = true
  error.value = ''
  try {
    const ids = newZone.value.satellite_ids
      .split(',')
      .map(s => s.trim())
      .filter(Boolean)
    await api.post('/admin/intercom/zones', {
      name: newZone.value.name,
      description: newZone.value.description,
      satellite_ids: ids,
    })
    success.value = `Zone "${newZone.value.name}" created`
    showCreateZone.value = false
    newZone.value = { name: '', description: '', satellite_ids: '' }
    fetchAll()
    setTimeout(() => { success.value = '' }, 3000)
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}

async function deleteZone(id) {
  if (!confirm('Delete this zone?')) return
  try {
    await api.delete(`/admin/intercom/zones/${id}`)
    fetchAll()
  } catch (e) {
    error.value = e.message
  }
}

async function endCall(id) {
  try {
    await api.post(`/admin/intercom/calls/${id}/end`)
    fetchAll()
  } catch (e) {
    error.value = e.message
  }
}

async function sendBroadcast() {
  if (!broadcastMsg.value.trim()) return
  broadcasting.value = true
  error.value = ''
  try {
    const payload = { message: broadcastMsg.value }
    if (broadcastZone.value) payload.zone = broadcastZone.value
    const res = await api.post('/admin/intercom/broadcast', payload)
    success.value = `Broadcast sent to ${res.satellites_reached} satellite(s)`
    broadcastMsg.value = ''
    fetchAll()
    setTimeout(() => { success.value = '' }, 3000)
  } catch (e) {
    error.value = e.message
  } finally {
    broadcasting.value = false
  }
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">📢 Intercom &amp; Broadcasting</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <!-- Broadcast Now -->
    <section class="section-card">
      <h3>Broadcast Now</h3>
      <div class="broadcast-row">
        <input v-model="broadcastMsg" class="form-input broadcast-input" placeholder="Message to broadcast…" />
        <input v-model="broadcastZone" class="form-input zone-input" placeholder="Zone (optional)" />
        <button class="btn btn-primary" :disabled="broadcasting || !broadcastMsg.trim()" @click="sendBroadcast">
          {{ broadcasting ? 'Sending…' : '📢 Broadcast' }}
        </button>
      </div>
    </section>

    <!-- Zones -->
    <section class="section-card">
      <div class="header-row">
        <h3>Zones</h3>
        <button class="btn btn-primary" @click="showCreateZone = true">+ Create Zone</button>
      </div>

      <div v-if="showCreateZone" class="modal-overlay" @click.self="showCreateZone = false">
        <div class="modal">
          <h3>Create Zone</h3>
          <div class="form-group">
            <label class="form-label">Name *</label>
            <input v-model="newZone.name" class="form-input" placeholder="upstairs" />
          </div>
          <div class="form-group">
            <label class="form-label">Description</label>
            <input v-model="newZone.description" class="form-input" placeholder="All upstairs rooms" />
          </div>
          <div class="form-group">
            <label class="form-label">Satellite IDs (comma-separated)</label>
            <input v-model="newZone.satellite_ids" class="form-input" placeholder="sat-bedroom, sat-office" />
          </div>
          <div class="modal-actions">
            <button class="btn" @click="showCreateZone = false">Cancel</button>
            <button class="btn btn-primary" :disabled="creating || !newZone.name" @click="createZone">
              {{ creating ? 'Creating…' : 'Create' }}
            </button>
          </div>
        </div>
      </div>

      <table class="data-table" v-if="zones.length">
        <thead>
          <tr>
            <th>ID</th><th>Name</th><th>Description</th><th>Satellites</th><th>Created</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="z in zones" :key="z.id">
            <td>{{ z.id }}</td>
            <td>{{ z.name }}</td>
            <td>{{ z.description }}</td>
            <td>{{ z.satellite_count }}</td>
            <td>{{ z.created_at }}</td>
            <td><button class="btn btn-danger btn-sm" @click="deleteZone(z.id)">Delete</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-text">No zones configured.</p>
    </section>

    <!-- Active Calls -->
    <section class="section-card">
      <h3>Active Calls</h3>
      <table class="data-table" v-if="activeCalls.length">
        <thead>
          <tr>
            <th>ID</th><th>Caller</th><th>Callee</th><th>Status</th><th>Started</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in activeCalls" :key="c.id">
            <td>{{ c.id }}</td>
            <td>{{ c.caller_satellite }}</td>
            <td>{{ c.callee_satellite }}</td>
            <td><span class="badge" :class="c.status">{{ c.status }}</span></td>
            <td>{{ c.started_at }}</td>
            <td><button class="btn btn-danger btn-sm" @click="endCall(c.id)">End</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-text">No active calls.</p>
    </section>

    <!-- Broadcast Log -->
    <section class="section-card">
      <h3>Recent Log</h3>
      <DataTable :columns="logColumns" :rows="logs" :loading="loading" />
    </section>
  </AppLayout>
</template>

<style scoped>
.page-title {
  margin: 0 0 1.5rem;
  font-size: 1.5rem;
  color: #eee;
}
.section-card {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border, #2a2a4a);
  border-radius: 10px;
  padding: 1.25rem;
  margin-bottom: 1.5rem;
}
.section-card h3 {
  margin: 0 0 1rem;
  font-size: 1.1rem;
  color: #ddd;
}
.header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}
.header-row h3 { margin: 0; }
.broadcast-row {
  display: flex;
  gap: 0.75rem;
  align-items: center;
}
.broadcast-input { flex: 1; }
.zone-input { width: 180px; }
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}
.data-table th,
.data-table td {
  padding: 0.6rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.data-table th {
  color: #aaa;
  font-weight: 600;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.data-table td { color: #ccc; }
.badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 600;
}
.badge.ringing { background: rgba(251,191,36,0.2); color: #fbbf24; }
.badge.active { background: rgba(52,211,153,0.2); color: #34d399; }
.empty-text { color: #666; font-size: 0.9rem; }

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
.btn {
  background: #1a1a2e;
  border: 1px solid #2a2a4a;
  color: #ccc;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
}
.btn:hover:not(:disabled) {
  border-color: #646cff;
  color: #fff;
}
.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.btn-primary {
  background: #646cff;
  border-color: #646cff;
  color: #fff;
}
.btn-primary:hover:not(:disabled) { background: #535bf2; }
.btn-danger {
  background: rgba(239, 68, 68, 0.15);
  border-color: rgba(239, 68, 68, 0.4);
  color: #ef4444;
}
.btn-danger:hover:not(:disabled) { background: rgba(239, 68, 68, 0.25); }
.btn-sm { padding: 0.25rem 0.6rem; font-size: 0.8rem; }

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.modal {
  background: #1a1a2e;
  border-radius: 10px;
  padding: 1.5rem;
  width: 440px;
  max-width: 90vw;
}
.modal h3 { margin: 0 0 1rem; color: #eee; }
.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin-bottom: 1rem;
}
.form-label { font-size: 0.8rem; color: #aaa; }
.form-input {
  background: #16162a;
  border: 1px solid #2a2a4a;
  border-radius: 6px;
  padding: 0.6rem 0.8rem;
  color: #eee;
  font-size: 0.9rem;
  outline: none;
}
.form-input:focus { border-color: #646cff; }
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.8rem;
  margin-top: 1rem;
}
</style>
