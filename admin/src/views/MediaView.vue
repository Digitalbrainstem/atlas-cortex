<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const activeTab = ref('playback')
const loading = ref(true)
const error = ref('')

// ── Playback state (all rooms) ────────────────────────────────
const rooms = ref([])
const selectedRoom = ref('')
let pollTimer = null

// ── Search & play ─────────────────────────────────────────────
const searchQuery = ref('')
const searchProvider = ref('')
const searchResults = ref([])
const searchLoading = ref(false)
const playTargetRoom = ref('')

// ── Providers ─────────────────────────────────────────────────
const providers = ref([])

// ── History ───────────────────────────────────────────────────
const history = ref([])
const historyPage = ref(1)
const historyTotal = ref(0)
const historyColumns = [
  { key: 'id', label: 'ID' },
  { key: 'title', label: 'Title' },
  { key: 'artist', label: 'Artist' },
  { key: 'provider', label: 'Provider' },
  { key: 'user_id', label: 'User' },
  { key: 'played_at', label: 'Played At' },
]

// ── Targets / satellites ──────────────────────────────────────
const targets = ref([])

// ── Queue ─────────────────────────────────────────────────────
const queueRoom = ref('')
const queueItems = ref([])
const queueAddQuery = ref('')

// ── Podcasts ──────────────────────────────────────────────────
const podcasts = ref([])
const newFeedUrl = ref('')

// ── Library scan ──────────────────────────────────────────────
const scanPath = ref('')
const scanResult = ref('')

// ── YouTube OAuth ─────────────────────────────────────────────
const ytAuthStep = ref('idle')
const ytUserCode = ref('')
const ytVerifyUrl = ref('')
const ytDeviceCode = ref('')
const ytMessage = ref('')
const ytAuthProviders = ref([])
const ytLinkUserId = ref('')
const ytSetGlobal = ref(false)
const ytAccountName = ref('')

