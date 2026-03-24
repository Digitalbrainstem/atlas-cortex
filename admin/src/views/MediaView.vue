<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const activeTab = ref('providers')
const loading = ref(true)
const error = ref('')

// Providers
const providers = ref([])

// Now Playing
const nowPlaying = ref(null)

// History
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

// Targets
const targets = ref([])

// Podcasts
const podcasts = ref([])
const newFeedUrl = ref('')

// Library scan
const scanPath = ref('')
const scanResult = ref('')

// YouTube OAuth
const ytAuthStep = ref('idle')      // idle | linking | polling | success | error
const ytUserCode = ref('')
const ytVerifyUrl = ref('')
const ytDeviceCode = ref('')
const ytMessage = ref('')
const ytAuthProviders = ref([])
const ytLinkUserId = ref('')        // user_id for per-user linking
const ytSetGlobal = ref(false)      // set as global default after linking
const ytAccountName = ref('')       // custom account label

async function fetchProviders() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/admin/media/providers')
    providers.value = data.providers || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function fetchNowPlaying() {
  try {
    nowPlaying.value = await api.get('/admin/media/now-playing')
  } catch (e) {
    error.value = e.message
  }
}

async function fetchHistory() {
  try {
    const data = await api.get(`/admin/media/history?page=${historyPage.value}`)
    history.value = data.history || []
    historyTotal.value = data.total || 0
  } catch (e) {
    error.value = e.message
  }
}

async function fetchTargets() {
  try {
    const data = await api.get('/admin/media/targets')
    targets.value = data.targets || []
  } catch (e) {
    error.value = e.message
  }
}

async function fetchPodcasts() {
  try {
    const data = await api.get('/admin/media/podcasts')
    podcasts.value = data.subscriptions || []
  } catch (e) {
    error.value = e.message
  }
}

async function subscribePodcast() {
  const url = newFeedUrl.value.trim()
  if (!url) return
  error.value = ''
  try {
    await api.post('/admin/media/podcasts/subscribe', { feed_url: url })
    newFeedUrl.value = ''
    fetchPodcasts()
  } catch (e) {
    error.value = e.message
  }
}

async function scanLibrary() {
  const path = scanPath.value.trim()
  if (!path) return
  scanResult.value = 'Scanning...'
  try {
    const data = await api.post('/admin/media/library/scan', { path })
    scanResult.value = `Indexed ${data.files_indexed} files`
  } catch (e) {
    scanResult.value = `Error: ${e.message}`
  }
}

async function fetchYtAuth() {
  try {
    const data = await api.get('/admin/media/auth')
    ytAuthProviders.value = data.providers || []
  } catch (_) { /* ignore */ }
}

function ytGlobalAccount() {
  return ytAuthProviders.value.find(p => p.provider === 'youtube' && p.is_global && p.authenticated)
}

function ytUserAccounts() {
  return ytAuthProviders.value.filter(p => p.provider === 'youtube' && !p.is_global && p.authenticated)
}

async function startYouTubeAuth(userId = '', setGlobal = false) {
  ytAuthStep.value = 'linking'
  ytMessage.value = ''
  ytLinkUserId.value = userId
  ytSetGlobal.value = setGlobal
  try {
    const data = await api.post('/admin/media/auth/youtube/start', { user_id: userId })
    ytUserCode.value = data.user_code
    ytVerifyUrl.value = data.verification_url
    ytDeviceCode.value = data.device_code
    ytAuthStep.value = 'polling'
    pollYouTubeAuth()
  } catch (e) {
    ytMessage.value = `Error: ${e.message}`
    ytAuthStep.value = 'error'
  }
}

async function pollYouTubeAuth() {
  try {
    const data = await api.post('/admin/media/auth/youtube/complete', {
      device_code: ytDeviceCode.value,
      timeout: 120,
      user_id: ytLinkUserId.value,
      set_global: ytSetGlobal.value,
      account_name: ytAccountName.value || '',
    })
    if (data.ok) {
      ytAuthStep.value = 'success'
      ytMessage.value = data.message
      fetchYtAuth()
    } else {
      ytAuthStep.value = 'error'
      ytMessage.value = data.message
    }
  } catch (e) {
    ytAuthStep.value = 'error'
    ytMessage.value = `Error: ${e.message}`
  }
}

async function unlinkYouTube(userId = '') {
  try {
    const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
    await api.delete(`/admin/media/auth/youtube${qs}`)
    ytAuthStep.value = 'idle'
    ytMessage.value = ''
    fetchYtAuth()
  } catch (e) {
    error.value = e.message
  }
}

async function setAsGlobalDefault(userId) {
  try {
    await api.post('/admin/media/auth/youtube/set-global', { user_id: userId })
    fetchYtAuth()
  } catch (e) {
    error.value = e.message
  }
}

function prevPage() {
  if (historyPage.value > 1) {
    historyPage.value--
    fetchHistory()
  }
}

function nextPage() {
  historyPage.value++
  fetchHistory()
}

