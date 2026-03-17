<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api.js'

const activeTab = ref('alarms')
const loading = ref(true)
const error = ref('')
const success = ref('')

// Data
const alarms = ref([])
const timers = ref([])
const reminders = ref([])

// New alarm form
const newAlarm = ref({ label: '', cron_expression: '', sound: 'default', tts_message: '' })
const creating = ref(false)

// Auto-refresh for timers
let refreshInterval = null

onMounted(() => {
  fetchAll()
  refreshInterval = setInterval(() => {
    if (activeTab.value === 'timers') fetchTimers()
  }, 5000)
})

onUnmounted(() => {
  if (refreshInterval) clearInterval(refreshInterval)
})

async function fetchAll() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([fetchAlarms(), fetchTimers(), fetchReminders()])
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function fetchAlarms() {
  const data = await api.get('/admin/scheduling/alarms')
  alarms.value = data.alarms || []
}

async function fetchTimers() {
  const data = await api.get('/admin/scheduling/timers')
  timers.value = data.timers || []
}

async function fetchReminders() {
  const data = await api.get('/admin/scheduling/reminders')
  reminders.value = data.reminders || []
}

async function createAlarm() {
  if (!newAlarm.value.cron_expression) {
    error.value = 'Cron expression is required'
    return
  }
  creating.value = true
  error.value = ''
  success.value = ''
  try {
    await api.post('/admin/scheduling/alarms', newAlarm.value)
    newAlarm.value = { label: '', cron_expression: '', sound: 'default', tts_message: '' }
    await fetchAlarms()
    success.value = 'Alarm created'
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}

async function deleteAlarm(id) {
  error.value = ''
  try {
    await api.delete(`/admin/scheduling/alarms/${id}`)
    await fetchAlarms()
    success.value = 'Alarm deleted'
  } catch (e) {
    error.value = e.message
  }
}

async function toggleAlarm(alarm) {
  error.value = ''
  const action = alarm.enabled ? 'disable' : 'enable'
  try {
    await api.post(`/admin/scheduling/alarms/${alarm.id}/${action}`)
    alarm.enabled = alarm.enabled ? 0 : 1
    success.value = `Alarm ${action}d`
  } catch (e) {
    error.value = e.message
  }
}

async function cancelTimer(id) {
  error.value = ''
  try {
    await api.delete(`/admin/scheduling/timers/${id}`)
    await fetchTimers()
    success.value = 'Timer cancelled'
  } catch (e) {
    error.value = e.message
  }
}

async function deleteReminder(id) {
  error.value = ''
  try {
    await api.delete(`/admin/scheduling/reminders/${id}`)
    await fetchReminders()
    success.value = 'Reminder deleted'
  } catch (e) {
    error.value = e.message
  }
}

function formatRemaining(seconds) {
  if (!seconds || seconds <= 0) return '0s'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">⏰ Scheduling</h2>

    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <!-- Tabs -->
    <div class="tabs">
      <button
        v-for="tab in ['alarms', 'timers', 'reminders']"
        :key="tab"
        class="tab-btn"
        :class="{ active: activeTab === tab }"
        @click="activeTab = tab"
      >
        {{ tab === 'alarms' ? '⏰ Alarms' : tab === 'timers' ? '⏱️ Timers' : '📌 Reminders' }}
      </button>
    </div>

    <div v-if="loading" class="loading-text">Loading…</div>

    <!-- Alarms Tab -->
    <div v-else-if="activeTab === 'alarms'">
      <div class="section-header">
        <h3>Alarms</h3>
      </div>

      <div class="add-form">
        <h4>Add Alarm</h4>
        <div class="form-row">
          <input v-model="newAlarm.label" placeholder="Label" class="input" />
          <input v-model="newAlarm.cron_expression" placeholder="Cron (e.g. 0 7 * * *)" class="input" />
          <input v-model="newAlarm.tts_message" placeholder="TTS message (optional)" class="input" />
          <button class="btn btn-primary" :disabled="creating" @click="createAlarm">
            {{ creating ? 'Creating…' : '+ Add' }}
          </button>
        </div>
      </div>

      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Label</th>
            <th>Cron</th>
            <th>Next Fire</th>
            <th>Enabled</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!alarms.length">
            <td colspan="6" class="loading-text">No alarms</td>
          </tr>
          <tr v-for="a in alarms" :key="a.id">
            <td>{{ a.id }}</td>
            <td>{{ a.label || '—' }}</td>
            <td><code>{{ a.cron_expression }}</code></td>
            <td>{{ a.next_fire || '—' }}</td>
            <td>
              <label class="toggle" @click.stop>
                <input type="checkbox" :checked="a.enabled" @change="toggleAlarm(a)" />
                <span class="toggle-label">{{ a.enabled ? 'On' : 'Off' }}</span>
              </label>
            </td>
            <td>
              <button class="btn btn-sm btn-danger" @click="deleteAlarm(a.id)">🗑️ Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Timers Tab -->
    <div v-else-if="activeTab === 'timers'">
      <div class="section-header">
        <h3>Active Timers</h3>
        <button class="btn btn-sm" @click="fetchTimers">🔄 Refresh</button>
      </div>

      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Label</th>
            <th>Remaining</th>
            <th>State</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!timers.length">
            <td colspan="5" class="loading-text">No active timers</td>
          </tr>
          <tr v-for="t in timers" :key="t.id">
            <td>{{ t.id }}</td>
            <td>{{ t.label || '—' }}</td>
            <td>{{ formatRemaining(t.remaining_seconds) }}</td>
            <td>
              <span class="state-badge" :class="t.state">{{ t.state }}</span>
            </td>
            <td>
              <button class="btn btn-sm btn-danger" @click="cancelTimer(t.id)">✕ Cancel</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Reminders Tab -->
    <div v-else-if="activeTab === 'reminders'">
      <div class="section-header">
        <h3>Reminders</h3>
      </div>

      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Message</th>
            <th>Type</th>
            <th>Trigger</th>
            <th>Fired</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!reminders.length">
            <td colspan="6" class="loading-text">No reminders</td>
          </tr>
          <tr v-for="r in reminders" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.message }}</td>
            <td>{{ r.trigger_type }}</td>
            <td>{{ r.trigger_at || r.cron_expression || r.event_condition || '—' }}</td>
            <td>
              <span :class="r.fired ? 'fired-yes' : 'fired-no'">{{ r.fired ? 'Yes' : 'No' }}</span>
            </td>
            <td>
              <button class="btn btn-sm btn-danger" @click="deleteReminder(r.id)">🗑️ Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </AppLayout>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 1.5rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0;
}

