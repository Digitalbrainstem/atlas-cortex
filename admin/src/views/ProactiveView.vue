<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api.js'

const activeTab = ref('rules')
const loading = ref(true)
const error = ref('')
const success = ref('')

// Data
const rules = ref([])
const events = ref([])
const briefingText = ref('')
const briefingSections = ref(null)
const briefingLoading = ref(false)

// Preferences
const prefsUserId = ref('')
const prefs = ref(null)
const prefsLoading = ref(false)

// New rule form
const newRule = ref({
  name: '', provider: 'weather', condition_type: 'threshold',
  condition_config: '{}', action_type: 'notify', action_config: '{}',
  priority: 'normal', cooldown_minutes: 60,
})
const creating = ref(false)

onMounted(() => fetchAll())

async function fetchAll() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([fetchRules(), fetchEvents()])
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function fetchRules() {
  const data = await api.get('/admin/proactive/rules')
  rules.value = data.rules || []
}

async function fetchEvents() {
  const data = await api.get('/admin/proactive/events')
  events.value = data.events || []
}

async function createRule() {
  if (!newRule.value.name) { error.value = 'Name is required'; return }
  creating.value = true
  error.value = ''
  success.value = ''
  try {
    let condCfg, actCfg
    try { condCfg = JSON.parse(newRule.value.condition_config) } catch { condCfg = {} }
    try { actCfg = JSON.parse(newRule.value.action_config) } catch { actCfg = {} }
    await api.post('/admin/proactive/rules', {
      ...newRule.value,
      condition_config: condCfg,
      action_config: actCfg,
    })
    newRule.value = {
      name: '', provider: 'weather', condition_type: 'threshold',
      condition_config: '{}', action_type: 'notify', action_config: '{}',
      priority: 'normal', cooldown_minutes: 60,
    }
    await fetchRules()
    success.value = 'Rule created'
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}

async function deleteRule(id) {
  error.value = ''
  try {
    await api.delete(`/admin/proactive/rules/${id}`)
    await fetchRules()
    success.value = 'Rule deleted'
  } catch (e) { error.value = e.message }
}

async function toggleRule(rule) {
  error.value = ''
  const action = rule.enabled ? 'disable' : 'enable'
  try {
    await api.post(`/admin/proactive/rules/${rule.id}/${action}`)
    rule.enabled = rule.enabled ? 0 : 1
    success.value = `Rule ${action}d`
  } catch (e) { error.value = e.message }
}

async function previewBriefing() {
  briefingLoading.value = true
  error.value = ''
  try {
    const data = await api.get('/admin/proactive/briefing')
    briefingText.value = data.text || ''
    briefingSections.value = data.sections || null
  } catch (e) { error.value = e.message }
  finally { briefingLoading.value = false }
}

async function loadPreferences() {
  if (!prefsUserId.value) { error.value = 'Enter a user ID'; return }
  prefsLoading.value = true
  error.value = ''
  try {
    prefs.value = await api.get(`/admin/proactive/preferences/${prefsUserId.value}`)
  } catch (e) { error.value = e.message }
  finally { prefsLoading.value = false }
}

async function savePreferences() {
  if (!prefsUserId.value || !prefs.value) return
  error.value = ''
  try {
    await api.patch(`/admin/proactive/preferences/${prefsUserId.value}`, prefs.value)
    success.value = 'Preferences saved'
  } catch (e) { error.value = e.message }
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">⚡ Proactive Intelligence</h2>

    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <!-- Tabs -->
    <div class="tabs">
      <button v-for="tab in ['rules', 'events', 'briefing', 'preferences']"
        :key="tab" class="tab-btn" :class="{ active: activeTab === tab }"
        @click="activeTab = tab">
        {{ tab === 'rules' ? '📋 Rules' : tab === 'events' ? '📜 Events'
           : tab === 'briefing' ? '☀️ Briefing' : '🔔 Preferences' }}
      </button>
    </div>

    <div v-if="loading" class="loading-text">Loading…</div>

    <!-- Rules Tab -->
    <div v-else-if="activeTab === 'rules'">
      <div class="add-form">
        <h4>Create Rule</h4>
        <div class="form-row">
          <input v-model="newRule.name" placeholder="Rule name" class="input" />
          <select v-model="newRule.provider" class="input">
            <option v-for="p in ['weather','energy','anomaly','calendar']" :key="p">{{ p }}</option>
          </select>
          <select v-model="newRule.condition_type" class="input">
            <option v-for="c in ['threshold','change','pattern','schedule']" :key="c">{{ c }}</option>
          </select>
          <select v-model="newRule.action_type" class="input">
            <option v-for="a in ['log','notify','routine']" :key="a">{{ a }}</option>
          </select>
        </div>
        <div class="form-row" style="margin-top: 0.5rem;">
          <input v-model="newRule.condition_config" placeholder='Condition config JSON' class="input" />
          <input v-model="newRule.action_config" placeholder='Action config JSON' class="input" />
          <select v-model="newRule.priority" class="input" style="max-width:120px">
            <option v-for="p in ['low','normal','high','critical']" :key="p">{{ p }}</option>
          </select>
          <button class="btn btn-primary" :disabled="creating" @click="createRule">
            {{ creating ? 'Creating…' : '+ Add' }}
          </button>
        </div>
      </div>

      <table class="table">
        <thead>
          <tr>
            <th>ID</th><th>Name</th><th>Provider</th><th>Type</th>
            <th>Priority</th><th>Fired</th><th>Enabled</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rules.length"><td colspan="8" class="loading-text">No rules</td></tr>
          <tr v-for="r in rules" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.name }}</td>
            <td>{{ r.provider }}</td>
            <td>{{ r.condition_type }}</td>
            <td><span class="priority-badge" :class="r.priority">{{ r.priority }}</span></td>
            <td>{{ r.fire_count || 0 }}</td>
            <td>
              <label class="toggle" @click.stop>
                <input type="checkbox" :checked="r.enabled" @change="toggleRule(r)" />
                <span class="toggle-label">{{ r.enabled ? 'On' : 'Off' }}</span>
              </label>
            </td>
            <td>
              <button class="btn btn-sm btn-danger" @click="deleteRule(r.id)">🗑️ Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Events Tab -->
    <div v-else-if="activeTab === 'events'">
      <div class="section-header">
        <h3>Recent Events</h3>
        <button class="btn btn-sm" @click="fetchEvents">🔄 Refresh</button>
      </div>
      <table class="table">
        <thead>
          <tr><th>ID</th><th>Rule</th><th>Provider</th><th>Type</th><th>Action</th><th>Time</th></tr>
        </thead>
        <tbody>
          <tr v-if="!events.length"><td colspan="6" class="loading-text">No events</td></tr>
          <tr v-for="e in events" :key="e.id">
            <td>{{ e.id }}</td>
            <td>{{ e.rule_id || '—' }}</td>
            <td>{{ e.provider }}</td>
            <td>{{ e.event_type }}</td>
            <td>{{ e.action_taken || '—' }}</td>
            <td>{{ e.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Briefing Tab -->
    <div v-else-if="activeTab === 'briefing'">
      <div class="section-header">
        <h3>Daily Briefing Preview</h3>
        <button class="btn btn-primary" :disabled="briefingLoading" @click="previewBriefing">
          {{ briefingLoading ? 'Generating…' : '☀️ Preview Briefing' }}
        </button>
      </div>
      <div v-if="briefingText" class="briefing-card">
        <p>{{ briefingText }}</p>
      </div>
      <div v-if="briefingSections" class="briefing-sections">
        <div v-for="(val, key) in briefingSections" :key="key" class="briefing-section">
          <strong>{{ key }}:</strong>
          <pre>{{ typeof val === 'object' ? JSON.stringify(val, null, 2) : val }}</pre>
        </div>
      </div>
    </div>

    <!-- Preferences Tab -->
    <div v-else-if="activeTab === 'preferences'">
      <div class="section-header">
        <h3>Notification Preferences</h3>
      </div>
      <div class="form-row" style="margin-bottom: 1rem;">
        <input v-model="prefsUserId" placeholder="User ID" class="input" />
        <button class="btn btn-primary" :disabled="prefsLoading" @click="loadPreferences">Load</button>
      </div>
      <div v-if="prefs" class="add-form">
        <div class="form-row">
          <label class="form-label">Quiet Start
            <input v-model="prefs.quiet_hours_start" class="input" />
          </label>
          <label class="form-label">Quiet End
            <input v-model="prefs.quiet_hours_end" class="input" />
          </label>
          <label class="form-label">Min Priority
            <select v-model="prefs.min_priority" class="input">
              <option v-for="p in ['low','normal','high','critical']" :key="p">{{ p }}</option>
            </select>
          </label>
          <label class="form-label">Max/Hour
            <input v-model.number="prefs.max_per_hour" type="number" class="input" />
          </label>
        </div>
        <button class="btn btn-primary" style="margin-top: 0.75rem;" @click="savePreferences">
          Save Preferences
        </button>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 1.5rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0;
}
.tab-btn {
  padding: 0.6rem 1.2rem;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 0.9rem;
  transition: color 0.15s, border-color 0.15s;
}
.tab-btn:hover { color: var(--text-primary); }
.tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}
.section-header h3 { margin: 0; }

