<script setup>
import { ref, onMounted, watch } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const loading = ref(true)
const error = ref('')
const success = ref('')
const users = ref([])
const selectedUserId = ref(null)
const controls = ref(null)
const loadingControls = ref(false)

const form = ref({
  parent_user_id: '',
  content_filter_level: 'moderate',
  allowed_hours_start: 8,
  allowed_hours_end: 20,
})

const restrictedActions = ref([])
const newAction = ref('')

const userColumns = [
  { key: 'id', label: 'ID' },
  { key: 'display_name', label: 'Name' },
  { key: 'age_group', label: 'Age Group' },
  { key: 'has_parental_controls', label: 'Controls' },
]

onMounted(async () => {
  try {
    const data = await api.get('/admin/users?parental=true')
    users.value = data.users || data.items || data
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

watch(selectedUserId, async (id) => {
  if (!id) return
  loadingControls.value = true
  error.value = ''
  try {
    const data = await api.get(`/admin/users/${id}/parental`)
    controls.value = data
    form.value = {
      parent_user_id: data.parent_user_id || '',
      content_filter_level: data.content_filter_level || 'moderate',
      allowed_hours_start: data.allowed_hours_start ?? 8,
      allowed_hours_end: data.allowed_hours_end ?? 20,
    }
    restrictedActions.value = data.restricted_actions || []
  } catch (e) {
    controls.value = null
    form.value = {
      parent_user_id: '',
      content_filter_level: 'moderate',
      allowed_hours_start: 8,
      allowed_hours_end: 20,
    }
    restrictedActions.value = []
  } finally {
    loadingControls.value = false
  }
})

function selectUser(row) {
  selectedUserId.value = row.id
}

function addAction() {
  const action = newAction.value.trim()
  if (action && !restrictedActions.value.includes(action)) {
    restrictedActions.value.push(action)
    newAction.value = ''
  }
}

function removeAction(idx) {
  restrictedActions.value.splice(idx, 1)
}

async function saveControls() {
  error.value = ''
  success.value = ''
  try {
    await api.post(`/admin/users/${selectedUserId.value}/parental`, {
      ...form.value,
      allowed_hours_start: parseInt(form.value.allowed_hours_start),
      allowed_hours_end: parseInt(form.value.allowed_hours_end),
      restricted_actions: restrictedActions.value,
    })
    success.value = 'Parental controls saved'
  } catch (e) {
    error.value = e.message
  }
}

async function deleteControls() {
  if (!confirm('Delete parental controls for this user?')) return
  error.value = ''
  success.value = ''
  try {
    await api.delete(`/admin/users/${selectedUserId.value}/parental`)
    controls.value = null
    success.value = 'Parental controls removed'
    form.value = {
      parent_user_id: '',
      content_filter_level: 'moderate',
      allowed_hours_start: 8,
      allowed_hours_end: 20,
    }
    restrictedActions.value = []
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Parental Controls</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <div class="parental-layout">
      <div class="section user-list-panel">
        <h3>Users</h3>
        <DataTable
          :columns="userColumns"
          :rows="users"
          :loading="loading"
          @row-click="selectUser"
        />
      </div>

      <div class="section controls-panel">
        <template v-if="!selectedUserId">
          <p class="placeholder-text">Select a user to view/edit parental controls</p>
        </template>
        <template v-else-if="loadingControls">
          <p class="loading-text">Loading controls…</p>
        </template>
        <template v-else>
          <h3>Controls for User {{ selectedUserId }}</h3>
          <div class="form-grid">
            <div class="form-group">
              <label class="form-label">Parent User ID</label>
              <input v-model="form.parent_user_id" class="form-input" placeholder="Parent user ID" />
            </div>
            <div class="form-group">
              <label class="form-label">Content Filter Level</label>
              <select v-model="form.content_filter_level" class="form-input">
                <option value="strict">Strict</option>
                <option value="moderate">Moderate</option>
                <option value="loose">Loose</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label">Allowed Hours Start</label>
              <input v-model="form.allowed_hours_start" class="form-input" type="number" min="0" max="23" />
            </div>
            <div class="form-group">
              <label class="form-label">Allowed Hours End</label>
              <input v-model="form.allowed_hours_end" class="form-input" type="number" min="0" max="23" />
            </div>
          </div>

          <div class="actions-section">
            <h4>Restricted Actions</h4>
            <div class="action-list">
              <div v-for="(action, idx) in restrictedActions" :key="idx" class="action-item">
                <span>{{ action }}</span>
                <button class="btn-icon btn-danger-icon" @click="removeAction(idx)">✕</button>
              </div>
              <div v-if="!restrictedActions.length" class="placeholder-text">No restricted actions</div>
            </div>
            <div class="action-add">
              <input v-model="newAction" class="form-input" placeholder="Add action…" @keyup.enter="addAction" />
              <button class="btn btn-sm" @click="addAction">Add</button>
            </div>
          </div>

          <div class="controls-actions">
            <button class="btn btn-primary" @click="saveControls">Save Controls</button>
            <button v-if="controls" class="btn btn-danger" @click="deleteControls">Delete Controls</button>
          </div>
        </template>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.page-title {
  margin: 0 0 1.5rem;
  font-size: 1.5rem;
  color: #eee;
}
.error-banner {
  background: rgba(220, 50, 50, 0.15);
  border: 1px solid rgba(220, 50, 50, 0.4);
  color: #ff6b6b;
  padding: 0.8rem 1rem;
  border-radius: 8px;
  margin-bottom: 1rem;
}
.success-banner {
  background: rgba(66, 184, 131, 0.15);
  border: 1px solid rgba(66, 184, 131, 0.4);
  color: #42b883;
  padding: 0.8rem 1rem;
  border-radius: 8px;
  margin-bottom: 1rem;
}
.parental-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}
.section {
  background: #1a1a2e;
  border-radius: 8px;
  padding: 1.5rem;
}
.section h3 {
  margin: 0 0 1rem;
  color: #ccc;
  font-size: 1.1rem;
}
.placeholder-text, .loading-text {
  color: #888;
  text-align: center;
  padding: 2rem;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  margin-bottom: 1.2rem;
}
.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.form-label {
  font-size: 0.8rem;
  color: #aaa;
}
.form-input {
  background: #16162a;
  border: 1px solid #2a2a4a;
  border-radius: 6px;
  padding: 0.6rem 0.8rem;
  color: #eee;
  font-size: 0.9rem;
  outline: none;
}
.form-input:focus {
  border-color: #646cff;
}
.actions-section {
  margin-bottom: 1.2rem;
}
.actions-section h4 {
  margin: 0 0 0.5rem;
  color: #aaa;
  font-size: 0.9rem;
}
.action-list {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  margin-bottom: 0.6rem;
}
.action-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #16162a;
  padding: 0.4rem 0.8rem;
  border-radius: 6px;
  font-size: 0.9rem;
  color: #ccc;
}
.btn-icon {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.9rem;
  padding: 0.2rem 0.4rem;
}
.btn-danger-icon {
  color: #ff6b6b;
}
.action-add {
  display: flex;
  gap: 0.5rem;
}
.action-add .form-input {
  flex: 1;
}
.controls-actions {
  display: flex;
  gap: 0.8rem;
}
.btn {
  border: none;
  border-radius: 6px;
  padding: 0.6rem 1.2rem;
  cursor: pointer;
  font-size: 0.9rem;
  font-weight: 600;
}
.btn-sm {
  padding: 0.4rem 0.8rem;
  font-size: 0.85rem;
  background: #2a2a4a;
  color: #ccc;
}
.btn-primary {
  background: #646cff;
  color: #fff;
}
.btn-primary:hover {
  background: #535bf2;
}
.btn-danger {
  background: #dc3545;
  color: #fff;
}
.btn-danger:hover {
  background: #c82333;
}
</style>
