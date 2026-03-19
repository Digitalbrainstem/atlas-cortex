<script setup>
import { ref, onMounted, computed } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import AvatarPreview from '../components/AvatarPreview.vue'
import { api } from '../api.js'

const loading = ref(true)
const error = ref('')
const success = ref('')
const skins = ref([])
const assignments = ref([])
const users = ref([])

// Feature flags
const flags = ref({})
const flagsLoading = ref(false)

const FLAG_LABELS = {
  show_mic: '🎤 Microphone button',
  show_skin_switcher: '🎨 Skin switcher',
  show_joke_button: '😂 Joke button',
  show_controls: '🎛️ Control bar',
  show_debug: '🔍 Debug info',
  satellite_mode: '🛰️ Satellite mode',
  dev_mode: '🔧 Developer mode (shows everything)',
}

const globalFlags = computed(() => {
  const g = flags.value['global'] || {}
  return Object.keys(FLAG_LABELS)
    .filter(k => k !== 'dev_mode')
    .map(k => ({ name: k, label: FLAG_LABELS[k], enabled: !!g[k] }))
})

const devMode = computed(() => !!(flags.value['global'] || {}).dev_mode)

const userScopes = computed(() => {
  return Object.keys(flags.value).filter(k => k !== 'global').map(scope => ({
    scope,
    flags: Object.entries(flags.value[scope] || {}).map(([name, enabled]) => ({
      name, label: FLAG_LABELS[name] || name, enabled: !!enabled
    }))
  }))
})

// New skin form
const showNewSkin = ref(false)
const newSkin = ref({ id: '', name: '', type: 'svg', path: '' })

// Per-user override form
const overrideUserId = ref('')
const overrideFlag = ref('show_mic')
const overrideEnabled = ref(true)

onMounted(async () => {
  await Promise.all([fetchSkins(), fetchAssignments(), fetchUsers(), fetchFlags()])
  loading.value = false
})

async function fetchSkins() {
  try {
    skins.value = await api.get('/admin/avatar/skins')
  } catch (e) {
    error.value = e.message
  }
}

async function fetchAssignments() {
  try {
    assignments.value = await api.get('/admin/avatar/assignments')
  } catch (e) {
    // Assignments may be empty, that's fine
  }
}

async function fetchUsers() {
  try {
    const data = await api.get('/admin/users')
    users.value = data.users || data.items || data || []
  } catch (e) {
    // Users endpoint may not return expected format
  }
}

async function fetchFlags() {
  try {
    flagsLoading.value = true
    flags.value = await api.get('/admin/avatar/flags')
  } catch (e) {
    error.value = e.message
  } finally {
    flagsLoading.value = false
  }
}

async function toggleFlag(scope, flagName, event) {
  error.value = ''
  success.value = ''
  try {
    await api.patch('/admin/avatar/flags', {
      scope,
      flag_name: flagName,
      enabled: event.target.checked
    })
    await fetchFlags()
    success.value = `Flag "${flagName}" updated`
  } catch (e) {
    error.value = e.message
    await fetchFlags()
  }
}

async function toggleDevMode(event) {
  error.value = ''
  success.value = ''
  try {
    await api.post('/admin/avatar/flags/dev-mode', { enabled: event.target.checked })
    await fetchFlags()
    success.value = event.target.checked ? 'Dev mode enabled' : 'Dev mode disabled'
  } catch (e) {
    error.value = e.message
    await fetchFlags()
  }
}

async function addUserOverride() {
  if (!overrideUserId.value) return
  error.value = ''
  success.value = ''
  try {
    await api.patch('/admin/avatar/flags', {
      scope: overrideUserId.value,
      flag_name: overrideFlag.value,
      enabled: overrideEnabled.value
    })
    await fetchFlags()
    success.value = `Override added for ${overrideUserId.value}`
    overrideUserId.value = ''
  } catch (e) {
    error.value = e.message
  }
}

