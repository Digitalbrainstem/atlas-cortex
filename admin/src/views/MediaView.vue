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
          v-for="tab in ['providers', 'now-playing', 'history', 'targets', 'podcasts', 'library']"
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
</style>
