<script setup>
import { ref, onMounted, computed } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api.js'

const plugins = ref([])
const loading = ref(true)
const error = ref('')
const success = ref('')
const expandedId = ref(null)
const editConfig = ref({})
const savingConfig = ref(false)
const healthChecking = ref(null)

onMounted(() => fetchPlugins())

async function fetchPlugins() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/admin/plugins')
    plugins.value = data.plugins || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function toggleExpand(pid) {
  if (expandedId.value === pid) {
    expandedId.value = null
  } else {
    expandedId.value = pid
    const p = plugins.value.find(x => x.plugin_id === pid)
    editConfig.value = p ? JSON.parse(JSON.stringify(p.config || {})) : {}
  }
}

function configText(cfg) {
  return JSON.stringify(cfg || {}, null, 2)
}

function onConfigInput(e) {
  try {
    editConfig.value = JSON.parse(e.target.value)
    error.value = ''
  } catch {
    // user is still typing
  }
}

async function toggleEnabled(plugin) {
  error.value = ''
  success.value = ''
  const action = plugin.enabled ? 'disable' : 'enable'
  try {
    await api.post(`/admin/plugins/${plugin.plugin_id}/${action}`)
    plugin.enabled = !plugin.enabled
    success.value = `Plugin ${plugin.plugin_id} ${action}d`
  } catch (e) {
    error.value = e.message
  }
}

async function saveConfig(pid) {
  savingConfig.value = true
  error.value = ''
  success.value = ''
  try {
    await api.patch(`/admin/plugins/${pid}/config`, { config: editConfig.value })
    const p = plugins.value.find(x => x.plugin_id === pid)
    if (p) p.config = JSON.parse(JSON.stringify(editConfig.value))
    success.value = 'Config saved'
  } catch (e) {
    error.value = e.message
  } finally {
    savingConfig.value = false
  }
}

async function runHealthCheck(pid) {
  healthChecking.value = pid
  error.value = ''
  success.value = ''
  try {
    const data = await api.post(`/admin/plugins/${pid}/health`)
    const p = plugins.value.find(x => x.plugin_id === pid)
    if (p) p.health_ok = data.health_ok
    success.value = `Health: ${data.health_ok ? 'OK' : 'FAIL'}`
  } catch (e) {
    error.value = e.message
  } finally {
    healthChecking.value = null
  }
}

function sourceBadge(source) {
  return source === 'community' ? '🌐 Community' : '✅ Official'
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Plugins</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <div v-if="loading" class="loading-text">Loading plugins…</div>

    <table v-else class="table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Type</th>
          <th>Source</th>
          <th>Status</th>
          <th>Health</th>
          <th>Hits</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!plugins.length">
          <td colspan="7" class="loading-text">No plugins found</td>
        </tr>
        <template v-for="p in plugins" :key="p.plugin_id">
          <tr class="plugin-row" @click="toggleExpand(p.plugin_id)">
            <td>
              <strong>{{ p.display_name || p.plugin_id }}</strong>
              <span v-if="p.version !== '0.0.0'" class="version-badge">v{{ p.version }}</span>
            </td>
            <td>{{ p.plugin_type }}</td>
            <td><span class="source-badge" :class="p.source">{{ sourceBadge(p.source) }}</span></td>
            <td>
              <label class="toggle" @click.stop>
                <input type="checkbox" :checked="p.enabled" @change="toggleEnabled(p)" />
                <span class="toggle-label">{{ p.enabled ? 'Enabled' : 'Disabled' }}</span>
              </label>
            </td>
            <td>
              <span class="health-dot" :class="{ ok: p.health_ok, fail: !p.health_ok }"></span>
              {{ p.health_ok ? 'Healthy' : 'Unhealthy' }}
            </td>
            <td>{{ p.hit_count }}</td>
            <td>
              <button
                class="btn btn-sm"
                :disabled="healthChecking === p.plugin_id"
                @click.stop="runHealthCheck(p.plugin_id)"
              >
                {{ healthChecking === p.plugin_id ? '…' : '🔍 Check' }}
              </button>
            </td>
          </tr>
          <tr v-if="expandedId === p.plugin_id" class="config-row">
            <td colspan="7">
              <div class="config-panel">
                <div class="config-meta">
                  <span v-if="p.author"><strong>Author:</strong> {{ p.author }}</span>
                  <span v-if="p.supports_learning" class="badge">Supports Learning</span>
                  <span v-if="!p.registered" class="badge badge-warn">Not Registered</span>
                </div>
                <h4>Configuration</h4>
                <textarea
                  class="config-editor"
                  :value="configText(editConfig)"
                  @input="onConfigInput"
                  rows="6"
                  spellcheck="false"
                ></textarea>
                <div class="config-actions">
                  <button class="btn btn-primary" :disabled="savingConfig" @click="saveConfig(p.plugin_id)">
                    {{ savingConfig ? 'Saving…' : 'Save Config' }}
                  </button>
                </div>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </AppLayout>
</template>

<style scoped>
.plugin-row { cursor: pointer; }
.plugin-row:hover { background-color: rgba(255,255,255,0.03); }

.version-badge {
  font-size: 0.7rem;
  color: var(--text-muted);
  margin-left: 0.4rem;
}

.source-badge {
  font-size: 0.8rem;
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius);
  white-space: nowrap;
}
.source-badge.official { background: rgba(34,197,94,0.1); color: #22c55e; }
.source-badge.community { background: rgba(59,130,246,0.1); color: #3b82f6; }

.toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  cursor: pointer;
}
.toggle input { cursor: pointer; }
.toggle-label { font-size: 0.85rem; }

.health-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 0.3rem;
}
.health-dot.ok { background: #22c55e; }
.health-dot.fail { background: #ef4444; }

.config-row td { padding: 0 !important; }

.config-panel {
  padding: 1rem 1.5rem;
  background: rgba(255,255,255,0.02);
  border-top: 1px solid var(--border);
}

.config-meta {
  display: flex;
  gap: 1rem;
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.badge {
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  border-radius: var(--radius);
  background: rgba(59,130,246,0.15);
  color: var(--accent);
}
.badge-warn { background: rgba(245,158,11,0.15); color: #f59e0b; }

.config-editor {
  width: 100%;
  font-family: 'Fira Code', 'Cascadia Code', monospace;
  font-size: 0.85rem;
  background: var(--bg-primary);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.75rem;
  resize: vertical;
}

.config-actions {
  margin-top: 0.5rem;
  display: flex;
  gap: 0.5rem;
}

.btn-sm {
  font-size: 0.8rem;
  padding: 0.25rem 0.5rem;
}
</style>
