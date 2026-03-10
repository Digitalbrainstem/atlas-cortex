<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const error = ref('')
const success = ref('')

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

// Voice settings
const voices = ref([])
const loadingVoices = ref(true)
const systemDefaultVoice = ref('')
const selectedVoice = ref('')
const savingVoice = ref(false)
const previewingVoice = ref('')
const regenerating = ref(false)
const previewAudio = ref(null)

onMounted(() => {
  fetchHardware()
  fetchModels()
  fetchServices()
  fetchBackups()
  fetchVoices()
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

async function fetchVoices() {
  loadingVoices.value = true
  try {
    const data = await api.get('/admin/tts/voices')
    voices.value = data.voices || []
    systemDefaultVoice.value = data.system_default || ''
    selectedVoice.value = data.system_default || ''
  } catch (e) {
    error.value = e.message
  } finally {
    loadingVoices.value = false
  }
}

async function saveDefaultVoice() {
  savingVoice.value = true
  regenerating.value = true
  error.value = ''
  success.value = ''
  try {
    await api.put('/admin/tts/default_voice', { voice: selectedVoice.value })
    systemDefaultVoice.value = selectedVoice.value
    success.value = 'Default voice updated — regenerating cached audio…'
    // Poll briefly then clear regenerating indicator
    setTimeout(() => {
      regenerating.value = false
      success.value = 'Default voice updated and cache regenerated'
      setTimeout(() => { success.value = '' }, 4000)
    }, 8000)
  } catch (e) {
    error.value = e.message
    regenerating.value = false
  } finally {
    savingVoice.value = false
  }
}

async function previewVoice(voiceName) {
  if (previewingVoice.value === voiceName) {
    // Stop current preview
    if (previewAudio.value) {
      previewAudio.value.pause()
      previewAudio.value = null
    }
    previewingVoice.value = ''
    return
  }
  previewingVoice.value = voiceName
  try {
    const resp = await fetch('/admin/tts/preview', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('atlas-admin-token')}`,
      },
      body: JSON.stringify({ text: 'Hi there! I\'m Atlas, your home assistant.', voice: voiceName, target: 'browser' }),
    })
    if (!resp.ok) throw new Error('Preview failed')
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    if (previewAudio.value) previewAudio.value.pause()
    const audio = new Audio(url)
    previewAudio.value = audio
    audio.onended = () => {
      previewingVoice.value = ''
      URL.revokeObjectURL(url)
    }
    audio.onerror = () => {
      previewingVoice.value = ''
      URL.revokeObjectURL(url)
    }
    await audio.play()
  } catch (e) {
    error.value = `Preview failed: ${e.message}`
    previewingVoice.value = ''
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
    <div v-if="success" class="success-banner">{{ success }}</div>

    <!-- Voice Settings -->
    <div class="section">
      <h3>🔊 Voice Settings</h3>
      <div v-if="loadingVoices" class="loading-text">Loading voices…</div>
      <div v-else>
        <div class="voice-setting">
          <div class="form-group">
            <label class="form-label">System Default Voice</label>
            <div class="voice-select-row">
              <select v-model="selectedVoice" class="form-input voice-select">
                <option value="">None (use environment default)</option>
                <option v-for="v in voices" :key="v.name" :value="v.name">
                  {{ v.name }} — {{ v.provider }}{{ v.description && v.description !== v.name ? ' (' + v.description + ')' : '' }}
                </option>
              </select>
              <button
                class="btn btn-primary"
                :disabled="savingVoice || selectedVoice === systemDefaultVoice"
                @click="saveDefaultVoice"
              >
                {{ savingVoice ? 'Saving…' : 'Save' }}
              </button>
            </div>
            <div v-if="regenerating" class="regen-banner">
              ⏳ Regenerating fillers &amp; joke audio for new voice…
            </div>
            <div class="form-hint">
              Voice resolution: User preference → System default → Environment variable
            </div>
          </div>
        </div>

        <div v-if="voices.length" class="voice-list">
          <div class="voice-list-header">Available Voices</div>
          <div class="voice-grid">
            <div v-for="v in voices" :key="v.name" class="voice-card" :class="{ active: v.name === systemDefaultVoice }">
              <div class="voice-card-info">
                <div class="voice-card-name">{{ v.name }}</div>
                <div class="voice-card-meta">{{ v.provider }}{{ v.description && v.description !== v.name ? ' · ' + v.description : '' }}</div>
              </div>
              <div class="voice-card-actions">
                <button
                  class="btn btn-sm btn-play"
                  :class="{ playing: previewingVoice === v.name }"
                  @click="previewVoice(v.name)"
                  :title="previewingVoice === v.name ? 'Stop' : 'Preview'"
                >
                  {{ previewingVoice === v.name ? '⏹' : '▶' }}
                </button>
              </div>
            </div>
          </div>
          <div class="voice-count">{{ voices.length }} voices across {{ [...new Set(voices.map(v => v.provider))].join(', ') }}</div>
        </div>
      </div>
    </div>

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
.success-banner {
  background: rgba(66, 184, 131, 0.15);
  border: 1px solid rgba(66, 184, 131, 0.4);
  color: #42b883;
  padding: 0.8rem 1rem;
  border-radius: 8px;
  margin-bottom: 1rem;
}
.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.form-label {
  font-size: 0.8rem;
  color: #aaa;
}
.form-input {
  background: #16162a;
  border: 1px solid #2a2a4a;
  border-radius: 6px;
  padding: 0.6rem 0.8rem;
  color: #eee;
  font-size: 0.9rem;
  outline: none;
}
.form-input:focus {
  border-color: #646cff;
}
.form-hint {
  font-size: 0.75rem;
  color: #666;
  margin-top: 0.2rem;
}
.voice-select-row {
  display: flex;
  gap: 0.8rem;
  align-items: center;
}
.voice-select {
  flex: 1;
  max-width: 500px;
}
.voice-count {
  font-size: 0.8rem;
  color: #888;
  margin-top: 0.8rem;
}
.voice-list {
  margin-top: 1.2rem;
}
.voice-list-header {
  font-size: 0.85rem;
  color: #aaa;
  font-weight: 600;
  margin-bottom: 0.6rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.voice-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 0.6rem;
}
.voice-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #16162a;
  border: 1px solid #2a2a4a;
  border-radius: 8px;
  padding: 0.7rem 1rem;
  transition: border-color 0.15s;
}
.voice-card.active {
  border-color: #646cff;
  background: rgba(100, 108, 255, 0.08);
}
.voice-card-name {
  font-weight: 600;
  color: #eee;
  font-size: 0.9rem;
}
.voice-card-meta {
  font-size: 0.75rem;
  color: #888;
  margin-top: 0.15rem;
}
.btn-play {
  width: 32px;
  height: 32px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.9rem;
  border-radius: 50%;
  background: #2a2a4a;
  color: #ccc;
  border: none;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.btn-play:hover {
  background: #646cff;
  color: #fff;
}
.btn-play.playing {
  background: #dc3545;
  color: #fff;
}
.regen-banner {
  background: rgba(100, 108, 255, 0.12);
  border: 1px solid rgba(100, 108, 255, 0.3);
  color: #a0a8ff;
  padding: 0.6rem 0.8rem;
  border-radius: 6px;
  font-size: 0.85rem;
  margin-top: 0.5rem;
  animation: pulse-border 1.5s infinite;
}
@keyframes pulse-border {
  0%, 100% { border-color: rgba(100, 108, 255, 0.3); }
  50% { border-color: rgba(100, 108, 255, 0.7); }
}
.btn {
  border: none;
  border-radius: 6px;
  padding: 0.6rem 1.2rem;
  cursor: pointer;
  font-size: 0.9rem;
  font-weight: 600;
}
.btn-primary {
  background: #646cff;
  color: #fff;
}
.btn-primary:hover:not(:disabled) {
  background: #535bf2;
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
