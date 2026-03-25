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
  vad_enabled: true,
  filler_enabled: true,
  filler_threshold_ms: 1500,
  tts_voice: '',
  led_brightness: 1.0,
  audio_device_out: '',
  button_mode: 'press',
})

// Provision form
const showProvision = ref(false)
const provisionForm = ref({ room: '', display_name: '', ssh_password: 'atlas' })
const provisioning = ref(false)
const provisionSteps = ref([])

const satId = computed(() => route.params.id)

// Debounced live push for sliders
let _volumeTimer = null
function onVolumeInput() {
  clearTimeout(_volumeTimer)
  _volumeTimer = setTimeout(() => {
    api.post(`/admin/satellites/${satId.value}/command`, { action: 'volume', params: { level: form.value.volume } }).catch(() => {})
  }, 150)
}

let _brightnessTimer = null
function onBrightnessInput() {
  clearTimeout(_brightnessTimer)
  _brightnessTimer = setTimeout(() => {
    api.patch(`/admin/satellites/${satId.value}`, { led_brightness: form.value.led_brightness }).catch(() => {})
  }, 150)
}

function onVadToggle() {
  api.patch(`/admin/satellites/${satId.value}`, { vad_enabled: form.value.vad_enabled }).catch(() => {})
}

function onVoiceChange() {
  api.patch(`/admin/satellites/${satId.value}`, { tts_voice: form.value.tts_voice }).catch(() => {})
}

// LED configuration
const ledPatterns = ref({})
const ledLoading = ref(false)
const showAddPattern = ref(false)
const newPatternName = ref('')

const ledStates = ['idle', 'listening', 'thinking', 'speaking', 'error', 'muted', 'wakeword']

// TTS Preview
const ttsText = ref('Hello, I am Atlas. How can I help you today?')
const ttsPlaying = ref(false)
const ttsVoices = ref([])
const selectedVoice = ref('')

// Filler preview
const fillerSentiment = ref('greeting')
const fillerPlaying = ref(false)

async function fetchTtsVoices() {
  try {
    const data = await api.get('/admin/tts/voices')
    ttsVoices.value = data.voices || []
    // Sync selected preview voice with satellite's default
    if (form.value.tts_voice) selectedVoice.value = form.value.tts_voice
  } catch { /* ignore */ }
}

async function playTtsPreview(target) {
  ttsPlaying.value = true
  try {
    if (target === 'browser') {
      const resp = await fetch(`/admin/tts/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Basic ' + btoa('admin:atlas-admin') },
        body: JSON.stringify({ text: ttsText.value, voice: selectedVoice.value || undefined, target: 'browser' })
      })
      if (!resp.ok) throw new Error(await resp.text())
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => { URL.revokeObjectURL(url); ttsPlaying.value = false }
      audio.play()
      return
    }
    // Push to satellite
    await api.post('/admin/tts/preview', { text: ttsText.value, voice: selectedVoice.value || undefined, target: satId.value })
    success.value = 'Audio sent to satellite'
  } catch (e) { error.value = e.message }
  ttsPlaying.value = false
}

async function playFiller(target) {
  fillerPlaying.value = true
  try {
    if (target === 'browser') {
      const resp = await fetch(`/admin/tts/filler_preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Basic ' + btoa('admin:atlas-admin') },
        body: JSON.stringify({ sentiment: fillerSentiment.value, target: 'browser' })
      })
      if (!resp.ok) throw new Error(await resp.text())
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => { URL.revokeObjectURL(url); fillerPlaying.value = false }
      audio.play()
      return
    }
    const result = await api.post('/admin/tts/filler_preview', { sentiment: fillerSentiment.value, target: satId.value })
    success.value = `Filler sent: "${result.filler}"`
  } catch (e) { error.value = e.message }
  fillerPlaying.value = false
}

async function fetchLedConfig() {
  try {
    const data = await api.get(`/admin/satellites/${satId.value}/led_config`)
    ledPatterns.value = data.patterns || {}
  } catch { /* use defaults */ }
}