async function resetFlags(scope) {
  error.value = ''
  success.value = ''
  try {
    await api.post(`/admin/avatar/flags/reset?scope=${encodeURIComponent(scope)}`)
    await fetchFlags()
    success.value = `Flags reset for ${scope}`
  } catch (e) {
    error.value = e.message
  }
}

async function setDefault(skinId) {
  error.value = ''
  success.value = ''
  try {
    await api.put(`/admin/avatar/default/${skinId}`)
    skins.value.forEach(s => s.is_default = (s.id === skinId))
    success.value = 'Default skin updated'
  } catch (e) {
    error.value = e.message
  }
}

async function deleteSkin(skinId) {
  if (!confirm(`Delete skin "${skinId}"?`)) return
  error.value = ''
  try {
    await api.delete(`/admin/avatar/skins/${skinId}`)
    skins.value = skins.value.filter(s => s.id !== skinId)
    success.value = 'Skin deleted'
  } catch (e) {
    error.value = e.message
  }
}

async function createSkin() {
  error.value = ''
  success.value = ''
  try {
    await api.post('/admin/avatar/skins', newSkin.value)
    showNewSkin.value = false
    newSkin.value = { id: '', name: '', type: 'svg', path: '' }
    await fetchSkins()
    success.value = 'Skin created'
  } catch (e) {
    error.value = e.message
  }
}

async function assignSkin(userId, skinId) {
  error.value = ''
  success.value = ''
  try {
    if (skinId === '_default') {
      await api.delete(`/admin/avatar/assignments/${userId}`)
    } else {
      await api.put(`/admin/avatar/assignments/${userId}`, { skin_id: skinId })
    }
    await fetchAssignments()
    success.value = 'Assignment updated'
  } catch (e) {
    error.value = e.message
  }
}

function getUserSkin(userId) {
  const a = assignments.value.find(a => a.user_id === userId)
  return a ? a.skin_id : '_default'
}
</script>