onMounted(() => {
  fetchProviders()
  fetchNowPlaying()
  fetchHistory()
  fetchTargets()
  fetchPodcasts()
  fetchYtAuth()
})
</script>

<template>
  <AppLayout>
    <div class="media-view">
      <h1>🎵 Media & Entertainment</h1>

      <div v-if="error" class="error-banner">{{ error }}</div>

      <!-- Tabs -->
      <div class="tabs">
        <button
          v-for="tab in ['providers', 'now-playing', 'history', 'targets', 'podcasts', 'library', 'youtube-auth']"
          :key="tab"
          :class="{ active: activeTab === tab }"
          @click="activeTab = tab"
        >
          {{ tab.replace('-', ' ').replace(/\b\w/g, c => c.toUpperCase()) }}
        </button>
      </div>

      <!-- Providers Tab -->
      <div v-if="activeTab === 'providers'" class="tab-content">
        <div v-if="loading" class="loading">Loading providers...</div>
        <div v-else class="card-grid">
          <div
            v-for="p in providers"
            :key="p.id"
            class="status-card"
            :class="{ healthy: p.healthy, unhealthy: !p.healthy }"
          >
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

      <!-- Now Playing Tab -->
      <div v-if="activeTab === 'now-playing'" class="tab-content">
        <div v-if="nowPlaying && nowPlaying.is_playing" class="now-playing-card">
          <div class="np-title">{{ nowPlaying.item?.title || 'Unknown' }}</div>
          <div class="np-artist">{{ nowPlaying.item?.artist || '' }}</div>
          <div class="np-meta">
            Volume: {{ Math.round((nowPlaying.volume || 0) * 100) }}%
            &nbsp;|&nbsp; Room: {{ nowPlaying.room || 'N/A' }}
          </div>
        </div>
        <div v-else class="empty-state">Nothing is playing right now.</div>
      </div>

      <!-- History Tab -->
      <div v-if="activeTab === 'history'" class="tab-content">
        <DataTable :columns="historyColumns" :rows="history" empty-text="No playback history" />
        <div class="pagination">
          <button @click="prevPage" :disabled="historyPage <= 1">← Prev</button>
          <span>Page {{ historyPage }}</span>
          <button @click="nextPage" :disabled="history.length < 50">Next →</button>
        </div>
      </div>

      <!-- Targets Tab -->
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

      <!-- Podcasts Tab -->
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

      <!-- Library Tab -->
      <div v-if="activeTab === 'library'" class="tab-content">
        <div class="form-row">
          <input v-model="scanPath" placeholder="/path/to/music" class="input" />
          <button @click="scanLibrary" class="btn-primary">Scan</button>
        </div>
        <div v-if="scanResult" class="scan-result">{{ scanResult }}</div>
      </div>

      <!-- YouTube Auth Tab -->
      <div v-if="activeTab === 'youtube-auth'" class="tab-content">
        <div class="yt-auth-section">
          <h2>📺 YouTube Premium</h2>
          <p class="yt-desc">
            Link YouTube accounts for ad-free video on satellite displays.
            Login is <strong>one time only</strong> — Atlas auto-refreshes tokens so you'll never need to log in again.
          </p>

          <!-- Active linking flow (shared by global + per-user) -->
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

          <!-- Normal view (not actively linking) -->
          <template v-else>
            <!-- Global default account -->
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

            <!-- Per-user accounts -->
            <div class="yt-auth-card" style="margin-top:1rem;">
              <h3>👤 Per-User Accounts</h3>
              <p class="yt-hint">Each family member can link their own YouTube account. Falls back to global if not set.</p>
              <div v-if="ytUserAccounts().length > 0" class="yt-user-list">
                <div v-for="ua in ytUserAccounts()" :key="ua.user_id" class="yt-user-row">
                  <div class="yt-user-info">
                    <strong>{{ ua.user_id }}</strong>
                    <span class="yt-user-account">{{ ua.account_name }}</span>
                  </div>
                  <div class="yt-user-actions">
                    <button
                      @click="setAsGlobalDefault(ua.user_id)"
                      class="btn-secondary btn-sm"
                      title="Copy this account as the global default"
                    >⭐ Set as Default</button>
                    <button @click="unlinkYouTube(ua.user_id)" class="btn-danger btn-sm">Unlink</button>
                  </div>
                </div>
              </div>
              <div v-else class="yt-no-users">No per-user accounts linked yet.</div>

              <!-- Add per-user account -->
              <div class="yt-add-user">
                <input v-model="ytAccountName" placeholder="User name (e.g. dad, jake)" class="input input-sm" />
                <button
                  @click="startYouTubeAuth(ytAccountName.trim(), false); ytAccountName = ''"
                  :disabled="!ytAccountName.trim()"
                  class="btn-primary btn-sm"
                >🔗 Link User Account</button>
              </div>
            </div>

            <p class="yt-onetime-note">
              🔒 Login is <strong>one-time</strong>. Google refresh tokens last indefinitely.
              Atlas auto-refreshes access tokens hourly — no one will ever need to re-login.
            </p>
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
}

.tabs {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
}