function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map(x => Math.max(0, Math.min(255, x)).toString(16).padStart(2, '0')).join('')
}

function hexToRgb(hex) {
  const m = hex.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i)
  return m ? { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) } : { r: 0, g: 0, b: 0 }
}

function patternColor(name) {
  const p = ledPatterns.value[name]
  return p ? rgbToHex(p.r, p.g, p.b) : '#000000'
}

function patternBrightness(name) {
  return ledPatterns.value[name]?.brightness ?? 0.4
}

let _ledTimer = null
function onLedChange(name, hex, brightness) {
  const rgb = hexToRgb(hex)
  ledPatterns.value[name] = { ...rgb, brightness: parseFloat(brightness) }
  clearTimeout(_ledTimer)
  _ledTimer = setTimeout(() => {
    api.patch(`/admin/satellites/${satId.value}/led_config`, {
      patterns: { [name]: ledPatterns.value[name] }
    }).catch(() => {})
  }, 200)
}

function previewPattern(name) {
  api.post(`/admin/satellites/${satId.value}/command`, { action: 'led', params: { pattern: name } }).catch(() => {})
  setTimeout(() => {
    api.post(`/admin/satellites/${satId.value}/command`, { action: 'led', params: { pattern: 'idle' } }).catch(() => {})
  }, 2000)
}

function addCustomPattern() {
  const name = newPatternName.value.trim().toLowerCase().replace(/\s+/g, '_')
  if (!name) return
  ledPatterns.value[name] = { r: 100, g: 100, b: 255, brightness: 0.4 }
  api.patch(`/admin/satellites/${satId.value}/led_config`, {
    patterns: { [name]: ledPatterns.value[name] }
  }).catch(() => {})
  newPatternName.value = ''
  showAddPattern.value = false
}

function removePattern(name) {
  if (ledStates.includes(name)) return
  delete ledPatterns.value[name]
  api.patch(`/admin/satellites/${satId.value}/led_config`, {
    patterns: { [name]: { r: 0, g: 0, b: 0, brightness: 0 } }
  }).catch(() => {})
}

const hardwareInfo = computed(() => {
  if (!satellite.value?.hardware_info) return null
  try { return JSON.parse(satellite.value.hardware_info) } catch { return null }
})

const capabilities = computed(() => {
  if (!satellite.value?.capabilities) return null
  try { return JSON.parse(satellite.value.capabilities) } catch { return null }
})

onMounted(() => { fetchSatellite(); fetchLedConfig(); fetchTtsVoices(); loadCommandHistory() })

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
      vad_enabled: data.vad_enabled ?? true,
      filler_enabled: data.filler_enabled ?? true,
      filler_threshold_ms: data.filler_threshold_ms ?? 1500,
      tts_voice: data.tts_voice || '',
      led_brightness: data.led_brightness ?? 1.0,
      audio_device_out: data.audio_device_out || '',
      button_mode: data.button_mode || 'press',
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

// ── Remote Management ──
const remoteScript = ref('')
const remoteScriptTimeout = ref(30)
const remoteScriptRunning = ref(false)
const remoteScriptResult = ref(null)
const configKey = ref('')
const configValue = ref('')
const kioskUrl = ref('')
const commandHistory = ref([])
const loadingHistory = ref(false)

async function sendRemoteCommand(type, payload = {}) {
  error.value = ''
  try {
    const result = await api.post(`/admin/satellites/${satId.value}/command`, { type, payload })
    success.value = `${type} command ${result.status === 'sent' ? 'sent' : 'queued'} (ID: ${result.id})`
    await loadCommandHistory()
    return result
  } catch (e) { error.value = e.message }
}

async function runScript() {
  if (!remoteScript.value.trim()) return
  remoteScriptRunning.value = true
  remoteScriptResult.value = null
  try {
    await sendRemoteCommand('EXEC_SCRIPT', {
      script: remoteScript.value,
      timeout: remoteScriptTimeout.value,
    })
  } finally { remoteScriptRunning.value = false }
}

