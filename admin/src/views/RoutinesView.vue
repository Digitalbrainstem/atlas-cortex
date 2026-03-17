<script setup>
import { ref, computed, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api.js'

const loading = ref(true)
const error = ref('')
const success = ref('')

const routines = ref([])
const templates = ref([])
const expandedId = ref(null)
const showTemplates = ref(false)

// New routine form
const newRoutine = ref({ name: '', description: '' })
const creating = ref(false)

onMounted(() => fetchAll())

async function fetchAll() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([fetchRoutines(), fetchTemplates()])
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function fetchRoutines() {
  const data = await api.get('/admin/routines')
  routines.value = data.routines || []
}

async function fetchTemplates() {
  const data = await api.get('/admin/routines/templates')
  templates.value = data.templates || []
}

function toggleExpand(id) {
  expandedId.value = expandedId.value === id ? null : id
}

function parseTriggers(routine) {
  if (!routine.triggers || !routine.triggers.length) return 'No triggers'
  return routine.triggers.map(t => {
    const type = t.trigger_type
    let config = {}
    try { config = typeof t.trigger_config === 'string' ? JSON.parse(t.trigger_config) : t.trigger_config } catch {}
    if (type === 'voice_phrase') return `🎤 "${config.phrase || '?'}"`
    if (type === 'schedule') return `⏰ ${config.cron || '?'}`
    if (type === 'ha_event') return `🏠 ${config.entity_id || '?'}`
    return type
  }).join(', ')
}

function parseStepType(step) {
  const types = {
    tts_announce: '🔊 Announce',
    ha_service: '🏠 HA Service',
    delay: '⏱️ Delay',
    condition: '🔀 Condition',
    set_variable: '📝 Set Variable',
  }
  return types[step.action_type] || step.action_type
}

function parseStepDetail(step) {
  let config = {}
  try { config = typeof step.action_config === 'string' ? JSON.parse(step.action_config) : step.action_config } catch {}
  if (step.action_type === 'tts_announce') return config.message || ''
  if (step.action_type === 'ha_service') return `${config.domain}.${config.service} → ${config.entity_id || ''}`
  if (step.action_type === 'delay') return `${config.seconds || 0}s`
  if (step.action_type === 'condition') return `${config.type || ''}`
  if (step.action_type === 'set_variable') return `${config.name} = ${config.value}`
  return JSON.stringify(config)
}

async function createRoutine() {
  if (!newRoutine.value.name) {
    error.value = 'Routine name is required'
    return
  }
  creating.value = true
  error.value = ''
  success.value = ''
  try {
    await api.post('/admin/routines', newRoutine.value)
    newRoutine.value = { name: '', description: '' }
    await fetchRoutines()
    success.value = 'Routine created'
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}

async function deleteRoutine(id) {
  if (!confirm('Delete this routine?')) return
  error.value = ''
  try {
    await api.delete(`/admin/routines/${id}`)
    await fetchRoutines()
    success.value = 'Routine deleted'
  } catch (e) {
    error.value = e.message
  }
}

async function toggleRoutine(routine) {
  error.value = ''
  const action = routine.enabled ? 'disable' : 'enable'
  try {
    await api.post(`/admin/routines/${routine.id}/${action}`)
    routine.enabled = routine.enabled ? 0 : 1
    success.value = `Routine ${action}d`
  } catch (e) {
    error.value = e.message
  }
}

async function runRoutine(routine) {
  error.value = ''
  success.value = ''
  try {
    const result = await api.post(`/admin/routines/${routine.id}/run`)
    await fetchRoutines()
    success.value = `Routine "${routine.name}" executed (run #${result.run_id})`
  } catch (e) {
    error.value = e.message
  }
}

async function instantiateTemplate(templateId) {
  error.value = ''
  success.value = ''
  try {
    const result = await api.post(`/admin/routines/templates/${templateId}/instantiate`, {})
    await fetchRoutines()
    showTemplates.value = false
    success.value = `Created routine "${result.name}" from template`
  } catch (e) {
    error.value = e.message
  }
}

function formatDate(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString()
  } catch { return iso }
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">🔄 Routines & Automations</h2>

    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <div v-if="loading" class="loading-text">Loading…</div>

    <template v-else>
      <!-- Actions bar -->
      <div class="actions-bar">
        <button class="btn btn-primary" @click="showTemplates = !showTemplates">
          {{ showTemplates ? '✕ Close Templates' : '📋 Add from Template' }}
        </button>
        <button class="btn" @click="fetchRoutines">🔄 Refresh</button>
      </div>

      <!-- Templates panel -->
      <div v-if="showTemplates" class="templates-panel">
        <h3>Available Templates</h3>
        <div class="template-grid">
          <div
            v-for="t in templates"
            :key="t.id"
            class="template-card"
            @click="instantiateTemplate(t.id)"
          >
            <div class="template-name">{{ t.name }}</div>
            <div class="template-desc">{{ t.description }}</div>
            <div class="template-meta">{{ t.step_count }} step(s)</div>
          </div>
        </div>
      </div>

      <!-- Create form -->
      <div class="add-form">
        <h4>Create Routine</h4>
        <div class="form-row">
          <input v-model="newRoutine.name" placeholder="Routine name" class="input" />
          <input v-model="newRoutine.description" placeholder="Description (optional)" class="input" />
          <button class="btn btn-primary" :disabled="creating" @click="createRoutine">
            {{ creating ? 'Creating…' : '+ Create' }}
          </button>
        </div>
      </div>

      <!-- Routines list -->
      <table class="table">
        <thead>
          <tr>
            <th></th>
            <th>Name</th>
            <th>Triggers</th>
            <th>Steps</th>
            <th>Last Run</th>
            <th>Runs</th>
            <th>Enabled</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!routines.length">
            <td colspan="8" class="loading-text">No routines yet</td>
          </tr>
          <template v-for="r in routines" :key="r.id">
            <tr class="routine-row" :class="{ expanded: expandedId === r.id }">
              <td>
                <button class="expand-btn" @click="toggleExpand(r.id)">
                  {{ expandedId === r.id ? '▼' : '▶' }}
                </button>
              </td>
              <td class="name-cell">
                <strong>{{ r.name }}</strong>
                <div v-if="r.description" class="desc-text">{{ r.description }}</div>
              </td>
              <td>{{ parseTriggers(r) }}</td>
              <td>{{ (r.steps || []).length }}</td>
              <td>{{ formatDate(r.last_run) }}</td>
              <td>{{ r.run_count || 0 }}</td>
              <td>
                <label class="toggle" @click.stop>
                  <input type="checkbox" :checked="r.enabled" @change="toggleRoutine(r)" />
                  <span class="toggle-label">{{ r.enabled ? 'On' : 'Off' }}</span>
                </label>
              </td>
              <td class="action-cell">
                <button class="btn btn-sm btn-run" @click="runRoutine(r)" title="Run now">▶ Run</button>
                <button class="btn btn-sm btn-danger" @click="deleteRoutine(r.id)" title="Delete">🗑️</button>
              </td>
            </tr>
            <!-- Expanded detail -->
            <tr v-if="expandedId === r.id" class="detail-row">
              <td colspan="8">
                <div class="detail-panel">
                  <!-- Steps -->
                  <div class="detail-section">
                    <h4>Steps</h4>
                    <div v-if="!r.steps || !r.steps.length" class="empty-text">No steps</div>
                    <div v-else class="steps-list">
                      <div v-for="(s, idx) in r.steps" :key="s.id" class="step-item">
                        <span class="step-order">{{ idx + 1 }}.</span>
                        <span class="step-type">{{ parseStepType(s) }}</span>
                        <span class="step-detail">{{ parseStepDetail(s) }}</span>
                        <span v-if="s.on_error !== 'continue'" class="step-error">
                          on-error: {{ s.on_error }}
                        </span>
                      </div>
                    </div>
                  </div>

                  <!-- Triggers -->
                  <div class="detail-section">
                    <h4>Triggers</h4>
                    <div v-if="!r.triggers || !r.triggers.length" class="empty-text">No triggers</div>
                    <div v-else class="triggers-list">
                      <div v-for="t in r.triggers" :key="t.id" class="trigger-item">
                        <span class="trigger-type">{{ t.trigger_type }}</span>
                        <code>{{ typeof t.trigger_config === 'string' ? t.trigger_config : JSON.stringify(t.trigger_config) }}</code>
                        <span class="trigger-enabled" :class="{ disabled: !t.enabled }">
                          {{ t.enabled ? '✅' : '⏸️' }}
                        </span>
                      </div>
                    </div>
                  </div>

                  <!-- Run history -->
                  <div class="detail-section">
                    <h4>Recent Runs</h4>
                    <div v-if="!r.recent_runs || !r.recent_runs.length" class="empty-text">No runs yet</div>
                    <div v-else class="runs-list">
                      <div v-for="run in r.recent_runs" :key="run.id" class="run-item">
                        <span class="run-status" :class="run.status">{{ run.status }}</span>
                        <span class="run-time">{{ formatDate(run.started_at) }}</span>
                        <span class="run-steps">{{ run.steps_completed }} step(s)</span>
                        <span v-if="run.error_message" class="run-error">{{ run.error_message }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>
  </AppLayout>
</template>

<style scoped>
.actions-bar {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.templates-panel {
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem;
  margin-bottom: 1.5rem;
}

.templates-panel h3 { margin: 0 0 0.75rem 0; }

.template-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.75rem;
}

.template-card {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.75rem;
  cursor: pointer;
  transition: border-color 0.15s, background-color 0.15s;
}

.template-card:hover {
  border-color: var(--accent);
  background: rgba(59, 130, 246, 0.05);
}

.template-name { font-weight: 600; margin-bottom: 0.25rem; }
.template-desc { font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 0.25rem; }
.template-meta { font-size: 0.75rem; color: var(--text-muted); }

.add-form {
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem;
  margin-bottom: 1.5rem;
}

.add-form h4 {
  margin: 0 0 0.75rem 0;
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.form-row {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}

.input {
  padding: 0.4rem 0.6rem;
  background: var(--bg-primary);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 0.85rem;
  flex: 1;
  min-width: 120px;
}

.routine-row { cursor: pointer; }
.routine-row:hover { background: rgba(255,255,255,0.02); }
.routine-row.expanded { background: rgba(59, 130, 246, 0.05); }

.expand-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 0.8rem;
  padding: 0.2rem;
}

.name-cell strong { display: block; }
.desc-text { font-size: 0.8rem; color: var(--text-muted); }

.action-cell {
  display: flex;
  gap: 0.25rem;
  white-space: nowrap;
}

.toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  cursor: pointer;
}
.toggle input { cursor: pointer; }
.toggle-label { font-size: 0.85rem; }

.btn-run {
  color: #22c55e;
  border-color: rgba(34, 197, 94, 0.3);
}
.btn-run:hover { background: rgba(34, 197, 94, 0.1); }

.btn-danger {
  color: #ef4444;
  border-color: rgba(239, 68, 68, 0.3);
}
.btn-danger:hover { background: rgba(239, 68, 68, 0.1); }

.btn-sm {
  font-size: 0.8rem;
  padding: 0.25rem 0.5rem;
}

.detail-row td {
  padding: 0 !important;
  border-top: none !important;
}

.detail-panel {
  padding: 1rem 1.5rem;
  background: rgba(255,255,255,0.01);
  border-top: 1px solid var(--border);
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 1.5rem;
}

@media (max-width: 768px) {
  .detail-panel { grid-template-columns: 1fr; }
}

.detail-section h4 {
  margin: 0 0 0.5rem 0;
  font-size: 0.85rem;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.empty-text { font-size: 0.8rem; color: var(--text-muted); }

.steps-list, .triggers-list, .runs-list {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.step-item {
  display: flex;
  gap: 0.5rem;
  align-items: baseline;
  font-size: 0.85rem;
}
.step-order { color: var(--text-muted); font-weight: 600; min-width: 1.5em; }
.step-type { color: var(--accent); font-weight: 500; white-space: nowrap; }
.step-detail { color: var(--text-secondary); }
.step-error { color: #f59e0b; font-size: 0.75rem; }

.trigger-item {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  font-size: 0.85rem;
}
.trigger-type { font-weight: 500; }
.trigger-item code {
  font-size: 0.75rem;
  background: rgba(255,255,255,0.05);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
}

.run-item {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  font-size: 0.8rem;
}

.run-status {
  display: inline-block;
  padding: 0.1rem 0.4rem;
  border-radius: var(--radius);
  font-size: 0.75rem;
  font-weight: 500;
}
.run-status.completed { background: rgba(34,197,94,0.15); color: #22c55e; }
.run-status.failed { background: rgba(239,68,68,0.15); color: #ef4444; }
.run-status.running { background: rgba(59,130,246,0.15); color: #3b82f6; }
.run-status.cancelled { background: rgba(245,158,11,0.15); color: #f59e0b; }

.run-time { color: var(--text-muted); }
.run-steps { color: var(--text-secondary); }
.run-error { color: #ef4444; font-size: 0.75rem; }
</style>
