<script setup>
import { ref, onMounted, computed } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const loading = ref(true)
const error = ref('')
const success = ref('')

// Channels
const channels = ref([])
const showCreate = ref(false)
const creating = ref(false)
const newChannel = ref({ channel_type: 'webhook', name: '', config: {} })

// Messages
const messages = ref([])
const messageFilter = ref({ channel_id: '', direction: '' })

// Channel columns
const channelColumns = [
  { key: 'channel_type', label: 'Type' },
  { key: 'name', label: 'Name' },
  { key: 'enabled', label: 'Enabled' },
  { key: 'last_activity', label: 'Last Activity' },
  { key: 'created_at', label: 'Created' },
]

// Message columns
const messageColumns = [
  { key: 'channel_name', label: 'Channel' },
  { key: 'channel_type', label: 'Type' },
  { key: 'direction', label: 'Direction' },
  { key: 'content_preview', label: 'Content' },
  { key: 'created_at', label: 'Time' },
]

const typeIcons = {
  sms: '📱',
  email: '📧',
  webhook: '🔗',
  serial: '🔌',
}

onMounted(fetchAll)

async function fetchAll() {
  loading.value = true
  error.value = ''
  try {
    const [cData, mData] = await Promise.all([
      api.get('/admin/legacy/channels'),
      api.get('/admin/legacy/messages?limit=50'),
    ])
    channels.value = (cData.channels || []).map(c => ({
      ...c,
      channel_type: `${typeIcons[c.channel_type] || '❓'} ${c.channel_type}`,
      _raw_type: c.channel_type,
      enabled: c.enabled ? '✅' : '❌',
    }))
    messages.value = (mData.messages || []).map(m => ({
      ...m,
      content_preview: (m.content || '').substring(0, 80) + (m.content && m.content.length > 80 ? '…' : ''),
      direction: m.direction === 'inbound' ? '📥 In' : '📤 Out',
    }))
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function createChannel() {
  creating.value = true
  error.value = ''
  try {
    await api.post('/admin/legacy/channels', {
      channel_type: newChannel.value.channel_type,
      name: newChannel.value.name,
      config: newChannel.value.config,
    })
    success.value = `Channel "${newChannel.value.name}" created`
    showCreate.value = false
    newChannel.value = { channel_type: 'webhook', name: '', config: {} }
    fetchAll()
    setTimeout(() => { success.value = '' }, 3000)
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}

async function deleteChannel(id) {
  if (!confirm('Delete this channel and all its messages?')) return
  try {
    await api.delete(`/admin/legacy/channels/${id}`)
    success.value = 'Channel deleted'
    fetchAll()
    setTimeout(() => { success.value = '' }, 3000)
  } catch (e) {
    error.value = e.message
  }
}

async function toggleChannel(channel) {
  try {
    const enabled = channel.enabled !== '✅'
    await api.patch(`/admin/legacy/channels/${channel.id}`, { enabled })
    fetchAll()
  } catch (e) {
    error.value = e.message
  }
}

async function testChannel(id) {
  try {
    const res = await api.post(`/admin/legacy/channels/${id}/test`, {
      message: 'Hello from Atlas!',
    })
    if (res.ok) {
      success.value = res.message || 'Test successful'
    } else {
      error.value = res.error || 'Test failed'
    }
    setTimeout(() => { success.value = ''; error.value = '' }, 3000)
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">📡 Legacy Protocol</h2>

    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <div v-if="loading" class="loading">Loading…</div>

    <template v-else>
      <!-- Channels Section -->
      <section class="section">
        <div class="section-header">
          <h3>Channels</h3>
          <button class="btn btn-primary" @click="showCreate = !showCreate">
            {{ showCreate ? '✕ Cancel' : '+ Add Channel' }}
          </button>
        </div>

        <!-- Create Channel Form -->
        <div v-if="showCreate" class="card create-form">
          <div class="form-row">
            <label>Type</label>
            <select v-model="newChannel.channel_type">
              <option value="sms">📱 SMS</option>
              <option value="email">📧 Email</option>
              <option value="webhook">🔗 Webhook</option>
              <option value="serial">🔌 Serial</option>
            </select>
          </div>
          <div class="form-row">
            <label>Name</label>
            <input v-model="newChannel.name" placeholder="My Webhook Channel" />
          </div>
          <button
            class="btn btn-primary"
            :disabled="creating || !newChannel.name"
            @click="createChannel"
          >
            {{ creating ? 'Creating…' : 'Create Channel' }}
          </button>
        </div>

        <DataTable
          v-if="channels.length"
          :columns="channelColumns"
          :rows="channels"
          :actions="[
            { label: '🧪 Test', handler: (r) => testChannel(r.id) },
            { label: '🔄 Toggle', handler: (r) => toggleChannel(r) },
            { label: '🗑️ Delete', handler: (r) => deleteChannel(r.id), danger: true },
          ]"
        />
        <p v-else class="empty-state">No channels configured. Add one to get started.</p>
      </section>

      <!-- Messages Section -->
      <section class="section">
        <div class="section-header">
          <h3>Message History</h3>
          <button class="btn btn-secondary" @click="fetchAll">↻ Refresh</button>
        </div>

        <DataTable
          v-if="messages.length"
          :columns="messageColumns"
          :rows="messages"
        />
        <p v-else class="empty-state">No messages yet.</p>
      </section>
    </template>
  </AppLayout>
</template>

<style scoped>
.section { margin-bottom: 2rem; }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
.create-form { padding: 1rem; margin-bottom: 1rem; }
.form-row { margin-bottom: 0.75rem; }
.form-row label { display: block; font-weight: 600; margin-bottom: 0.25rem; }
.form-row input, .form-row select { width: 100%; padding: 0.5rem; border: 1px solid var(--border-color, #ccc); border-radius: 4px; }
.empty-state { color: var(--text-muted, #888); font-style: italic; text-align: center; padding: 2rem; }
</style>