<template>
  <AppLayout>
    <div class="avatar-page">
      <div class="page-header">
        <h1>🎭 Avatar Skins</h1>
        <button class="btn btn-primary" @click="showNewSkin = !showNewSkin">
          {{ showNewSkin ? 'Cancel' : '+ New Skin' }}
        </button>
      </div>

      <div v-if="error" class="alert alert-error">{{ error }}</div>
      <div v-if="success" class="alert alert-success">{{ success }}</div>

      <!-- Feature Flags -->
      <div class="section flags-section">
        <h2>🚩 Feature Flags</h2>
        <p class="hint">Control which UI elements are visible on the avatar display page.</p>

        <h3>Global Defaults</h3>
        <div class="flag-row" v-for="flag in globalFlags" :key="flag.name">
          <label class="toggle">
            <input type="checkbox" :checked="flag.enabled" @change="toggleFlag('global', flag.name, $event)">
            <span>{{ flag.label }}</span>
          </label>
        </div>

        <h3>Dev Mode</h3>
        <div class="flag-row">
          <label class="toggle">
            <input type="checkbox" :checked="devMode" @change="toggleDevMode">
            <span>🔧 Developer Debug Mode (shows all controls + debug overlay)</span>
          </label>
        </div>
        <button class="btn btn-sm" @click="resetFlags('global')" style="margin-top:0.5rem">Reset Global Defaults</button>

        <h3>Per-User Overrides</h3>
        <div v-if="userScopes.length" class="user-overrides">
          <div v-for="us in userScopes" :key="us.scope" class="user-override-group">
            <div class="user-override-header">
              <strong>{{ us.scope }}</strong>
              <button class="btn btn-sm btn-danger" @click="resetFlags(us.scope)">Remove</button>
            </div>
            <div class="flag-row" v-for="f in us.flags" :key="f.name">
              <label class="toggle">
                <input type="checkbox" :checked="f.enabled" @change="toggleFlag(us.scope, f.name, $event)">
                <span>{{ f.label }}</span>
              </label>
            </div>
          </div>
        </div>
        <div v-else class="hint">No per-user overrides configured.</div>
        <div class="add-override">
          <select v-model="overrideUserId">
            <option value="">Select user…</option>
            <option v-for="u in users" :key="u.id || u.user_id" :value="u.id || u.user_id">
              {{ u.display_name || u.name || u.user_id || u.id }}
            </option>
          </select>
          <select v-model="overrideFlag">
            <option v-for="(label, key) in FLAG_LABELS" :key="key" :value="key">{{ label }}</option>
          </select>
          <label class="toggle inline-toggle">
            <input type="checkbox" v-model="overrideEnabled"> <span>Enabled</span>
          </label>
          <button class="btn btn-sm btn-primary" @click="addUserOverride" :disabled="!overrideUserId">Add Override</button>
        </div>
      </div>

      <!-- New skin form -->
      <div v-if="showNewSkin" class="card new-skin-form">
        <h3>Add New Skin</h3>
        <div class="form-row">
          <label>ID <input v-model="newSkin.id" placeholder="my-robot" /></label>
          <label>Name <input v-model="newSkin.name" placeholder="Friendly Robot" /></label>
          <label>Type
            <select v-model="newSkin.type">
              <option value="svg">SVG</option>
              <option value="sprite">Sprite</option>
              <option value="custom">Custom</option>
            </select>
          </label>
          <label>Path <input v-model="newSkin.path" placeholder="cortex/avatar/skins/robot.svg" /></label>
        </div>
        <button class="btn btn-primary" @click="createSkin" :disabled="!newSkin.id || !newSkin.name">Create</button>
      </div>

      <!-- Skin grid -->
      <div v-if="loading" class="loading">Loading…</div>
      <div v-else class="skin-grid">
        <div v-for="skin in skins" :key="skin.id" class="skin-card" :class="{ 'is-default': skin.is_default }">
          <AvatarPreview :skin-id="skin.id" :size="160" :animate="true" />
          <div class="skin-info">
            <strong>{{ skin.name }}</strong>
            <span class="skin-id">{{ skin.id }}</span>
            <span v-if="skin.is_default" class="badge badge-default">Default</span>
          </div>
          <div class="skin-actions">
            <button v-if="!skin.is_default" class="btn btn-sm" @click="setDefault(skin.id)">Set Default</button>
            <button v-if="!skin.is_default" class="btn btn-sm btn-danger" @click="deleteSkin(skin.id)">Delete</button>
          </div>
        </div>
      </div>

      <!-- User assignments -->
      <div class="section" v-if="users.length">
        <h2>👤 User Assignments</h2>
        <p class="hint">Assign a specific avatar to each user, or leave as "Default" to use the system default.</p>
        <table class="assign-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Assigned Skin</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in users" :key="user.id || user.user_id">
              <td>{{ user.display_name || user.name || user.user_id || user.id }}</td>
              <td>
                <select :value="getUserSkin(user.id || user.user_id)" @change="assignSkin(user.id || user.user_id, $event.target.value)">
                  <option value="_default">🌐 Default</option>
                  <option v-for="skin in skins" :key="skin.id" :value="skin.id">{{ skin.name }}</option>
                </select>
              </td>
              <td>
                <AvatarPreview :skin-id="getUserSkin(user.id || user.user_id) === '_default' ? (skins.find(s => s.is_default)?.id || 'default') : getUserSkin(user.id || user.user_id)" :size="48" :animate="false" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Display link -->
      <div class="section">
        <h2>📺 Avatar Display</h2>
        <p class="hint">Open this URL on a tablet, smart display, or any browser to show the live avatar:</p>
        <code class="display-url">{{ displayUrl }}</code>
      </div>
    </div>
  </AppLayout>
</template>

<script>
export default {
  computed: {
    displayUrl() {
      return `${location.origin}/avatar?room=default`
    }
  }
}
</script>

<style scoped>
.avatar-page { max-width: 1100px; }

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}
.page-header h1 { font-size: 1.5rem; color: var(--text-primary); }

.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 1.25rem;
  margin-bottom: 1.5rem;
}