.add-form {
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem;
  margin-bottom: 1.5rem;
}
.add-form h4 { margin: 0 0 0.75rem 0; font-size: 0.9rem; color: var(--text-secondary); }

.form-row {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}

.form-label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.8rem;
  color: var(--text-secondary);
}

.input {
  padding: 0.4rem 0.6rem;
  background: var(--bg-primary);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 0.85rem;
  flex: 1;
  min-width: 100px;
}

.priority-badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius);
  font-size: 0.8rem;
  font-weight: 500;
}
.priority-badge.low { background: rgba(156,163,175,0.15); color: #9ca3af; }
.priority-badge.normal { background: rgba(59,130,246,0.15); color: #3b82f6; }
.priority-badge.high { background: rgba(245,158,11,0.15); color: #f59e0b; }
.priority-badge.critical { background: rgba(239,68,68,0.15); color: #ef4444; }

.toggle { display: flex; align-items: center; gap: 0.4rem; cursor: pointer; }
.toggle input { cursor: pointer; }
.toggle-label { font-size: 0.85rem; }

.btn-danger { color: #ef4444; border-color: rgba(239,68,68,0.3); }
.btn-danger:hover { background: rgba(239,68,68,0.1); }
.btn-sm { font-size: 0.8rem; padding: 0.25rem 0.5rem; }

.briefing-card {
  background: rgba(59,130,246,0.05);
  border: 1px solid rgba(59,130,246,0.2);
  border-radius: var(--radius);
  padding: 1.25rem;
  margin-bottom: 1rem;
  line-height: 1.6;
}
.briefing-sections { display: flex; flex-direction: column; gap: 0.5rem; }
.briefing-section {
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.75rem;
}
.briefing-section pre {
  margin: 0.25rem 0 0 0;
  font-size: 0.8rem;
  white-space: pre-wrap;
  color: var(--text-secondary);
}
</style>