.tab-btn {
  padding: 0.6rem 1.2rem;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 0.9rem;
  transition: color 0.15s, border-color 0.15s;
}

.tab-btn:hover {
  color: var(--text-primary);
}

.tab-btn.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.section-header h3 { margin: 0; }

.add-form {
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem;
  margin-bottom: 1.5rem;
}

.add-form h4 {
  margin: 0 0 0.75rem 0;
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.form-row {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}

.input {
  padding: 0.4rem 0.6rem;
  background: var(--bg-primary);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 0.85rem;
  flex: 1;
  min-width: 120px;
}

.state-badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius);
  font-size: 0.8rem;
  font-weight: 500;
}

.state-badge.running { background: rgba(34,197,94,0.15); color: #22c55e; }
.state-badge.paused { background: rgba(245,158,11,0.15); color: #f59e0b; }

.fired-yes { color: var(--text-muted); }
.fired-no { color: #22c55e; }

.toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  cursor: pointer;
}
.toggle input { cursor: pointer; }
.toggle-label { font-size: 0.85rem; }

.btn-danger {
  color: #ef4444;
  border-color: rgba(239,68,68,0.3);
}
.btn-danger:hover {
  background: rgba(239,68,68,0.1);
}

.btn-sm {
  font-size: 0.8rem;
  padding: 0.25rem 0.5rem;
}

code {
  font-family: 'Fira Code', 'Cascadia Code', monospace;
  font-size: 0.8rem;
  background: rgba(255,255,255,0.05);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
}
</style>