async function pushConfig() {
  if (!configKey.value.trim()) return
  let val = configValue.value
  try { val = JSON.parse(val) } catch { /* keep as string */ }
  await sendRemoteCommand('CONFIG_UPDATE', { [configKey.value]: val })
  configKey.value = ''
  configValue.value = ''
}

async function setKioskUrl() {
  if (!kioskUrl.value.trim()) return
  await sendRemoteCommand('KIOSK_URL', { url: kioskUrl.value })
}

async function updateAgent() {
  if (!confirm('Pull latest code, reinstall, and restart agent?')) return
  await sendRemoteCommand('UPDATE_AGENT')
}

async function rebootDevice() {
  if (!confirm('Are you sure you want to reboot this device?')) return
  await sendRemoteCommand('REBOOT')
}

async function requestLogs() {
  await sendRemoteCommand('LOG_REQUEST', { lines: 200 })
}

async function loadCommandHistory() {
  loadingHistory.value = true
  try {
    const result = await api.get(`/admin/satellites/${satId.value}/commands?limit=20`)
    commandHistory.value = result.commands || []
  } catch { /* ignore */ }
  finally { loadingHistory.value = false }
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
          <input type="range" v-model.number="form.volume" min="0" max="1" step="0.05" @input="onVolumeInput" />
        </div>
        <div class="form-row">
          <label>Mic Gain ({{ Math.round(form.mic_gain * 100) }}%)</label>
          <input type="range" v-model.number="form.mic_gain" min="0" max="1" step="0.05" />
        </div>
        <div class="form-row" v-if="capabilities?.playback_devices?.length > 1">
          <label>Audio Output</label>
          <select v-model="form.audio_device_out">
            <option value="">Default</option>
            <option v-for="d in capabilities.playback_devices" :key="d.alsa_id" :value="d.alsa_id">{{ d.name }} ({{ d.alsa_id }})</option>
          </select>
          <p class="hint">Requires satellite restart to take effect</p>
        </div>
        <div class="form-row">
          <label>Button Mode</label>
          <select v-model="form.button_mode">
            <option value="press">Press (tap to start, auto-stop on silence)</option>
            <option value="toggle">Toggle (tap to start, tap to stop)</option>
            <option value="hold">Hold (hold to talk, release to stop)</option>
          </select>
        </div>
        <div class="form-row toggle-row">
          <label><input type="checkbox" v-model="form.vad_enabled" @change="onVadToggle" /> Enable VAD (Voice Activity Detection)</label>
        </div>
        <div v-if="form.vad_enabled" class="form-row">
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
        <div class="form-row" v-if="ttsVoices.length">
          <label>Default Voice</label>
          <select v-model="form.tts_voice" @change="onVoiceChange">
            <option value="">Service Default</option>
            <option v-for="v in ttsVoices" :key="v.name" :value="v.name">{{ v.name }}</option>
          </select>
          <p class="hint">Fillers and responses will use this voice</p>
        </div>
      </section>

      <!-- LED Configuration -->
      <section class="card led-card" v-if="capabilities?.led_type && capabilities.led_type !== 'none'">
        <h2>💡 LED Patterns
          <button class="btn-sm" @click="showAddPattern = true" title="Add custom pattern">＋</button>
        </h2>
        <div class="form-row">
          <label>Master Brightness ({{ Math.round(form.led_brightness * 100) }}%)</label>
          <input type="range" v-model.number="form.led_brightness" min="0" max="1" step="0.05" @input="onBrightnessInput" />
        </div>
        <div class="led-grid">
          <div v-for="name in Object.keys(ledPatterns)" :key="name" class="led-row">
            <div class="led-preview" :style="{ backgroundColor: patternColor(name), opacity: patternBrightness(name) || 0.1 }" @click="previewPattern(name)" title="Click to preview"></div>
            <div class="led-name">{{ name }}</div>
            <input type="color" :value="patternColor(name)" @input="e => onLedChange(name, e.target.value, patternBrightness(name))" />
            <div class="led-brightness">
              <input type="range" :value="patternBrightness(name)" min="0" max="1" step="0.05" @input="e => onLedChange(name, patternColor(name), e.target.value)" />
              <span>{{ Math.round(patternBrightness(name) * 100) }}%</span>
            </div>
            <button v-if="!ledStates.includes(name)" class="btn-sm btn-danger-sm" @click="removePattern(name)">✕</button>
          </div>
        </div>

        <div v-if="showAddPattern" class="add-pattern-row">
          <input v-model="newPatternName" placeholder="Pattern name" @keyup.enter="addCustomPattern" />
          <button class="btn-sm" @click="addCustomPattern">Add</button>
          <button class="btn-sm" @click="showAddPattern = false">Cancel</button>
        </div>
      </section>

      <!-- Voice Preview -->
      <section class="card voice-card">
        <h2>🗣️ Voice Preview</h2>
        <div class="form-row">
          <label>Text</label>
          <textarea v-model="ttsText" rows="2" class="tts-input"></textarea>
        </div>
        <div class="form-row" v-if="ttsVoices.length">
          <label>Preview Voice</label>
          <select v-model="selectedVoice">
            <option value="">{{ form.tts_voice ? `Satellite Default (${form.tts_voice})` : 'Service Default' }}</option>
            <option v-for="v in ttsVoices" :key="v.name" :value="v.name">{{ v.name }}</option>
          </select>
        </div>
        <div class="btn-row">
          <button class="btn btn-secondary" @click="playTtsPreview('browser')" :disabled="ttsPlaying">
            {{ ttsPlaying ? '⏳' : '🔊' }} Play in Browser
          </button>
          <button class="btn btn-secondary" @click="playTtsPreview('satellite')" :disabled="ttsPlaying">
            📡 Play on Satellite
          </button>
        </div>
      </section>

      <!-- Filler Phrases -->
      <section class="card">
        <h2>💬 Filler Phrases</h2>
        <div class="form-row">
          <label>Sentiment</label>
          <select v-model="fillerSentiment">
            <option value="greeting">Greeting</option>
            <option value="question">Question</option>
            <option value="excited">Excited</option>
            <option value="frustrated">Frustrated</option>
            <option value="late_night">Late Night</option>
          </select>
        </div>
        <div class="btn-row">
          <button class="btn btn-secondary" @click="playFiller('browser')" :disabled="fillerPlaying">
            🔊 Preview in Browser
          </button>
          <button class="btn btn-secondary" @click="playFiller('satellite')" :disabled="fillerPlaying">
            📡 Play on Satellite
          </button>
        </div>
      </section>

      <!-- Remote Management -->
      <section class="card remote-mgmt-card" style="grid-column: 1 / -1;">
        <h2>🛰️ Remote Management</h2>

        <!-- Quick Actions -->
        <div class="mgmt-section">
          <h3>Quick Actions</h3>
          <div class="btn-row">
            <button class="btn btn-secondary" @click="updateAgent">⬆️ Update Agent</button>
            <button class="btn btn-secondary" @click="sendRemoteCommand('RESTART_SERVICE', { service: 'atlas-satellite' })">🔄 Restart Service</button>
            <button class="btn btn-secondary" @click="requestLogs">📋 Request Logs</button>
            <button class="btn btn-danger" @click="rebootDevice">⚡ Reboot Device</button>
          </div>
        </div>

        <!-- Script Runner -->
        <div class="mgmt-section">
          <h3>Script Runner</h3>
          <textarea v-model="remoteScript" rows="3" class="tts-input" placeholder="echo 'hello from satellite'"></textarea>
          <div class="btn-row" style="align-items: center;">
            <label style="white-space: nowrap;">Timeout:
              <input type="number" v-model.number="remoteScriptTimeout" min="1" max="300" style="width: 60px; padding: 0.3rem;" /> s
            </label>
            <button class="btn btn-primary" @click="runScript" :disabled="remoteScriptRunning || !remoteScript.trim()">
              {{ remoteScriptRunning ? '⏳ Running...' : '▶ Run' }}
            </button>
          </div>
        </div>

        <!-- Config Push -->
        <div class="mgmt-section">
          <h3>Push Config</h3>
          <div class="btn-row">
            <input v-model="configKey" placeholder="key (e.g. volume)" style="flex: 1; padding: 0.4rem;" />
            <input v-model="configValue" placeholder="value (e.g. 0.8)" style="flex: 1; padding: 0.4rem;" />
            <button class="btn btn-secondary" @click="pushConfig" :disabled="!configKey.trim()">Push</button>
          </div>
        </div>

        <!-- Kiosk URL -->
        <div class="mgmt-section">
          <h3>Kiosk URL</h3>
          <div class="btn-row">
            <input v-model="kioskUrl" placeholder="https://homeassistant.local/dashboard" style="flex: 1; padding: 0.4rem;" />
            <button class="btn btn-secondary" @click="setKioskUrl" :disabled="!kioskUrl.trim()">Set URL</button>
          </div>
        </div>

        <!-- Command History -->
        <div class="mgmt-section">
          <h3>Command History
            <button class="btn-sm" @click="loadCommandHistory" :disabled="loadingHistory">🔄</button>
          </h3>
          <div v-if="commandHistory.length" class="cmd-history">
            <table class="cmd-table">
              <thead><tr><th>ID</th><th>Type</th><th>Status</th><th>Result</th><th>Time</th></tr></thead>
              <tbody>
                <tr v-for="cmd in commandHistory" :key="cmd.id">
                  <td>{{ cmd.id }}</td>
                  <td>{{ cmd.command_type }}</td>
                  <td><span class="status-badge" :class="cmd.status">{{ cmd.status }}</span></td>
                  <td class="result-cell">{{ (cmd.result || '—').substring(0, 80) }}</td>
                  <td>{{ new Date(cmd.created_at).toLocaleString() }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted">No commands yet</p>
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
.hint { font-size: 0.75rem; color: var(--text-muted); margin: 0.2rem 0 0; }
.toggle-row label { display: flex; align-items: center; gap: 0.4rem; cursor: pointer; }

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

/* LED configuration */
.led-card { grid-column: 1 / -1; }
.led-grid { display: flex; flex-direction: column; gap: 0.5rem; }
.led-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.4rem 0;
}
.led-preview {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid var(--border);
  cursor: pointer;
  flex-shrink: 0;
  transition: box-shadow 0.2s;
}
.led-preview:hover { box-shadow: 0 0 8px currentColor; }
.led-name {
  min-width: 80px;
  font-size: 0.85rem;
  text-transform: capitalize;
  color: var(--text-primary);
}
.led-row input[type="color"] {
  width: 32px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  padding: 0;
}
.led-brightness {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex: 1;
}
.led-brightness input { flex: 1; }
.led-brightness span { font-size: 0.75rem; color: var(--text-muted); min-width: 35px; }
.btn-danger-sm {
  background: rgba(239, 68, 68, 0.15);
  color: #fca5a5;
  border: 1px solid rgba(239, 68, 68, 0.3);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.75rem;
}
.add-pattern-row {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
  align-items: center;
}
.add-pattern-row input {
  padding: 0.3rem 0.5rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-primary);
  flex: 1;
}

/* Voice & Filler */
.voice-card { grid-column: 1 / -1; }
.tts-input {
  width: 100%;
  padding: 0.4rem 0.5rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-primary);
  font-family: inherit;
  resize: vertical;
}
select {
  padding: 0.4rem 0.5rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-primary);
  width: 100%;
}
.btn-row {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

/* Remote Management */
.mgmt-section { margin-bottom: 1rem; }
.mgmt-section h3 { margin: 0.75rem 0 0.4rem; font-size: 0.9rem; color: var(--text-secondary); }
.cmd-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
.cmd-table th, .cmd-table td { padding: 0.35rem 0.5rem; text-align: left; border-bottom: 1px solid var(--border); }
.cmd-table th { color: var(--text-secondary); font-weight: 600; }
.result-cell { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cmd-history { max-height: 250px; overflow-y: auto; }
</style>
