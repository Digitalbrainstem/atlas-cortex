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
const showRawJson = ref(false)
const showPasswords = ref({})

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
    showRawJson.value = false
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

function onFieldInput(key, value) {
  editConfig.value = { ...editConfig.value, [key]: value }
}

function togglePasswordVisibility(key) {
  showPasswords.value = { ...showPasswords.value, [key]: !showPasswords.value[key] }
}

function hasConfigFields(plugin) {
  return plugin.config_fields && plugin.config_fields.length > 0
}

function missingRequired(plugin) {
  if (!plugin.config_fields) return []
  return plugin.config_fields.filter(
    f => f.required && !plugin.config[f.key]
  )
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
    if (p) {
      p.config = JSON.parse(JSON.stringify(editConfig.value))
      // Recompute needs_setup locally
      if (p.config_fields) {
        p.needs_setup = p.config_fields.some(f => f.required && !editConfig.value[f.key])
      }
    }
    success.value = 'Config saved — restart the server to apply changes'
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
    if (p) {
      p.health_ok = data.health_ok
      if (data.health_message) p.health_message = data.health_message
    }
    success.value = `Health: ${data.health_ok ? 'OK' : 'FAIL'}${data.health_message ? ' — ' + data.health_message : ''}`
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
              <span v-if="p.needs_setup" class="badge badge-setup">Needs Setup</span>
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
              <span :title="p.health_message || ''">{{ p.health_ok ? 'Healthy' : 'Unhealthy' }}</span>
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

                <!-- Health message banner -->
                <div v-if="p.health_message && !p.health_ok" class="health-banner">
                  ℹ️ {{ p.health_message }}
                </div>

                <!-- Needs setup banner -->
                <div v-if="p.needs_setup && missingRequired(p).length" class="setup-banner">
                  ⚠️ Missing required config: {{ missingRequired(p).map(f => f.label).join(', ') }}
                </div>

                <h4>Configuration</h4>

                <!-- Schema-driven form fields -->
                <div v-if="hasConfigFields(p) && !showRawJson" class="config-fields">
                  <div v-for="field in p.config_fields" :key="field.key" class="config-field">
                    <label class="field-label">
                      {{ field.label }}
                      <span v-if="field.required" class="required-marker">*</span>
                    </label>

                    <!-- Text input -->
                    <input
                      v-if="field.field_type === 'text'"
                      type="text"
                      class="field-input"
                      :placeholder="field.placeholder"
                      :value="editConfig[field.key] || ''"
                      @input="onFieldInput(field.key, $event.target.value)"
                    />

                    <!-- Password input with show/hide -->
                    <div v-else-if="field.field_type === 'password'" class="password-wrapper">
                      <input
                        :type="showPasswords[field.key] ? 'text' : 'password'"
                        class="field-input"
                        :placeholder="field.placeholder"
                        :value="editConfig[field.key] || ''"
                        @input="onFieldInput(field.key, $event.target.value)"
                      />
                      <button
                        type="button"
                        class="btn btn-sm password-toggle"
                        @click="togglePasswordVisibility(field.key)"
                      >
                        {{ showPasswords[field.key] ? '🙈' : '👁' }}
                      </button>
                    </div>

                    <!-- URL input -->
                    <input
                      v-else-if="field.field_type === 'url'"
                      type="url"
                      class="field-input"
                      :placeholder="field.placeholder"
                      :value="editConfig[field.key] || ''"
                      @input="onFieldInput(field.key, $event.target.value)"
                    />

                    <!-- Number input -->
                    <input
                      v-else-if="field.field_type === 'number'"
                      type="number"
                      class="field-input"
                      :placeholder="field.placeholder"
                      :value="editConfig[field.key] ?? field.default ?? ''"
                      @input="onFieldInput(field.key, parseFloat($event.target.value) || '')"
                    />

                    <!-- Toggle switch -->
                    <label v-else-if="field.field_type === 'toggle'" class="toggle-field">
                      <input
                        type="checkbox"
                        :checked="editConfig[field.key] ?? field.default ?? false"
                        @change="onFieldInput(field.key, $event.target.checked)"
                      />
                      <span class="toggle-label">{{ editConfig[field.key] ? 'Enabled' : 'Disabled' }}</span>
                    </label>

                    <!-- Select dropdown -->
                    <select
                      v-else-if="field.field_type === 'select'"
                      class="field-input"
                      :value="editConfig[field.key] || field.default || ''"
                      @change="onFieldInput(field.key, $event.target.value)"
                    >
                      <option value="" disabled>Select…</option>
                      <option
                        v-for="opt in (field.options || [])"
                        :key="opt.value"
                        :value="opt.value"
                      >
                        {{ opt.label }}
                      </option>
                    </select>

                    <!-- Fallback text input -->
                    <input
                      v-else
                      type="text"
                      class="field-input"
                      :placeholder="field.placeholder"
                      :value="editConfig[field.key] || ''"
                      @input="onFieldInput(field.key, $event.target.value)"
                    />

                    <p v-if="field.help_text" class="field-help">{{ field.help_text }}</p>
                  </div>
                </div>

                <!-- Raw JSON editor (collapsed by default, always available) -->
                <div class="raw-json-toggle">
                  <button class="btn btn-sm btn-ghost" @click="showRawJson = !showRawJson">
                    {{ showRawJson ? '← Back to form' : '{ } Raw JSON' }}
                  </button>
                </div>

                <div v-if="showRawJson || !hasConfigFields(p)">
                  <textarea
                    class="config-editor"
                    :value="configText(editConfig)"
                    @input="onConfigInput"
                    rows="6"
                    spellcheck="false"
                  ></textarea>
                </div>

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
.badge-setup {
  font-size: 0.65rem;
  padding: 0.1rem 0.4rem;
  border-radius: var(--radius);
  background: rgba(245,158,11,0.15);
  color: #f59e0b;
  margin-left: 0.4rem;
}

.health-banner {
  background: rgba(59,130,246,0.1);
  border: 1px solid rgba(59,130,246,0.2);
  border-radius: var(--radius);
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.setup-banner {
  background: rgba(245,158,11,0.1);
  border: 1px solid rgba(245,158,11,0.2);
  border-radius: var(--radius);
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
  color: #f59e0b;
}

/* Schema-driven form fields */
.config-fields {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  margin-bottom: 1rem;
}

.config-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.field-label {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-primary);
}

.required-marker {
  color: #ef4444;
  margin-left: 0.15rem;
}

.field-input {
  width: 100%;
  max-width: 480px;
  font-size: 0.85rem;
  background: var(--bg-primary);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.5rem 0.75rem;
}
.field-input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(59,130,246,0.15);
}
.field-input::placeholder {
  color: var(--text-muted);
}

select.field-input {
  cursor: pointer;
}

.password-wrapper {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  max-width: 520px;
}
.password-wrapper .field-input {
  max-width: none;
  flex: 1;
}
.password-toggle {
  flex-shrink: 0;
  border: 1px solid var(--border);
  background: var(--bg-primary);
  cursor: pointer;
  padding: 0.4rem 0.5rem;
}

.toggle-field {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  cursor: pointer;
}
.toggle-field input { cursor: pointer; }

.field-help {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin: 0;
  max-width: 480px;
}

.raw-json-toggle {
  margin-bottom: 0.5rem;
}

.btn-ghost {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  cursor: pointer;
}
.btn-ghost:hover {
  background: rgba(255,255,255,0.05);
  color: var(--text-primary);
}

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
