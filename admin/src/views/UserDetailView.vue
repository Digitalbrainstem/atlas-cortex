<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api.js'

const route = useRoute()
const userId = route.params.id

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const success = ref('')
const user = ref(null)

const form = ref({
  display_name: '',
  vocabulary_level: '',
  preferred_tone: '',
  communication_style: '',
})

const ageForm = ref({
  birth_year: '',
  birth_month: '',
})

const emotional = ref(null)
const topics = ref([])
const activityHours = ref([])

onMounted(async () => {
  try {
    const data = await api.get(`/admin/users/${userId}`)
    user.value = data
    form.value = {
      display_name: data.display_name || '',
      vocabulary_level: data.vocabulary_level || '',
      preferred_tone: data.preferred_tone || '',
      communication_style: data.communication_style || '',
    }
    ageForm.value = {
      birth_year: data.birth_year || '',
      birth_month: data.birth_month || '',
    }
    emotional.value = data.emotional_profile || null
    topics.value = data.topics || []
    activityHours.value = data.activity_hours || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

async function saveProfile() {
  saving.value = true
  error.value = ''
  success.value = ''
  try {
    await api.patch(`/admin/users/${userId}`, form.value)
    success.value = 'Profile updated successfully'
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function setAge() {
  error.value = ''
  success.value = ''
  try {
    await api.post(`/admin/users/${userId}/age`, {
      birth_year: parseInt(ageForm.value.birth_year),
      birth_month: parseInt(ageForm.value.birth_month) || undefined,
    })
    success.value = 'Age updated successfully'
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <AppLayout>
    <div class="detail-header">
      <router-link to="/users" class="back-link">← Back to Users</router-link>
      <h2 class="page-title">User Detail</h2>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>
    <div v-if="loading" class="loading-text">Loading user…</div>

    <template v-if="user && !loading">
      <div class="section">
        <h3>Profile</h3>
        <div class="form-grid">
          <div class="form-group">
            <label class="form-label">User ID</label>
            <div class="form-static">{{ user.id }}</div>
          </div>
          <div class="form-group">
            <label class="form-label">Age Group</label>
            <div class="form-static">{{ user.age_group || '—' }}</div>
          </div>
          <div class="form-group">
            <label class="form-label">Display Name</label>
            <input v-model="form.display_name" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">Vocabulary Level</label>
            <select v-model="form.vocabulary_level" class="form-input">
              <option value="">Select…</option>
              <option value="basic">Basic</option>
              <option value="intermediate">Intermediate</option>
              <option value="advanced">Advanced</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Preferred Tone</label>
            <select v-model="form.preferred_tone" class="form-input">
              <option value="">Select…</option>
              <option value="friendly">Friendly</option>
              <option value="professional">Professional</option>
              <option value="playful">Playful</option>
              <option value="calm">Calm</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Communication Style</label>
            <select v-model="form.communication_style" class="form-input">
              <option value="">Select…</option>
              <option value="concise">Concise</option>
              <option value="detailed">Detailed</option>
              <option value="conversational">Conversational</option>
            </select>
          </div>
        </div>
        <button class="btn btn-primary" :disabled="saving" @click="saveProfile">
          {{ saving ? 'Saving…' : 'Save Profile' }}
        </button>
      </div>

      <div class="section">
        <h3>Age Management</h3>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Birth Year</label>
            <input v-model="ageForm.birth_year" class="form-input" type="number" placeholder="2010" />
          </div>
          <div class="form-group">
            <label class="form-label">Birth Month (optional)</label>
            <input v-model="ageForm.birth_month" class="form-input" type="number" min="1" max="12" placeholder="1-12" />
          </div>
          <button class="btn btn-primary" @click="setAge">Set Age</button>
        </div>
      </div>

      <div v-if="emotional" class="section">
        <h3>Emotional Profile</h3>
        <div class="info-grid">
          <div class="info-item">
            <span class="info-label">Rapport Score</span>
            <div class="rapport-bar-track">
              <div class="rapport-bar-fill" :style="{ width: (emotional.rapport_score * 100) + '%' }"></div>
            </div>
            <span class="info-value">{{ (emotional.rapport_score * 100).toFixed(0) }}%</span>
          </div>
          <div class="info-item">
            <span class="info-label">Interaction Count</span>
            <span class="info-value">{{ emotional.interaction_count }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">Positive</span>
            <span class="info-value positive">{{ emotional.positive_count ?? 0 }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">Neutral</span>
            <span class="info-value">{{ emotional.neutral_count ?? 0 }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">Negative</span>
            <span class="info-value negative">{{ emotional.negative_count ?? 0 }}</span>
          </div>
        </div>
      </div>

      <div class="two-col">
        <div v-if="topics.length" class="section">
          <h3>Topics</h3>
          <div class="tag-list">
            <span v-for="t in topics" :key="t" class="tag">{{ t }}</span>
          </div>
        </div>
        <div v-if="activityHours.length" class="section">
          <h3>Activity Hours</h3>
          <div class="hours-grid">
            <div v-for="h in activityHours" :key="h.hour" class="hour-cell" :title="'Hour ' + h.hour + ': ' + h.count + ' interactions'">
              <div class="hour-bar" :style="{ height: Math.max(4, h.count * 3) + 'px' }"></div>
              <span class="hour-label">{{ h.hour }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </AppLayout>
</template>

<style scoped>
.detail-header {
  margin-bottom: 1.5rem;
}
.back-link {
  color: #646cff;
  font-size: 0.85rem;
  text-decoration: none;
}
.back-link:hover {
  text-decoration: underline;
}
.page-title {
  margin: 0.5rem 0 0;
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
.loading-text {
  color: #888;
  text-align: center;
  padding: 3rem;
}
.section {
  background: #1a1a2e;
  border-radius: 8px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}
.section h3 {
  margin: 0 0 1rem;
  color: #ccc;
  font-size: 1.1rem;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  margin-bottom: 1.2rem;
}
.form-row {
  display: flex;
  gap: 1rem;
  align-items: flex-end;
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
.form-static {
  color: #ccc;
  font-size: 0.95rem;
  padding: 0.6rem 0;
}
.btn {
  border: none;
  border-radius: 6px;
  padding: 0.6rem 1.2rem;
  cursor: pointer;
  font-size: 0.9rem;
  font-weight: 600;
}
.btn-primary {
  background: #646cff;
  color: #fff;
}
.btn-primary:hover:not(:disabled) {
  background: #535bf2;
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.info-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 1.5rem;
}
.info-item {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.info-label {
  font-size: 0.8rem;
  color: #888;
}
.info-value {
  font-size: 1.1rem;
  font-weight: 600;
  color: #eee;
}
.info-value.positive { color: #42b883; }
.info-value.negative { color: #ff6b6b; }
.rapport-bar-track {
  width: 120px;
  height: 8px;
  background: #16162a;
  border-radius: 4px;
  overflow: hidden;
}
.rapport-bar-fill {
  height: 100%;
  background: #42b883;
  border-radius: 4px;
}
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}
.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.tag {
  background: #2a2a4a;
  padding: 0.3rem 0.7rem;
  border-radius: 12px;
  font-size: 0.8rem;
  color: #ccc;
}
.hours-grid {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 80px;
}
.hour-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.hour-bar {
  width: 12px;
  background: #646cff;
  border-radius: 2px 2px 0 0;
}
.hour-label {
  font-size: 0.6rem;
  color: #888;
}
</style>