// ── Helpers ───────────────────────────────────────────────────
function fmtTime(s) {
  if (!s || s < 0) return '0:00'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, '0')}`
}

const activeRooms = computed(() => rooms.value.filter(r => r.is_playing || r.item))
const currentRoom = computed(() => rooms.value.find(r => r.room === selectedRoom.value))

// ── Data fetching ─────────────────────────────────────────────
async function fetchAllRooms() {
  try {
    const data = await api.get('/admin/media/all-rooms')
    rooms.value = data.rooms || []
    if (!selectedRoom.value && rooms.value.length > 0) {
      selectedRoom.value = rooms.value[0].room
    }
  } catch (_) { /* silent poll */ }
}

async function fetchProviders() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/admin/media/providers')
    providers.value = data.providers || []
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function fetchHistory() {
  try {
    const data = await api.get(`/admin/media/history?page=${historyPage.value}`)
    history.value = data.history || []
    historyTotal.value = data.total || 0
  } catch (e) { error.value = e.message }
}

async function fetchTargets() {
  try {
    const data = await api.get('/admin/media/targets')
    targets.value = data.targets || []
  } catch (e) { error.value = e.message }
}

async function fetchQueue(room) {
  const r = room || queueRoom.value || 'default'
  try {
    const data = await api.get(`/admin/media/queue?room=${encodeURIComponent(r)}`)
    queueItems.value = data.queue || []
    queueRoom.value = r
  } catch (e) { error.value = e.message }
}

async function fetchPodcasts() {
  try {
    const data = await api.get('/admin/media/podcasts')
    podcasts.value = data.subscriptions || []
  } catch (e) { error.value = e.message }
}

async function fetchYtAuth() {
  try {
    const data = await api.get('/admin/media/auth')
    ytAuthProviders.value = data.providers || []
  } catch (_) { /* ignore */ }
}

// ── Playback controls ─────────────────────────────────────────
async function mediaCmd(action, room, extra = {}) {
  try {
    await api.post(`/admin/media/${action}`, { room, ...extra })
    await fetchAllRooms()
  } catch (e) { error.value = e.message }
}

async function setVolume(room, level) {
  try {
    await api.post('/admin/media/volume', { level, room })
    await fetchAllRooms()
  } catch (e) { error.value = e.message }
}

async function setSatelliteVolume(satelliteId, level) {
  try {
    await api.post('/admin/media/satellite-volume', { level, satellite_id: satelliteId })
  } catch (e) { error.value = e.message }
}

async function seekTo(room, positionSeconds) {
  try {
    await api.post('/admin/media/seek', { position_seconds: positionSeconds, room })
    await fetchAllRooms()
  } catch (e) { error.value = e.message }
}

// ── Search & play ─────────────────────────────────────────────
async function doSearch() {
  const q = searchQuery.value.trim()
  if (!q) return
  searchLoading.value = true
  searchResults.value = []
  try {
    const prov = searchProvider.value || ''
    const data = await api.get(`/admin/media/search?q=${encodeURIComponent(q)}&provider=${encodeURIComponent(prov)}`)
    searchResults.value = data.results || []
  } catch (e) { error.value = e.message }
  finally { searchLoading.value = false }
}

async function playItem(query, room) {
  try {
    await api.post('/admin/media/play', { query, room: room || playTargetRoom.value || 'default' })
    searchResults.value = []
    searchQuery.value = ''
    await fetchAllRooms()
    activeTab.value = 'playback'
  } catch (e) { error.value = e.message }
}

async function addToQueue(query) {
  const room = queueRoom.value || 'default'
  try {
    await api.post('/admin/media/queue/add', { query, room })
    await fetchQueue(room)
  } catch (e) { error.value = e.message }
}

async function clearQueue(room) {
  try {
    await api.post('/admin/media/queue/clear', { room: room || queueRoom.value || 'default' })
    await fetchQueue(room)
  } catch (e) { error.value = e.message }
}

// ── Queue management ──────────────────────────────────────────
async function addQueueItem() {
  const q = queueAddQuery.value.trim()
  if (!q) return
  await addToQueue(q)
  queueAddQuery.value = ''
}

// ── History pagination ────────────────────────────────────────
function prevPage() { if (historyPage.value > 1) { historyPage.value--; fetchHistory() } }
function nextPage() { historyPage.value++; fetchHistory() }

// ── Podcasts ──────────────────────────────────────────────────
async function subscribePodcast() {
  const url = newFeedUrl.value.trim()
  if (!url) return
  try {
    await api.post('/admin/media/podcasts/subscribe', { feed_url: url })
    newFeedUrl.value = ''
    fetchPodcasts()
  } catch (e) { error.value = e.message }
}

// ── Library scan ──────────────────────────────────────────────
async function scanLibrary() {
  const path = scanPath.value.trim()
  if (!path) return
  scanResult.value = 'Scanning...'
  try {
    const data = await api.post('/admin/media/library/scan', { path })
    scanResult.value = `Indexed ${data.files_indexed} files`
  } catch (e) { scanResult.value = `Error: ${e.message}` }
}

// ── YouTube OAuth ─────────────────────────────────────────────
function ytGlobalAccount() {
  return ytAuthProviders.value.find(p => p.provider === 'youtube' && p.is_global && p.authenticated)
}
function ytUserAccounts() {
  return ytAuthProviders.value.filter(p => p.provider === 'youtube' && !p.is_global && p.authenticated)
}
async function startYouTubeAuth(userId = '', setGlobal = false) {
  ytAuthStep.value = 'linking'; ytMessage.value = ''; ytLinkUserId.value = userId; ytSetGlobal.value = setGlobal
  try {
    const data = await api.post('/admin/media/auth/youtube/start', { user_id: userId })
    ytUserCode.value = data.user_code; ytVerifyUrl.value = data.verification_url; ytDeviceCode.value = data.device_code
    ytAuthStep.value = 'polling'; pollYouTubeAuth()
  } catch (e) { ytMessage.value = `Error: ${e.message}`; ytAuthStep.value = 'error' }
}
async function pollYouTubeAuth() {
  try {
    const data = await api.post('/admin/media/auth/youtube/complete', {
      device_code: ytDeviceCode.value, timeout: 120, user_id: ytLinkUserId.value,
      set_global: ytSetGlobal.value, account_name: ytAccountName.value || '',
    })
    ytAuthStep.value = data.ok ? 'success' : 'error'; ytMessage.value = data.message
    if (data.ok) fetchYtAuth()
  } catch (e) { ytAuthStep.value = 'error'; ytMessage.value = `Error: ${e.message}` }
}
async function unlinkYouTube(userId = '') {
  try {
    const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
    await api.delete(`/admin/media/auth/youtube${qs}`)
    ytAuthStep.value = 'idle'; ytMessage.value = ''; fetchYtAuth()
  } catch (e) { error.value = e.message }
}
async function setAsGlobalDefault(userId) {
  try { await api.post('/admin/media/auth/youtube/set-global', { user_id: userId }); fetchYtAuth() }
  catch (e) { error.value = e.message }
}

// ── Progress bar click handler ────────────────────────────────
function onProgressClick(e, room, duration) {
  if (!duration) return
  const rect = e.currentTarget.getBoundingClientRect()
  const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
  seekTo(room, pct * duration)
}

// ── Volume slider handler ─────────────────────────────────────
function onVolumeInput(e, room) {
  setVolume(room, parseInt(e.target.value))
}

function onSatVolumeInput(e, satId) {
  setSatelliteVolume(satId, parseInt(e.target.value))
}

// ── Lifecycle ─────────────────────────────────────────────────
onMounted(() => {
  fetchAllRooms()
  fetchProviders()
  fetchHistory()
  fetchTargets()
  fetchPodcasts()
  fetchYtAuth()
  pollTimer = setInterval(fetchAllRooms, 3000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <AppLayout>
    <div class="media-view">
      <h1>🎵 Media & Entertainment</h1>

      <div v-if="error" class="error-banner">{{ error }} <button class="dismiss-btn" @click="error = ''">✕</button></div>

      <!-- Tabs -->
      <div class="tabs">
        <button v-for="tab in ['playback', 'search', 'queue', 'providers', 'history', 'targets', 'podcasts', 'library', 'youtube-auth']"
          :key="tab" :class="{ active: activeTab === tab }" @click="activeTab = tab">
          {{ tab === 'youtube-auth' ? 'YouTube Auth' : tab.replace('-', ' ').replace(/\b\w/g, c => c.toUpperCase()) }}
        </button>
      </div>

      <!-- ═══════ Playback Tab ═══════ -->
      <div v-if="activeTab === 'playback'" class="tab-content">
        <div v-if="activeRooms.length === 0" class="empty-state">
          <p>🔇 No active playback in any room</p>
          <button class="btn-primary" style="margin-top:1rem" @click="activeTab = 'search'">🔍 Search & Play</button>
        </div>

        <div v-for="rm in rooms" :key="rm.room" class="room-card" :class="{ playing: rm.is_playing }">
          <div class="room-header">
            <span class="room-name">{{ rm.room }}</span>
            <span class="room-status" :class="rm.is_playing ? 'status-playing' : 'status-paused'">
              {{ rm.is_playing ? '▶ Playing' : '⏸ Paused' }}
            </span>
          </div>

          <div v-if="rm.item" class="room-now-playing">
            <div v-if="rm.item.album_art_url" class="room-art">
              <img :src="rm.item.album_art_url" alt="" />
            </div>
            <div v-else class="room-art room-art-placeholder">🎵</div>
            <div class="room-track-info">
              <div class="room-track-title">{{ rm.item.title }}</div>
              <div class="room-track-artist">{{ rm.item.artist }}</div>
              <div class="room-track-meta">{{ rm.item.album }} · {{ rm.item.provider }}</div>
            </div>
          </div>

          <!-- Progress -->
          <div v-if="rm.item" class="room-progress">
            <span class="time-label">{{ fmtTime(rm.position_seconds) }}</span>
            <div class="progress-bar" @click="onProgressClick($event, rm.room, rm.item.duration_seconds)">
              <div class="progress-fill" :style="{ width: (rm.item.duration_seconds > 0 ? (rm.position_seconds / rm.item.duration_seconds) * 100 : 0) + '%' }"></div>
            </div>
            <span class="time-label">{{ fmtTime(rm.item.duration_seconds) }}</span>
          </div>

          <!-- Controls -->
          <div class="room-controls">
            <button class="ctrl-btn" @click="mediaCmd('previous', rm.room)" title="Previous">⏮</button>
            <button class="ctrl-btn ctrl-play" @click="mediaCmd(rm.is_playing ? 'pause' : 'resume', rm.room)" :title="rm.is_playing ? 'Pause' : 'Play'">
              {{ rm.is_playing ? '⏸' : '▶' }}
            </button>
            <button class="ctrl-btn" @click="mediaCmd('next', rm.room)" title="Next">⏭</button>
            <button class="ctrl-btn ctrl-stop" @click="mediaCmd('stop', rm.room)" title="Stop">⏹</button>
          </div>

          <!-- Volume -->
          <div class="room-volume">
            <span class="vol-label">🔊 Volume</span>
            <input type="range" min="0" max="100" :value="Math.round((rm.volume || 0) * 100)" @input="onVolumeInput($event, rm.room)" class="vol-slider" />
            <span class="vol-value">{{ Math.round((rm.volume || 0) * 100) }}%</span>
          </div>
        </div>

        <!-- Satellite volumes -->
        <div v-if="targets.length > 0" class="sat-volume-section">
          <h3>📡 Satellite Volumes</h3>
          <div v-for="t in targets.filter(t => t.type === 'satellite')" :key="t.id" class="sat-vol-row">
            <span class="sat-vol-name">{{ t.id }} <span class="sat-vol-room">({{ t.room || 'N/A' }})</span></span>
            <input type="range" min="0" max="100" value="70" @input="onSatVolumeInput($event, t.id)" class="vol-slider" />
          </div>
        </div>
      </div>

      <!-- ═══════ Search Tab ═══════ -->
      <div v-if="activeTab === 'search'" class="tab-content">
        <div class="search-bar">
          <input v-model="searchQuery" placeholder="Search for music, podcasts, audiobooks..." class="input search-input" @keyup.enter="doSearch" />
          <select v-model="searchProvider" class="input search-provider">
            <option value="">All Providers</option>
            <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
          <select v-model="playTargetRoom" class="input search-room">
            <option value="">Default Room</option>
            <option v-for="t in targets" :key="t.id" :value="t.room">{{ t.room || t.id }}</option>
          </select>
          <button @click="doSearch" class="btn-primary" :disabled="searchLoading">
            {{ searchLoading ? 'Searching...' : '🔍 Search' }}
          </button>
        </div>

        <div v-if="searchResults.length > 0" class="search-results">
          <div v-for="(r, i) in searchResults" :key="i" class="search-result-item">
            <div class="sr-info">
              <div class="sr-title">{{ r.title }}</div>
              <div class="sr-artist">{{ r.artist }} <span v-if="r.album">· {{ r.album }}</span></div>
              <div class="sr-meta">{{ r.provider }} · {{ fmtTime(r.duration_seconds) }}</div>
            </div>
            <div class="sr-actions">
              <button class="btn-primary btn-sm" @click="playItem(r.title + ' ' + r.artist, playTargetRoom)">▶ Play</button>
              <button class="btn-secondary btn-sm" @click="addToQueue(r.title + ' ' + r.artist)">+ Queue</button>
            </div>
          </div>
        </div>
        <div v-else-if="searchQuery && !searchLoading" class="empty-state">No results. Try a different query.</div>
      </div>

      <!-- ═══════ Queue Tab ═══════ -->
      <div v-if="activeTab === 'queue'" class="tab-content">
        <div class="form-row">
          <select v-model="queueRoom" class="input" style="max-width:200px" @change="fetchQueue(queueRoom)">
            <option value="">Default</option>
            <option v-for="t in targets" :key="t.id" :value="t.room">{{ t.room || t.id }}</option>
          </select>
          <button @click="fetchQueue(queueRoom)" class="btn-secondary">Refresh</button>
        </div>
        <div class="form-row">
          <input v-model="queueAddQuery" placeholder="Add to queue..." class="input" @keyup.enter="addQueueItem" />
          <button @click="addQueueItem" class="btn-primary" :disabled="!queueAddQuery.trim()">+ Add</button>
          <button @click="clearQueue(queueRoom)" class="btn-danger" :disabled="queueItems.length === 0">Clear All</button>
        </div>
        <div v-if="queueItems.length === 0" class="empty-state">Queue is empty.</div>
        <div v-else class="queue-list">
          <div v-for="(q, i) in queueItems" :key="i" class="queue-item">
            <span class="queue-num">{{ i + 1 }}</span>
            <div class="queue-info">
              <div class="queue-title">{{ q.title }}</div>
              <div class="queue-artist">{{ q.artist }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- ═══════ Providers Tab ═══════ -->
      <div v-if="activeTab === 'providers'" class="tab-content">
        <div v-if="loading" class="loading">Loading providers...</div>
        <div v-else class="card-grid">
          <div v-for="p in providers" :key="p.id" class="status-card" :class="{ healthy: p.healthy, unhealthy: !p.healthy }">
            <div class="card-header">
              <span class="card-title">{{ p.name }}</span>
              <span class="status-dot" :class="p.healthy ? 'green' : 'red'"></span>
            </div>
            <div class="card-body">
              <div class="card-detail">ID: {{ p.id }}</div>
              <div class="card-detail">Streaming: {{ p.streaming ? 'Yes' : 'No' }}</div>
              <div class="card-detail">Status: {{ p.healthy ? 'Connected' : 'Unavailable' }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- ═══════ History Tab ═══════ -->
      <div v-if="activeTab === 'history'" class="tab-content">
        <DataTable :columns="historyColumns" :rows="history" empty-text="No playback history" />
        <div class="pagination">
          <button @click="prevPage" :disabled="historyPage <= 1">← Prev</button>
          <span>Page {{ historyPage }}</span>
          <button @click="nextPage" :disabled="history.length < 50">Next →</button>
        </div>
      </div>

      <!-- ═══════ Targets Tab ═══════ -->
      <div v-if="activeTab === 'targets'" class="tab-content">
        <div v-if="targets.length === 0" class="empty-state">No playback targets found.</div>
        <div v-else class="card-grid">
          <div v-for="t in targets" :key="t.id" class="status-card">
            <div class="card-header">
              <span class="card-title">{{ t.id }}</span>
            </div>
            <div class="card-body">
              <div class="card-detail">Type: {{ t.type }}</div>
              <div class="card-detail">Room: {{ t.room || 'N/A' }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- ═══════ Podcasts Tab ═══════ -->
      <div v-if="activeTab === 'podcasts'" class="tab-content">
        <div class="form-row">
          <input v-model="newFeedUrl" placeholder="Podcast RSS feed URL..." class="input" />
          <button @click="subscribePodcast" class="btn-primary">Subscribe</button>
        </div>
        <div v-if="podcasts.length === 0" class="empty-state">No podcast subscriptions.</div>
        <div v-else class="podcast-list">
          <div v-for="pod in podcasts" :key="pod.id" class="podcast-item">
            <div class="pod-title">{{ pod.title }}</div>
            <div class="pod-desc">{{ pod.description }}</div>
            <div class="pod-meta">Last checked: {{ pod.last_checked || 'Never' }}</div>
          </div>
        </div>
      </div>

      <!-- ═══════ Library Tab ═══════ -->
      <div v-if="activeTab === 'library'" class="tab-content">
        <div class="form-row">
          <input v-model="scanPath" placeholder="/path/to/music" class="input" />
          <button @click="scanLibrary" class="btn-primary">Scan</button>
        </div>
        <div v-if="scanResult" class="scan-result">{{ scanResult }}</div>
      </div>

      <!-- ═══════ YouTube Auth Tab ═══════ -->
      <div v-if="activeTab === 'youtube-auth'" class="tab-content">
        <div class="yt-auth-section">
          <h2>📺 YouTube Premium</h2>
          <p class="yt-desc">
            Link YouTube accounts for ad-free video on satellite displays.
            Login is <strong>one time only</strong> — Atlas auto-refreshes tokens so you'll never need to log in again.
          </p>

          <div v-if="ytAuthStep === 'linking'" class="yt-auth-card">
            <div class="yt-polling"><p>Starting device flow...</p></div>
          </div>
          <div v-else-if="ytAuthStep === 'polling'" class="yt-auth-card">
            <div class="yt-code-display">
              <p>Go to:</p>
              <a :href="ytVerifyUrl" target="_blank" class="yt-verify-url">{{ ytVerifyUrl }}</a>
              <p>And enter this code:</p>
              <div class="yt-code">{{ ytUserCode }}</div>
              <p v-if="ytLinkUserId" class="yt-link-for">Linking for: <strong>{{ ytLinkUserId }}</strong></p>
              <p class="yt-waiting">Waiting for authorization<span class="yt-dots">...</span></p>
            </div>
          </div>
          <div v-else-if="ytAuthStep === 'success'" class="yt-auth-card">
            <div class="yt-success">
              <p class="yt-status-ok">✅ {{ ytMessage }}</p>
              <button @click="ytAuthStep = 'idle'; fetchYtAuth()" class="btn-primary">Done</button>
            </div>
          </div>
          <div v-else-if="ytAuthStep === 'error'" class="yt-auth-card">
            <p class="yt-error">{{ ytMessage }}</p>
            <button @click="ytAuthStep = 'idle'" class="btn-primary" style="margin-top:0.75rem;">Try Again</button>
          </div>

          <template v-else>
            <div class="yt-auth-card">
              <h3>🌐 Global Default Account</h3>
              <p class="yt-hint">Used when a user doesn't have their own account linked.</p>
              <div v-if="ytGlobalAccount()" class="yt-account-row">
                <span class="yt-status-ok">✅ {{ ytGlobalAccount().account_name || 'YouTube Premium' }}</span>
                <button @click="unlinkYouTube()" class="btn-danger btn-sm">Unlink</button>
              </div>
              <div v-else class="yt-account-row">
                <span class="yt-status-none">No global account linked</span>
                <button @click="startYouTubeAuth('', false)" class="btn-primary btn-sm">🔗 Link Global Account</button>
              </div>
            </div>
            <div class="yt-auth-card" style="margin-top:1rem;">
              <h3>👤 Per-User Accounts</h3>
              <p class="yt-hint">Each family member can link their own YouTube account.</p>
              <div v-if="ytUserAccounts().length > 0" class="yt-user-list">
                <div v-for="ua in ytUserAccounts()" :key="ua.user_id" class="yt-user-row">
                  <div class="yt-user-info">
                    <strong>{{ ua.user_id }}</strong>
                    <span class="yt-user-account">{{ ua.account_name }}</span>
                  </div>
                  <div class="yt-user-actions">
                    <button @click="setAsGlobalDefault(ua.user_id)" class="btn-secondary btn-sm" title="Copy as global default">⭐ Set as Default</button>
                    <button @click="unlinkYouTube(ua.user_id)" class="btn-danger btn-sm">Unlink</button>
                  </div>
                </div>
              </div>
              <div v-else class="yt-no-users">No per-user accounts linked yet.</div>
              <div class="yt-add-user">
                <input v-model="ytAccountName" placeholder="User name (e.g. dad, jake)" class="input input-sm" />
                <button @click="startYouTubeAuth(ytAccountName.trim(), false); ytAccountName = ''" :disabled="!ytAccountName.trim()" class="btn-primary btn-sm">🔗 Link User Account</button>
              </div>
            </div>
            <p class="yt-onetime-note">🔒 Login is <strong>one-time</strong>. Google refresh tokens last indefinitely.</p>
          </template>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.media-view { max-width: 1200px; }

.error-banner {
  background: rgba(239, 68, 68, 0.15);
  color: var(--danger);
  padding: 0.75rem 1rem;
  border-radius: var(--radius);
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.dismiss-btn { background: none; border: none; color: var(--danger); cursor: pointer; font-size: 1rem; }

/* Tabs */
.tabs { display: flex; gap: 0.25rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
.tabs button {
  padding: 0.5rem 1rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-secondary);
  cursor: pointer; font-size: 0.85rem; text-transform: capitalize;
}
.tabs button.active { background: rgba(59, 130, 246, 0.15); color: var(--accent); border-color: var(--accent); }

/* Room cards */
.room-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 1.25rem;
  margin-bottom: 1rem;
  transition: border-color 0.2s;
}
.room-card.playing { border-color: var(--accent); }

.room-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
.room-name { font-weight: 700; font-size: 1.1rem; }
.room-status { font-size: 0.8rem; padding: 0.2rem 0.6rem; border-radius: 99px; }
.status-playing { background: rgba(59, 130, 246, 0.2); color: var(--accent); }
.status-paused { background: rgba(100, 116, 139, 0.2); color: var(--text-muted); }

.room-now-playing { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
.room-art { width: 72px; height: 72px; border-radius: var(--radius); flex-shrink: 0; overflow: hidden; }
.room-art img { width: 100%; height: 100%; object-fit: cover; }
.room-art-placeholder { background: rgba(255,255,255,0.06); display: flex; align-items: center; justify-content: center; font-size: 28px; }
.room-track-title { font-weight: 600; font-size: 1rem; }
.room-track-artist { color: var(--text-secondary); font-size: 0.9rem; margin-top: 0.15rem; }
.room-track-meta { color: var(--text-muted); font-size: 0.8rem; margin-top: 0.15rem; }

/* Progress */
.room-progress { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; }
.time-label { font-size: 0.75rem; color: var(--text-muted); min-width: 36px; font-family: var(--font-mono); }
.progress-bar { flex: 1; height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px; cursor: pointer; position: relative; }
.progress-fill { height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.3s linear; }

/* Controls */
.room-controls { display: flex; align-items: center; justify-content: center; gap: 0.75rem; margin-bottom: 1rem; }
.ctrl-btn {
  background: none; border: 1px solid var(--border); color: var(--text-primary);
  font-size: 1.2rem; cursor: pointer; padding: 0.5rem; min-width: 44px; min-height: 44px;
  display: flex; align-items: center; justify-content: center; border-radius: 50%;
  transition: background 0.2s, border-color 0.2s;
}
.ctrl-btn:hover { background: rgba(255,255,255,0.05); border-color: var(--accent); }
.ctrl-play { font-size: 1.5rem; min-width: 52px; min-height: 52px; border-color: var(--accent); }
.ctrl-stop { font-size: 1rem; }

/* Volume */
.room-volume { display: flex; align-items: center; gap: 0.75rem; }
.vol-label { font-size: 0.85rem; color: var(--text-secondary); min-width: 70px; }
.vol-slider {
  flex: 1; height: 6px; appearance: none; background: rgba(255,255,255,0.1); border-radius: 3px; cursor: pointer;
}
.vol-slider::-webkit-slider-thumb {
  appearance: none; width: 16px; height: 16px; border-radius: 50%;
  background: var(--accent); cursor: pointer;
}
.vol-slider::-moz-range-thumb {
  width: 16px; height: 16px; border-radius: 50%;
  background: var(--accent); cursor: pointer; border: none;
}
.vol-value { font-size: 0.8rem; color: var(--text-muted); min-width: 36px; text-align: right; }

/* Satellite volume section */
.sat-volume-section {
  background: var(--bg-secondary); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 1.25rem; margin-top: 1rem;
}
.sat-volume-section h3 { font-size: 1rem; margin-bottom: 0.75rem; }
.sat-vol-row { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }
.sat-vol-name { min-width: 160px; font-size: 0.9rem; }
.sat-vol-room { color: var(--text-muted); font-size: 0.8rem; }

/* Search */
.search-bar { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
.search-input { flex: 2; min-width: 200px; }
.search-provider { max-width: 180px; }
.search-room { max-width: 160px; }
.search-results { display: flex; flex-direction: column; gap: 0.5rem; }
.search-result-item {
  background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 0.75rem 1rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem;
}
.sr-title { font-weight: 600; }
.sr-artist { font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.1rem; }
.sr-meta { font-size: 0.75rem; color: var(--text-muted); margin-top: 0.1rem; }
.sr-actions { display: flex; gap: 0.5rem; flex-shrink: 0; }

/* Queue */
.queue-list { display: flex; flex-direction: column; gap: 0.25rem; }
.queue-item {
  display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 0.75rem;
  background: var(--bg-secondary); border-radius: var(--radius);
}
.queue-num { color: var(--text-muted); font-size: 0.85rem; min-width: 24px; text-align: center; }
.queue-title { font-weight: 500; }
.queue-artist { font-size: 0.8rem; color: var(--text-muted); }

/* Shared */
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 1rem; }
.status-card { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius); padding: 1rem; }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
.card-title { font-weight: 600; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; }
.status-dot.green { background: #22c55e; }
.status-dot.red { background: #ef4444; }
.card-detail { font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.25rem; }
.empty-state { text-align: center; color: var(--text-muted); padding: 3rem; }
.loading { text-align: center; color: var(--text-muted); padding: 2rem; }
.pagination { display: flex; align-items: center; justify-content: center; gap: 1rem; margin-top: 1rem; }
.pagination button { padding: 0.4rem 0.8rem; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius); color: var(--text-secondary); cursor: pointer; }
.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
.form-row { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
.input { flex: 1; padding: 0.5rem 0.75rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: var(--radius); color: var(--text-primary); font-size: 0.9rem; }
.btn-primary { padding: 0.5rem 1rem; background: var(--accent); color: #fff; border: none; border-radius: var(--radius); cursor: pointer; font-size: 0.9rem; white-space: nowrap; }
.btn-primary:hover { opacity: 0.9; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary { padding: 0.5rem 1rem; background: var(--bg-secondary); color: var(--text-primary); border: 1px solid var(--border); border-radius: var(--radius); cursor: pointer; font-size: 0.9rem; }
.btn-secondary:hover { background: var(--bg-primary); }
.btn-danger { padding: 0.5rem 1rem; background: #ef4444; color: #fff; border: none; border-radius: var(--radius); cursor: pointer; font-size: 0.85rem; }
.btn-danger:hover { opacity: 0.9; }
.btn-sm { font-size: 0.8rem; padding: 0.35rem 0.75rem; }
.input-sm { max-width: 200px; }

/* Podcasts */
.podcast-list { display: flex; flex-direction: column; gap: 0.75rem; }
.podcast-item { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius); padding: 1rem; }
.pod-title { font-weight: 600; }
.pod-desc { font-size: 0.85rem; color: var(--text-muted); margin-top: 0.25rem; }
.pod-meta { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.5rem; }
.scan-result { padding: 0.75rem 1rem; background: var(--bg-secondary); border-radius: var(--radius); margin-top: 0.5rem; }

/* YouTube Auth */
.yt-auth-section h2 { margin-bottom: 0.5rem; }
.yt-auth-card { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.5rem; max-width: 600px; }
.yt-auth-card h3 { margin-bottom: 0.25rem; font-size: 1rem; }
.yt-desc { color: var(--text-muted); margin-bottom: 1.5rem; font-size: 0.9rem; max-width: 600px; }
.yt-hint { color: var(--text-muted); font-size: 0.8rem; margin-bottom: 0.75rem; }
.yt-account-row { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.yt-status-ok { font-weight: 600; color: #22c55e; }
.yt-status-none { color: var(--text-muted); font-size: 0.9rem; }
.yt-user-list { display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 1rem; }
.yt-user-row { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; padding: 0.5rem 0.75rem; background: var(--bg-primary); border-radius: var(--radius); flex-wrap: wrap; }
.yt-user-info { display: flex; flex-direction: column; }
.yt-user-account { font-size: 0.8rem; color: var(--text-muted); }
.yt-user-actions { display: flex; gap: 0.5rem; }
.yt-no-users { color: var(--text-muted); font-size: 0.85rem; margin-bottom: 1rem; }
.yt-add-user { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
.yt-onetime-note { margin-top: 1.5rem; color: var(--text-muted); font-size: 0.8rem; max-width: 600px; }
.yt-link-for { color: var(--text-secondary); margin-bottom: 0.5rem; }
.yt-code-display { text-align: center; }
.yt-verify-url { display: block; font-size: 1.1rem; font-weight: 600; color: var(--accent); margin: 0.5rem 0 1.5rem; }
.yt-code { font-size: 2.5rem; font-weight: 700; letter-spacing: 0.2em; font-family: monospace; background: var(--bg-primary); border: 2px dashed var(--border); border-radius: var(--radius); padding: 1rem 2rem; display: inline-block; margin: 0.5rem 0 1.5rem; }
.yt-waiting { color: var(--text-muted); font-size: 0.9rem; }
@keyframes ytdots { 0% { content: ''; } 33% { content: '.'; } 66% { content: '..'; } 100% { content: '...'; } }
.yt-dots { animation: ytdots 1.5s steps(4, end) infinite; }
.yt-polling { text-align: center; color: var(--text-muted); padding: 2rem 0; }
.yt-success { text-align: center; }
.yt-success .btn-primary { margin-top: 1rem; }
.yt-error { color: var(--danger); margin-top: 0.75rem; font-size: 0.9rem; }
</style>
