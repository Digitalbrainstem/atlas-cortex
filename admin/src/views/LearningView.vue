<script setup>
import { ref, computed, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api.js'

const loading = ref(true)
const error = ref('')
const users = ref([])
const selectedUser = ref('')
const userProgress = ref(null)
const sessions = ref([])
const leaderboard = ref({ top_streaks: [], top_scores: [] })
const reportVisible = ref(false)
const report = ref(null)
const reportLoading = ref(false)

onMounted(async () => {
  try {
    const [progressData, sessionData, lbData] = await Promise.all([
      api.get('/admin/learning/progress'),
      api.get('/admin/learning/sessions'),
      api.get('/admin/learning/leaderboard'),
    ])

    // Extract unique users
    const userSet = new Set()
    for (const p of (progressData.progress || [])) userSet.add(p.user_id)
    for (const s of (sessionData.sessions || [])) userSet.add(s.user_id)
    users.value = [...userSet].sort()

    sessions.value = sessionData.sessions || []
    leaderboard.value = lbData || { top_streaks: [], top_scores: [] }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

async function selectUser(uid) {
  selectedUser.value = uid
  reportVisible.value = false
  report.value = null
  try {
    userProgress.value = await api.get(`/admin/learning/progress/${uid}`)
  } catch (e) {
    error.value = e.message
  }
}

async function showReport() {
  if (!selectedUser.value) return
  reportLoading.value = true
  try {
    report.value = await api.get(`/admin/learning/report/${selectedUser.value}`)
    reportVisible.value = true
  } catch (e) {
    error.value = e.message
  } finally {
    reportLoading.value = false
  }
}

const userSessions = computed(() => {
  if (!selectedUser.value) return sessions.value.slice(0, 20)
  return sessions.value.filter(s => s.user_id === selectedUser.value).slice(0, 20)
})

const dueReviews = computed(() => {
  if (!userProgress.value) return []
  const now = new Date().toISOString()
  return (userProgress.value.progress || []).filter(p => p.next_review && p.next_review <= now)
})

function profPct(val) {
  return Math.round((val || 0) * 100)
}

function profColor(val) {
  const pct = (val || 0) * 100
  if (pct >= 80) return '#22c55e'
  if (pct >= 50) return '#f0a500'
  return '#ef4444'
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">📚 Learning Progress</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading" class="loading-text">Loading learning data…</div>
    <template v-else>
      <!-- User Selector -->
      <div class="controls">
        <select v-model="selectedUser" @change="selectUser(selectedUser)" class="user-select">
          <option value="">All Users</option>
          <option v-for="u in users" :key="u" :value="u">{{ u }}</option>
        </select>
        <button v-if="selectedUser" class="btn-report" @click="showReport" :disabled="reportLoading">
          📋 {{ reportLoading ? 'Loading...' : 'View Report' }}
        </button>
      </div>

      <!-- Subject Breakdown (when user selected) -->
      <div v-if="userProgress && userProgress.subjects" class="section">
        <h3>Subject Proficiency</h3>
        <div class="subject-grid">
          <div v-for="(data, subj) in userProgress.subjects" :key="subj" class="subject-card">
            <div class="subject-header">
              <span class="subject-name">{{ subj }}</span>
              <span class="subject-pct" :style="{ color: profColor(data.avg_proficiency) }">
                {{ profPct(data.avg_proficiency) }}%
              </span>
            </div>
            <div class="prof-bar-track">
              <div
                class="prof-bar-fill"
                :style="{ width: profPct(data.avg_proficiency) + '%', background: profColor(data.avg_proficiency) }"
              ></div>
            </div>
            <div class="subject-meta">
              {{ data.correct_attempts }}/{{ data.total_attempts }} correct
              · {{ data.topics }} topics
              <span v-if="data.best_streak > 0"> · 🔥 {{ data.best_streak }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Topic Details (when user selected) -->
      <div v-if="userProgress && userProgress.progress && userProgress.progress.length" class="section">
        <h3>Topic Details</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>Subject</th>
              <th>Topic</th>
              <th>Proficiency</th>
              <th>Attempts</th>
              <th>Streak</th>
              <th>Best</th>
              <th>Last Practiced</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in userProgress.progress" :key="p.subject + p.topic">
              <td>{{ p.subject }}</td>
              <td>{{ p.topic }}</td>
              <td>
                <div class="mini-bar-track">
                  <div class="mini-bar-fill" :style="{ width: profPct(p.proficiency) + '%', background: profColor(p.proficiency) }"></div>
                </div>
                <span class="mini-pct">{{ profPct(p.proficiency) }}%</span>
              </td>
              <td>{{ p.correct_attempts }}/{{ p.total_attempts }}</td>
              <td>{{ p.streak > 0 ? '🔥 ' + p.streak : '—' }}</td>
              <td>{{ p.best_streak }}</td>
              <td>{{ p.last_practiced ? new Date(p.last_practiced).toLocaleDateString() : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Due for Review -->
      <div v-if="dueReviews.length" class="section">
        <h3>📅 Topics Due for Review</h3>
        <div class="due-list">
          <div v-for="d in dueReviews" :key="d.subject + d.topic" class="due-item">
            <span class="due-subject">{{ d.subject }}</span>
            <span class="due-topic">{{ d.topic }}</span>
            <span class="due-prof" :style="{ color: profColor(d.proficiency) }">
              {{ profPct(d.proficiency) }}%
            </span>
          </div>
        </div>
      </div>

      <!-- Recent Sessions -->
      <div class="section">
        <h3>Recent Sessions</h3>
        <table class="data-table" v-if="userSessions.length">
          <thead>
            <tr>
              <th>User</th>
              <th>Subject</th>
              <th>Mode</th>
              <th>Difficulty</th>
              <th>Score</th>
              <th>Q&A</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in userSessions" :key="s.id">
              <td>{{ s.user_id }}</td>
              <td>{{ s.subject }}</td>
              <td>{{ s.mode }}</td>
              <td>{{ s.difficulty_level }}</td>
              <td>{{ s.score }}</td>
              <td>{{ s.correct_answers }}/{{ s.questions_asked }}</td>
              <td>{{ s.started_at ? new Date(s.started_at).toLocaleString() : '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty-text">No recent sessions.</p>
      </div>

      <!-- Leaderboard -->
      <div class="section" v-if="leaderboard.top_streaks.length || leaderboard.top_scores.length">
        <h3>🏆 Leaderboard</h3>
        <div class="lb-grid">
          <div v-if="leaderboard.top_streaks.length" class="lb-col">
            <h4>🔥 Top Streaks</h4>
            <div v-for="(s, i) in leaderboard.top_streaks.slice(0, 10)" :key="'s' + i" class="lb-row">
              <span class="lb-rank">{{ i + 1 }}.</span>
              <span class="lb-user">{{ s.user_id }}</span>
              <span class="lb-val">{{ s.best_streak }} 🔥</span>
            </div>
          </div>
          <div v-if="leaderboard.top_scores.length" class="lb-col">
            <h4>⭐ Top Scores</h4>
            <div v-for="(s, i) in leaderboard.top_scores.slice(0, 10)" :key="'sc' + i" class="lb-row">
              <span class="lb-rank">{{ i + 1 }}.</span>
              <span class="lb-user">{{ s.user_id }}</span>
              <span class="lb-val">{{ s.score }} pts</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Parent Report Modal -->
      <div v-if="reportVisible && report" class="report-overlay" @click.self="reportVisible = false">
        <div class="report-modal">
          <div class="report-header">
            <h3>📋 Parent Report — {{ selectedUser }}</h3>
            <button class="close-btn" @click="reportVisible = false">✕</button>
          </div>
          <div class="report-body">
            <div class="report-stats">
              <div class="rstat">
                <span class="rstat-val">{{ report.total_sessions }}</span>
                <span class="rstat-label">Sessions</span>
              </div>
              <div class="rstat">
                <span class="rstat-val">{{ report.total_questions }}</span>
                <span class="rstat-label">Questions</span>
              </div>
              <div class="rstat">
                <span class="rstat-val">{{ Math.round(report.accuracy * 100) }}%</span>
                <span class="rstat-label">Accuracy</span>
              </div>
              <div class="rstat">
                <span class="rstat-val">🔥 {{ report.current_streak }}</span>
                <span class="rstat-label">Streak</span>
              </div>
            </div>

            <div v-if="report.strongest_areas && report.strongest_areas.length" class="report-section">
              <h4>🌟 Strongest Areas</h4>
              <div v-for="a in report.strongest_areas" :key="a.topic" class="report-item good">
                {{ a.subject }} — {{ a.topic }}: {{ Math.round(a.proficiency * 100) }}%
              </div>
            </div>

            <div v-if="report.needs_help && report.needs_help.length" class="report-section">
              <h4>💪 Needs Practice</h4>
              <div v-for="a in report.needs_help" :key="a.topic" class="report-item warn">
                {{ a.subject }} — {{ a.topic }}: {{ Math.round(a.proficiency * 100) }}%
              </div>
            </div>

            <div v-if="report.subjects_practiced" class="report-section">
              <h4>📊 Subjects Practiced (Last {{ report.period_days }} days)</h4>
              <div v-for="(data, subj) in report.subjects_practiced" :key="subj" class="report-item">
                <strong>{{ subj }}</strong>: {{ data.sessions }} sessions,
                {{ data.correct }}/{{ data.questions }} correct
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </AppLayout>
</template>

<style scoped>
.page-title { margin: 0 0 1.5rem; font-size: 1.5rem; color: #eee; }
.error-banner {
  background: rgba(220, 50, 50, 0.15);
  border: 1px solid rgba(220, 50, 50, 0.4);
  color: #ff6b6b;
  padding: 0.8rem 1rem;
  border-radius: 8px;
  margin-bottom: 1rem;
}
.loading-text { color: #888; text-align: center; padding: 3rem; }
.empty-text { color: #666; font-style: italic; padding: 1rem 0; }

.controls {
  display: flex; gap: 1rem; margin-bottom: 1.5rem; align-items: center;
}
.user-select {
  padding: 0.5rem 0.8rem; border-radius: 6px;
  background: #1a1a2e; color: #eee;
  border: 1px solid #333; font-size: 0.9rem; min-width: 200px;
}
.btn-report {
  padding: 0.5rem 1rem; border-radius: 6px;
  background: #646cff; color: #fff; border: none;
  cursor: pointer; font-size: 0.9rem;
}
.btn-report:hover { background: #535bf2; }
.btn-report:disabled { opacity: 0.5; cursor: not-allowed; }

.section {
  background: #1a1a2e; border-radius: 8px;
  padding: 1.2rem; margin-bottom: 1.5rem;
}
.section h3 { margin: 0 0 1rem; font-size: 1rem; color: #ccc; }

.subject-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 1rem;
}
.subject-card {
  background: #16162a; border-radius: 8px; padding: 1rem;
}
.subject-header {
  display: flex; justify-content: space-between; margin-bottom: 0.5rem;
}
.subject-name { font-weight: 600; color: #eee; text-transform: capitalize; }
.subject-pct { font-weight: 700; font-size: 1.1rem; }
.prof-bar-track {
  height: 8px; background: #222; border-radius: 4px;
  overflow: hidden; margin-bottom: 0.5rem;
}
.prof-bar-fill {
  height: 100%; border-radius: 4px; transition: width 0.4s;
}
.subject-meta { font-size: 0.8rem; color: #888; }

.data-table {
  width: 100%; border-collapse: collapse; font-size: 0.85rem;
}
.data-table th {
  text-align: left; padding: 0.6rem 0.5rem;
  border-bottom: 1px solid #333; color: #999; font-weight: 600;
}
.data-table td {
  padding: 0.5rem; border-bottom: 1px solid #1e1e3a; color: #ccc;
}
.data-table tr:hover td { background: rgba(100, 108, 255, 0.05); }

.mini-bar-track {
  display: inline-block; width: 60px; height: 6px;
  background: #222; border-radius: 3px; vertical-align: middle;
  overflow: hidden; margin-right: 0.4rem;
}
.mini-bar-fill { height: 100%; border-radius: 3px; }
.mini-pct { font-size: 0.8rem; }

.due-list { display: flex; flex-direction: column; gap: 0.4rem; }
.due-item {
  display: flex; gap: 1rem; align-items: center;
  padding: 0.5rem 0.8rem; background: #16162a;
  border-radius: 6px; font-size: 0.85rem;
}
.due-subject { color: #999; min-width: 70px; text-transform: capitalize; }
.due-topic { color: #ccc; flex: 1; }
.due-prof { font-weight: 600; }

.lb-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
@media (max-width: 700px) { .lb-grid { grid-template-columns: 1fr; } }
.lb-col h4 { margin: 0 0 0.6rem; color: #aaa; font-size: 0.9rem; }
.lb-row {
  display: flex; gap: 0.5rem; padding: 0.3rem 0; font-size: 0.85rem;
  color: #ccc;
}
.lb-rank { color: #666; width: 25px; }
.lb-user { flex: 1; }
.lb-val { font-weight: 600; color: #f0a500; }

.report-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center; z-index: 1000;
}
.report-modal {
  background: #1a1a2e; border-radius: 12px;
  width: 90%; max-width: 600px; max-height: 80vh;
  overflow-y: auto; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}
.report-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 1.2rem 1.5rem; border-bottom: 1px solid #333;
}
.report-header h3 { margin: 0; color: #eee; font-size: 1.1rem; }
.close-btn {
  background: none; border: none; color: #888; font-size: 1.2rem;
  cursor: pointer; padding: 0.25rem;
}
.close-btn:hover { color: #eee; }
.report-body { padding: 1.5rem; }
.report-stats {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem;
  margin-bottom: 1.5rem;
}
.rstat { text-align: center; }
.rstat-val { display: block; font-size: 1.4rem; font-weight: 700; color: #eee; }
.rstat-label { font-size: 0.75rem; color: #888; text-transform: uppercase; }
.report-section { margin-bottom: 1.2rem; }
.report-section h4 { margin: 0 0 0.5rem; color: #aaa; font-size: 0.9rem; }
.report-item {
  padding: 0.4rem 0.8rem; margin-bottom: 0.3rem;
  border-radius: 4px; font-size: 0.85rem; color: #ccc;
  background: #16162a;
}
.report-item.good { border-left: 3px solid #22c55e; }
.report-item.warn { border-left: 3px solid #f0a500; }
</style>