.tabs button {
  padding: 0.5rem 1rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 0.85rem;
  text-transform: capitalize;
}

.tabs button.active {
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  border-color: var(--accent);
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 1rem;
}

.status-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
}

.card-title { font-weight: 600; }

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.status-dot.green { background: #22c55e; }
.status-dot.red { background: #ef4444; }

.card-detail {
  font-size: 0.85rem;
  color: var(--text-muted);
  margin-bottom: 0.25rem;
}

.now-playing-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 2rem;
  text-align: center;
}

.np-title { font-size: 1.5rem; font-weight: 700; }
.np-artist { font-size: 1.1rem; color: var(--text-secondary); margin-top: 0.5rem; }
.np-meta { font-size: 0.85rem; color: var(--text-muted); margin-top: 1rem; }

.empty-state {
  text-align: center;
  color: var(--text-muted);
  padding: 3rem;
}

.loading { text-align: center; color: var(--text-muted); padding: 2rem; }

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  margin-top: 1rem;
}

.pagination button {
  padding: 0.4rem 0.8rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-secondary);
  cursor: pointer;
}

.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }

.form-row {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.input {
  flex: 1;
  padding: 0.5rem 0.75rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-primary);
  font-size: 0.9rem;
}

.btn-primary {
  padding: 0.5rem 1rem;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 0.9rem;
}

.btn-primary:hover { opacity: 0.9; }

.podcast-list { display: flex; flex-direction: column; gap: 0.75rem; }

.podcast-item {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem;
}

.pod-title { font-weight: 600; }
.pod-desc { font-size: 0.85rem; color: var(--text-muted); margin-top: 0.25rem; }
.pod-meta { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.5rem; }

.scan-result {
  padding: 0.75rem 1rem;
  background: var(--bg-secondary);
  border-radius: var(--radius);
  margin-top: 0.5rem;
}

/* YouTube Auth */
.yt-auth-section h2 { margin-bottom: 0.5rem; }

.yt-auth-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.5rem;
  max-width: 600px;
}

.yt-auth-card h3 { margin-bottom: 0.25rem; font-size: 1rem; }

.yt-desc {
  color: var(--text-muted);
  margin-bottom: 1.5rem;
  font-size: 0.9rem;
  max-width: 600px;
}

.yt-hint {
  color: var(--text-muted);
  font-size: 0.8rem;
  margin-bottom: 0.75rem;
}

.yt-account-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.yt-status-ok {
  font-weight: 600;
  color: #22c55e;
}

.yt-status-none {
  color: var(--text-muted);
  font-size: 0.9rem;
}

.yt-user-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.yt-user-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.5rem 0.75rem;
  background: var(--bg-primary);
  border-radius: var(--radius);
  flex-wrap: wrap;
}

.yt-user-info { display: flex; flex-direction: column; }
.yt-user-account { font-size: 0.8rem; color: var(--text-muted); }
.yt-user-actions { display: flex; gap: 0.5rem; }

.yt-no-users {
  color: var(--text-muted);
  font-size: 0.85rem;
  margin-bottom: 1rem;
}

.yt-add-user {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

.yt-onetime-note {
  margin-top: 1.5rem;
  color: var(--text-muted);
  font-size: 0.8rem;
  max-width: 600px;
}

.yt-link-for {
  color: var(--text-secondary);
  margin-bottom: 0.5rem;
}

.btn-sm { font-size: 0.8rem; padding: 0.35rem 0.75rem; }
.input-sm { max-width: 200px; }

.btn-secondary {
  padding: 0.5rem 1rem;
  background: var(--bg-secondary);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 0.9rem;
}

.btn-secondary:hover { background: var(--bg-primary); }

.btn-danger {
  padding: 0.5rem 1rem;
  background: #ef4444;
  color: #fff;
  border: none;
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 0.85rem;
}

.btn-danger:hover { opacity: 0.9; }

.btn-lg { font-size: 1rem; padding: 0.75rem 1.5rem; }

.yt-code-display { text-align: center; }

.yt-verify-url {
  display: block;
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--accent);
  margin: 0.5rem 0 1.5rem;
}

.yt-code {
  font-size: 2.5rem;
  font-weight: 700;
  letter-spacing: 0.2em;
  font-family: monospace;
  background: var(--bg-primary);
  border: 2px dashed var(--border);
  border-radius: var(--radius);
  padding: 1rem 2rem;
  display: inline-block;
  margin: 0.5rem 0 1.5rem;
}

.yt-waiting { color: var(--text-muted); font-size: 0.9rem; }

@keyframes ytdots {
  0% { content: ''; }
  33% { content: '.'; }
  66% { content: '..'; }
  100% { content: '...'; }
}
.yt-dots { animation: ytdots 1.5s steps(4, end) infinite; }

.yt-polling { text-align: center; color: var(--text-muted); padding: 2rem 0; }

.yt-success { text-align: center; }
.yt-success .btn-primary { margin-top: 1rem; }

.yt-error {
  color: var(--danger);
  margin-top: 0.75rem;
  font-size: 0.9rem;
}
</style>