.new-skin-form h3 { margin-bottom: 1rem; color: var(--text-primary); }
.form-row {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 1rem;
}
.form-row label {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  font-size: 0.8rem;
  color: var(--text-muted);
}
.form-row input, .form-row select {
  padding: 0.5rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-primary);
  font-size: 0.875rem;
}

.skin-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1.25rem;
  margin-bottom: 2rem;
}

.skin-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 1rem;
  text-align: center;
  transition: border-color 0.2s;
}
.skin-card.is-default { border-color: #3b82f6; }
.skin-card:hover { border-color: #64748b; }

.skin-info {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  margin: 0.75rem 0;
}
.skin-info strong { color: var(--text-primary); font-size: 0.95rem; }
.skin-id { color: var(--text-muted); font-size: 0.75rem; font-family: monospace; }

.badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 600;
}
.badge-default { background: #3b82f620; color: #3b82f6; }

.skin-actions {
  display: flex;
  gap: 0.5rem;
  justify-content: center;
}

.btn {
  padding: 0.5rem 1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-card);
  color: var(--text-primary);
  cursor: pointer;
  font-size: 0.8rem;
  transition: all 0.15s;
}
.btn:hover { background: var(--bg-primary); }
.btn-primary { background: #3b82f6; border-color: #3b82f6; color: white; }
.btn-primary:hover { background: #2563eb; }
.btn-danger { color: #ef4444; border-color: #ef444440; }
.btn-danger:hover { background: #ef444420; }
.btn-sm { padding: 0.3rem 0.6rem; font-size: 0.75rem; }

.section { margin-top: 2rem; }
.section h2 { font-size: 1.2rem; color: var(--text-primary); margin-bottom: 0.5rem; }
.hint { color: var(--text-muted); font-size: 0.85rem; margin-bottom: 1rem; }

/* Feature flags */
.flags-section { margin-bottom: 2rem; }
.flags-section h3 {
  font-size: 0.95rem; color: var(--text-primary);
  margin: 1rem 0 0.5rem; padding-top: 0.5rem;
  border-top: 1px solid var(--border);
}
.flags-section h3:first-of-type { border-top: none; padding-top: 0; }
.flag-row {
  padding: 0.4rem 0;
}
.toggle {
  display: flex; align-items: center; gap: 0.6rem;
  cursor: pointer; font-size: 0.875rem; color: var(--text-primary);
}
.toggle input[type="checkbox"] {
  width: 1.1rem; height: 1.1rem; accent-color: #3b82f6; cursor: pointer;
}
.user-overrides { margin-bottom: 1rem; }
.user-override-group {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 0.75rem; margin-bottom: 0.5rem;
}
.user-override-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 0.4rem;
}
.user-override-header strong { color: var(--text-primary); font-size: 0.85rem; }
.add-override {
  display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;
  margin-top: 0.75rem;
}
.add-override select {
  padding: 0.4rem; background: var(--bg-primary); border: 1px solid var(--border);
  border-radius: var(--radius); color: var(--text-primary); font-size: 0.8rem;
}
.inline-toggle { gap: 0.3rem; font-size: 0.8rem; }

.assign-table {
  width: 100%;
  border-collapse: collapse;
}
.assign-table th, .assign-table td {
  padding: 0.75rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
}
.assign-table th {
  color: var(--text-muted);
  font-size: 0.75rem;
  text-transform: uppercase;
  font-weight: 600;
}
.assign-table select {
  padding: 0.4rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-primary);
}

.display-url {
  display: block;
  padding: 0.75rem 1rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: #3b82f6;
  font-size: 0.9rem;
  word-break: break-all;
}

.alert {
  padding: 0.75rem 1rem;
  border-radius: var(--radius);
  margin-bottom: 1rem;
  font-size: 0.85rem;
}
.alert-error { background: #ef444420; color: #ef4444; border: 1px solid #ef444440; }
.alert-success { background: #22c55e20; color: #22c55e; border: 1px solid #22c55e40; }

.loading { color: var(--text-muted); padding: 2rem; text-align: center; }
</style>
