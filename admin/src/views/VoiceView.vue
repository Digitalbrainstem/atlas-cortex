<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api.js'

const loading = ref(true)
const error = ref('')
const success = ref('')
const speakers = ref([])
const editingId = ref(null)
const editThreshold = ref(0)

onMounted(async () => {
  await fetchSpeakers()
})

async function fetchSpeakers() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/admin/voice/speakers')
    speakers.value = data.speakers || data.items || data
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function startEdit(speaker) {
  editingId.value = speaker.id
  editThreshold.value = speaker.confidence_threshold
}

function cancelEdit() {
  editingId.value = null
}

async function saveThreshold(speaker) {
  error.value = ''
  success.value = ''
  try {
    await api.patch(`/admin/voice/speakers/${speaker.id}`, {
      confidence_threshold: parseFloat(editThreshold.value),
    })
    speaker.confidence_threshold = parseFloat(editThreshold.value)
    editingId.value = null
    success.value = 'Threshold updated'
  } catch (e) {
    error.value = e.message
  }
}

async function deleteSpeaker(speaker) {
  if (!confirm(`Delete speaker "${speaker.display_name || speaker.id}"?`)) return
  error.value = ''
  success.value = ''
  try {
    await api.delete(`/admin/voice/speakers/${speaker.id}`)
    speakers.value = speakers.value.filter(s => s.id !== speaker.id)
    success.value = 'Speaker deleted'
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Voice Enrollments</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <div class="section">
      <div v-if="loading" class="loading-text">Loading speakers…</div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Name</th>
            <th>User ID</th>
            <th>Samples</th>
            <th>Enrolled</th>
            <th>Threshold</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!speakers.length">
            <td colspan="7" class="loading-text">No enrolled speakers</td>
          </tr>
          <tr v-for="speaker in speakers" :key="speaker.id">
            <td>{{ speaker.id }}</td>
            <td>{{ speaker.display_name || '—' }}</td>
            <td>{{ speaker.user_id || '—' }}</td>
            <td>{{ speaker.sample_count ?? '—' }}</td>
            <td>{{ speaker.enrolled_at || '—' }}</td>
            <td>
              <template v-if="editingId === speaker.id">
                <div class="inline-edit">
                  <input
                    v-model="editThreshold"
                    class="form-input form-input-sm"
                    type="number"
                    step="0.01"
                    min="0"
                    max="1"
                  />
                  <button class="btn btn-sm btn-primary" @click="saveThreshold(speaker)">✓</button>
                  <button class="btn btn-sm" @click="cancelEdit">✕</button>
                </div>
              </template>
              <template v-else>
                <span class="threshold-value" @click="startEdit(speaker)">
                  {{ speaker.confidence_threshold ?? '—' }}
                </span>
              </template>
            </td>
            <td>
              <button class="btn btn-sm btn-danger" @click="deleteSpeaker(speaker)">Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
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
.section {
  background: #1a1a2e;
  border-radius: 8px;
  padding: 1.5rem;
}
.loading-text {
  color: #888;
  text-align: center;
  padding: 2rem;
}
.table {
  width: 100%;
  border-collapse: collapse;
}
.table th, .table td {
  padding: 0.6rem 0.8rem;
  text-align: left;
  border-bottom: 1px solid #2a2a4a;
  font-size: 0.9rem;
}
.table th {
  color: #aaa;
  font-weight: 600;
  font-size: 0.8rem;
  text-transform: uppercase;
}
.threshold-value {
  cursor: pointer;
  padding: 0.2rem 0.4rem;
  border-radius: 4px;
  transition: background 0.15s;
}
.threshold-value:hover {
  background: #2a2a4a;
}
.inline-edit {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.form-input {
  background: #16162a;
  border: 1px solid #2a2a4a;
  border-radius: 6px;
  padding: 0.5rem 0.7rem;
  color: #eee;
  font-size: 0.9rem;
  outline: none;
}
.form-input:focus {
  border-color: #646cff;
}
.form-input-sm {
  width: 70px;
  padding: 0.3rem 0.5rem;
  font-size: 0.85rem;
}
.btn {
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1rem;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 600;
  background: #2a2a4a;
  color: #ccc;
}
.btn-sm {
  padding: 0.3rem 0.6rem;
  font-size: 0.8rem;
}
.btn-primary {
  background: #646cff;
  color: #fff;
}
.btn-danger {
  background: #dc3545;
  color: #fff;
}
.btn-danger:hover {
  background: #c82333;
}
</style>
