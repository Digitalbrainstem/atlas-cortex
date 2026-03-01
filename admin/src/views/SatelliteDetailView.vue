<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api.js'

const route = useRoute()
const router = useRouter()
const satellite = ref(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const success = ref('')

// Editable fields
const form = ref({
  display_name: '',
  room: '',
  wake_word: 'hey atlas',
  volume: 0.7,
  mic_gain: 0.8,
  vad_sensitivity: 0.5,
  filler_enabled: true,
  filler_threshold_ms: 1500,
})

// Provision form
const showProvision = ref(false)
const provisionForm = ref({ room: '', display_name: '', ssh_password: 'atlas' })
const provisioning = ref(false)
const provisionSteps = ref([])

const satId = computed(() => route.params.id)

const hardwareInfo = computed(() => {
  if (!satellite.value?.hardware_info) return null
  try { return JSON.parse(satellite.value.hardware_info) } catch { return null }
})

const capabilities = computed(() => {
  if (!satellite.value?.capabilities) return null
  try { return JSON.parse(satellite.value.capabilities) } catch { return null }
})

onMounted(() => fetchSatellite())

async function fetchSatellite() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/admin/satellites/${satId.value}`)
    satellite.value = data
    form.value = {
      display_name: data.display_name || '',
      room: data.room || '',
      wake_word: data.wake_word || 'hey atlas',
      volume: data.volume ?? 0.7,
      mic_gain: data.mic_gain ?? 0.8,
      vad_sensitivity: data.vad_sensitivity ?? 0.5,
      filler_enabled: data.filler_enabled ?? true,
      filler_threshold_ms: data.filler_threshold_ms ?? 1500,
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function saveChanges() {
  saving.value = true
  error.value = ''
  try {
    const updated = await api.patch(`/admin/satellites/${satId.value}`, form.value)
    satellite.value = updated
    success.value = 'Changes saved'
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function provisionSatellite() {
  provisioning.value = true
  provisionSteps.value = []
  error.value = ''
  try {
    const result = await api.post(`/admin/satellites/${satId.value}/provision`, provisionForm.value)
    provisionSteps.value = result.steps || []
    if (result.success) {
      success.value = 'Provisioning complete!'
      showProvision.value = false
      await fetchSatellite()
    } else {
      error.value = `Provisioning failed: ${result.error}`
    }
  } catch (e) {
    error.value = e.message
  } finally {
    provisioning.value = false
  }
}

async function detectHardware() {
  error.value = ''
  try {
    const result = await api.post(`/admin/satellites/${satId.value}/detect`)
    success.value = `Hardware detected: ${result.platform}`
    await fetchSatellite()
  } catch (e) {
    error.value = `Detection failed: ${e.message}`
  }
}

async function restartAgent() {
  try {
    const result = await api.post(`/admin/satellites/${satId.value}/restart`)
    success.value = result.sent ? 'Restart sent' : 'Satellite not connected'
  } catch (e) { error.value = e.message }
}

async function testAudio() {
  try {
    const result = await api.post(`/admin/satellites/${satId.value}/test`)
    success.value = result.sent ? 'Audio test sent' : 'Satellite not connected'
  } catch (e) { error.value = e.message }
}

async function identifySat() {
  try {
    await api.post(`/admin/satellites/${satId.value}/identify`)
    success.value = 'LEDs blinking for 10 seconds'
  } catch (e) { error.value = e.message }
}

async function removeSatellite() {
  if (!confirm('Are you sure you want to remove this satellite?')) return
  try {
    await api.delete(`/admin/satellites/${satId.value}`)
    router.push({ name: 'satellites' })
  } catch (e) { error.value = e.message }
}
</script>

<template>
  <AppLayout>
    <div class="page-header">
      <button class="btn btn-secondary" @click="router.push({ name: 'satellites' })">← Back</button>
      <h1 v-if="satellite">📡 {{ satellite.display_name }}</h1>
      <div class="header-actions" v-if="satellite">
        <button class="btn btn-secondary" @click="identifySat">💡 Identify</button>
        <button
          v-if="satellite.status === 'new' || satellite.status === 'announced'"
          class="btn btn-primary"
          @click="showProvision = true"
        >🚀 Provision</button>
      </div>
    </div>

    <div v-if="error" class="alert alert-error">{{ error }}</div>
    <div v-if="success" class="alert alert-success">{{ success }}</div>
    <div v-if="loading" class="loading">Loading...</div>

    <div v-if="satellite && !loading" class="detail-grid">
      <!-- General -->
      <section class="card">
        <h2>General</h2>
        <div class="form-row">
          <label>Display Name</label>
          <input v-model="form.display_name" />
        </div>
        <div class="form-row">
          <label>Room</label>
          <input v-model="form.room" placeholder="kitchen, bedroom, etc." />
        </div>
        <div class="info-row">
          <span class="label">Mode:</span>
          <span :class="satellite.mode">{{ satellite.mode }}</span>
        </div>
        <div class="info-row"><span class="label">ID:</span> {{ satellite.id }}</div>
        <div class="info-row"><span class="label">Hostname:</span> {{ satellite.hostname || '—' }}</div>
        <div class="info-row"><span class="label">IP:</span> {{ satellite.ip_address }}</div>
        <div class="info-row">
          <span class="label">Status:</span>
          <span class="status-badge" :class="satellite.status">{{ satellite.status }}</span>
        </div>
      </section>

      <!-- Hardware -->
      <section class="card">
        <h2>Hardware
          <button class="btn-sm" @click="detectHardware" title="Re-detect">🔄</button>
        </h2>
        <template v-if="hardwareInfo">
          <div class="info-row"><span class="label">Platform:</span> {{ hardwareInfo.platform?.model || '—' }}</div>
          <div class="info-row"><span class="label">Arch:</span> {{ hardwareInfo.platform?.arch || '—' }}</div>
          <div class="info-row"><span class="label">CPU:</span> {{ hardwareInfo.platform?.cpu_cores || '?' }} cores</div>
          <div class="info-row"><span class="label">RAM:</span> {{ hardwareInfo.platform?.ram_mb || '?' }} MB</div>
          <div class="info-row"><span class="label">Mic:</span> {{ capabilities?.mic ? '✅' : '❌' }}</div>
          <div class="info-row"><span class="label">Speaker:</span> {{ capabilities?.speaker ? '✅' : '❌' }}</div>
          <div class="info-row"><span class="label">LEDs:</span> {{ capabilities?.led_type || 'none' }} ({{ capabilities?.led_count || 0 }})</div>
        </template>
        <p v-else class="muted">No hardware data — click 🔄 to detect</p>
      </section>

      <!-- Audio Settings -->
      <section class="card">
        <h2>Audio Settings</h2>
        <div class="form-row">
          <label>Wake Word</label>
          <input v-model="form.wake_word" />
        </div>
        <div class="form-row">
          <label>Volume ({{ Math.round(form.volume * 100) }}%)</label>
          <input type="range" v-model.number="form.volume" min="0" max="1" step="0.05" />
        </div>
        <div class="form-row">
          <label>Mic Gain ({{ Math.round(form.mic_gain * 100) }}%)</label>
          <input type="range" v-model.number="form.mic_gain" min="0" max="1" step="0.05" />
        </div>
        <div class="form-row">
          <label>VAD Sensitivity ({{ Math.round(form.vad_sensitivity * 100) }}%)</label>
          <input type="range" v-model.number="form.vad_sensitivity" min="0" max="1" step="0.05" />
        </div>
        <div class="form-row">
          <label><input type="checkbox" v-model="form.filler_enabled" /> Enable filler phrases</label>
        </div>
        <div v-if="form.filler_enabled" class="form-row">
          <label>Filler threshold ({{ form.filler_threshold_ms }}ms)</label>
          <input type="range" v-model.number="form.filler_threshold_ms" min="500" max="5000" step="100" />
        </div>
      </section>

      <!-- Actions -->
      <section class="card actions-card">
        <button class="btn btn-primary" @click="saveChanges" :disabled="saving">
          {{ saving ? 'Saving...' : 'Save Changes' }}
        </button>
        <button class="btn btn-secondary" @click="restartAgent">Restart Agent</button>
        <button class="btn btn-secondary" @click="testAudio">Test Audio ▶</button>
        <button class="btn btn-danger" @click="removeSatellite">Remove ✕</button>
      </section>
    </div>

    <!-- Provision Dialog -->
    <div v-if="showProvision" class="modal-overlay" @click.self="showProvision = false">
      <div class="modal">
        <h2>🚀 Provision Satellite</h2>
        <form @submit.prevent="provisionSatellite">
          <div class="form-group">
            <label>Room</label>
            <input v-model="provisionForm.room" required placeholder="kitchen" />
          </div>
          <div class="form-group">
            <label>Display Name</label>
            <input v-model="provisionForm.display_name" placeholder="Kitchen Speaker" />
          </div>
          <div class="form-group">
            <label>SSH Password</label>
            <input v-model="provisionForm.ssh_password" type="password" />
          </div>

          <div v-if="provisionSteps.length" class="provision-steps">
            <div v-for="step in provisionSteps" :key="step.name" class="step" :class="step.status">
              <span class="step-icon">
                {{ step.status === 'done' ? '✅' : step.status === 'running' ? '🔄' : step.status === 'failed' ? '❌' : '◌' }}
              </span>
              {{ step.detail || step.name }}
            </div>
          </div>

          <div class="form-actions">
            <button type="button" class="btn btn-secondary" @click="showProvision = false">Cancel</button>
            <button type="submit" class="btn btn-primary" :disabled="provisioning">
              {{ provisioning ? 'Provisioning...' : 'Start Provisioning' }}
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
  align-items: center;
  gap: 1rem;
  margin-bottom: 1.5rem;
}
.page-header h1 { flex: 1; margin: 0; }
.header-actions { display: flex; gap: 0.5rem; }

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 1rem;
}

.card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem;
}
.card h2 {
  margin: 0 0 1rem;
  font-size: 1rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.info-row {
  display: flex;
  gap: 0.5rem;
  padding: 0.3rem 0;
  font-size: 0.875rem;
}
.info-row .label { color: var(--text-muted); min-width: 80px; }

.form-row {
  margin-bottom: 0.75rem;
}
.form-row label {
  display: block;
  font-size: 0.8rem;
  color: var(--text-secondary);
  margin-bottom: 0.25rem;
}
.form-row input[type="text"],
.form-row input:not([type]) {
  width: 100%;
  padding: 0.4rem 0.5rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-primary);
}
.form-row input[type="range"] { width: 100%; }

.status-badge {
  padding: 0.1rem 0.5rem;
  border-radius: 4px;
  font-size: 0.8rem;
}
.status-badge.online { background: rgba(34, 197, 94, 0.15); color: #86efac; }
.status-badge.offline { background: rgba(107, 114, 128, 0.15); color: #9ca3af; }
.status-badge.announced { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.status-badge.error { background: rgba(239, 68, 68, 0.15); color: #fca5a5; }
.status-badge.provisioning { background: rgba(59, 130, 246, 0.15); color: #93c5fd; }

.actions-card {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: flex-start;
}

.provision-steps {
  margin: 1rem 0;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.75rem;
}
.step {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.25rem 0;
  font-size: 0.85rem;
}
.step.done { color: #86efac; }
.step.running { color: #93c5fd; }
.step.failed { color: #fca5a5; }
.step.pending, .step.skipped { color: var(--text-muted); }

.muted { color: var(--text-muted); font-size: 0.85rem; }

.alert {
  padding: 0.75rem 1rem;
  border-radius: var(--radius);
  margin-bottom: 1rem;
  font-size: 0.9rem;
}
.alert-error { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.alert-success { background: rgba(34, 197, 94, 0.1); border: 1px solid rgba(34, 197, 94, 0.3); color: #86efac; }

.btn-sm {
  padding: 0.2rem 0.4rem;
  font-size: 0.75rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 4px;
  cursor: pointer;
}

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
.form-actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1.5rem; }
</style>
