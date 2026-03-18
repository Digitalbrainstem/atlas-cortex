<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const activeTab = ref('library')
const error = ref('')
const success = ref('')

// ── Story Library ───────────────────────────────────────────────
const stories = ref([])
const loadingStories = ref(true)
const expandedStoryId = ref(null)
const storyDetail = ref(null)
const loadingDetail = ref(false)

// ── Progress ────────────────────────────────────────────────────
const progress = ref([])
const loadingProgress = ref(true)

// ── Create form ─────────────────────────────────────────────────
const showCreateForm = ref(false)
const creating = ref(false)
const newStory = ref({ title: '', genre: 'adventure', age_group: 'child', total_chapters: 5, interactive: false })

// ── Character voice form ────────────────────────────────────────
const voiceStoryId = ref(null)
const characters = ref([])
const loadingChars = ref(false)
const newChar = ref({ name: '', voice_id: '', voice_style: '', description: '' })
const addingChar = ref(false)

const voiceArchetypes = [
  { label: 'Narrator (Warm)', id: 'af_bella' },
  { label: 'Narrator (Deep)', id: 'am_adam' },
  { label: 'Child', id: 'af_sky' },
  { label: 'Villain', id: 'am_echo' },
  { label: 'Wise Elder', id: 'af_nicole' },
  { label: 'Custom…', id: '' },
]

onMounted(() => {
  fetchStories()
  fetchProgress()
})

async function fetchStories() {
  loadingStories.value = true
  try {
    const data = await api.get('/admin/stories')
    stories.value = data.stories || []
  } catch (e) {
    error.value = e.message
  } finally {
    loadingStories.value = false
  }
}

async function fetchProgress() {
  loadingProgress.value = true
  try {
    const data = await api.get('/admin/stories/progress')
    progress.value = data.progress || []
  } catch (e) {
    error.value = e.message
  } finally {
    loadingProgress.value = false
  }
}

async function toggleDetail(story) {
  if (expandedStoryId.value === story.id) {
    expandedStoryId.value = null
    storyDetail.value = null
    return
  }
  expandedStoryId.value = story.id
  loadingDetail.value = true
  try {
    storyDetail.value = await api.get(`/admin/stories/${story.id}`)
  } catch (e) {
    error.value = e.message
  } finally {
    loadingDetail.value = false
  }
}

async function createStory() {
  creating.value = true
  error.value = ''
  success.value = ''
  try {
    await api.post('/admin/stories', newStory.value)
    success.value = `Story "${newStory.value.title}" created`
    newStory.value = { title: '', genre: 'adventure', age_group: 'child', total_chapters: 5, interactive: false }
    showCreateForm.value = false
    fetchStories()
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}

async function deleteStory(story) {
  if (!confirm(`Delete "${story.title}"?`)) return
  error.value = ''
  success.value = ''
  try {
    await api.delete(`/admin/stories/${story.id}`)
    success.value = `Story "${story.title}" deleted`
    if (expandedStoryId.value === story.id) {
      expandedStoryId.value = null
      storyDetail.value = null
    }
    fetchStories()
  } catch (e) {
    error.value = e.message
  }
}

async function approveStory(story) {
  error.value = ''
  success.value = ''
  try {
    await api.post(`/admin/stories/${story.id}/approve`)
    story.parent_approved = 1
    success.value = `Story "${story.title}" approved`
  } catch (e) {
    error.value = e.message
  }
}

async function openVoiceEditor(storyId) {
  voiceStoryId.value = storyId
  loadingChars.value = true
  try {
    const data = await api.get(`/admin/stories/characters/${storyId}`)
    characters.value = data.characters || []
  } catch (e) {
    error.value = e.message
  } finally {
    loadingChars.value = false
  }
}

function closeVoiceEditor() {
  voiceStoryId.value = null
  characters.value = []
  newChar.value = { name: '', voice_id: '', voice_style: '', description: '' }
}

function selectArchetype(evt) {
  const selected = voiceArchetypes.find(a => a.label === evt.target.value)
  if (selected && selected.id) {
    newChar.value.voice_id = selected.id
  }
}

async function addCharacter() {
  if (!newChar.value.name || !newChar.value.voice_id) return
  addingChar.value = true
  error.value = ''
  try {
    await api.post(`/admin/stories/characters/${voiceStoryId.value}`, newChar.value)
    success.value = `Character "${newChar.value.name}" added`
    newChar.value = { name: '', voice_id: '', voice_style: '', description: '' }
    openVoiceEditor(voiceStoryId.value)
  } catch (e) {
    error.value = e.message
  } finally {
    addingChar.value = false
  }
}
</script>

<template>
  <AppLayout>
    <h2 class="page-title">📖 Story Management</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <!-- Tabs -->
    <div class="tabs">
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'library' }" @click="activeTab = 'library'">Library</button>
      <button class="tab-btn" :class="{ 'tab-btn--active': activeTab === 'progress' }" @click="activeTab = 'progress'">Progress</button>
    </div>

    <!-- Story Library -->
    <div v-if="activeTab === 'library'" class="tab-content">
      <div class="section-header">
        <h3>Story Library</h3>
        <button class="action-btn" @click="showCreateForm = !showCreateForm">
          {{ showCreateForm ? '✕ Cancel' : '+ New Story' }}
        </button>
      </div>

      <!-- Create Form -->
      <div v-if="showCreateForm" class="form-card">
        <div class="form-row">
          <label>Title</label>
          <input v-model="newStory.title" type="text" placeholder="Story title" class="form-input" />
        </div>
        <div class="form-row">
          <label>Genre</label>
          <select v-model="newStory.genre" class="form-input">
            <option value="adventure">Adventure</option>
            <option value="mystery">Mystery</option>
            <option value="fantasy">Fantasy</option>
            <option value="science_fiction">Science Fiction</option>
            <option value="fairy_tale">Fairy Tale</option>
            <option value="educational">Educational</option>
          </select>
        </div>
        <div class="form-row">
          <label>Age Group</label>
          <select v-model="newStory.age_group" class="form-input">
            <option value="child">Child</option>
            <option value="tween">Tween</option>
            <option value="teen">Teen</option>
            <option value="adult">Adult</option>
          </select>
        </div>
        <div class="form-row">
          <label>Chapters</label>
          <input v-model.number="newStory.total_chapters" type="number" min="1" max="50" class="form-input form-input--sm" />
        </div>
        <div class="form-row">
          <label>Interactive</label>
          <label class="toggle-label">
            <input v-model="newStory.interactive" type="checkbox" />
            {{ newStory.interactive ? 'Yes — choices per chapter' : 'No — linear story' }}
          </label>
        </div>
        <button class="action-btn" :disabled="creating || !newStory.title" @click="createStory">
          {{ creating ? 'Creating…' : 'Create Story' }}
        </button>
      </div>

      <!-- Stories Table -->
      <div v-if="loadingStories" class="loading-text">Loading stories…</div>
      <table v-else class="table">
        <thead>
          <tr>
            <th></th>
            <th>Title</th>
            <th>Genre</th>
            <th>Age Group</th>
            <th>Chapters</th>
            <th>Interactive</th>
            <th>Approved</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!stories.length">
            <td colspan="8" class="loading-text">No stories yet</td>
          </tr>
          <template v-for="s in stories" :key="s.id">
            <tr class="story-row" @click="toggleDetail(s)">
              <td class="expand-cell">{{ expandedStoryId === s.id ? '▼' : '▶' }}</td>
              <td>{{ s.title }}</td>
              <td>{{ s.genre }}</td>
              <td>{{ s.target_age_group }}</td>
              <td>{{ s.chapter_count ?? s.total_chapters }}</td>
              <td>{{ s.is_interactive ? '✓' : '—' }}</td>
              <td>
                <span class="badge" :class="s.parent_approved ? 'badge--green' : 'badge--red'">
                  {{ s.parent_approved ? 'Yes' : 'No' }}
                </span>
              </td>
              <td class="action-cell" @click.stop>
                <button v-if="!s.parent_approved" class="sm-btn sm-btn--green" @click="approveStory(s)">Approve</button>
                <button class="sm-btn sm-btn--blue" @click="openVoiceEditor(s.id)">Voices</button>
                <button class="sm-btn sm-btn--red" @click="deleteStory(s)">Delete</button>
              </td>
            </tr>
            <!-- Expanded detail -->
            <tr v-if="expandedStoryId === s.id" class="detail-row">
              <td colspan="8">
                <div v-if="loadingDetail" class="loading-text">Loading detail…</div>
                <div v-else-if="storyDetail" class="detail-content">
                  <div class="detail-section">
                    <h4>Chapters</h4>
                    <table class="table sub-table">
                      <thead>
                        <tr><th>#</th><th>Title</th><th>Voice</th><th>Audio</th><th>Duration</th></tr>
                      </thead>
                      <tbody>
                        <tr v-if="!storyDetail.chapters.length">
                          <td colspan="5" class="loading-text">No chapters</td>
                        </tr>
                        <tr v-for="ch in storyDetail.chapters" :key="ch.id">
                          <td>{{ ch.chapter_number }}</td>
                          <td>{{ ch.title || '—' }}</td>
                          <td>{{ ch.narrator_voice || '—' }}</td>
                          <td>{{ ch.audio_cached ? '✓' : '—' }}</td>
                          <td>{{ ch.duration_seconds ? ch.duration_seconds.toFixed(1) + 's' : '—' }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div class="detail-section">
                    <h4>Characters</h4>
                    <table class="table sub-table">
                      <thead>
                        <tr><th>Name</th><th>Voice</th><th>Style</th><th>Description</th></tr>
                      </thead>
                      <tbody>
                        <tr v-if="!storyDetail.characters.length">
                          <td colspan="4" class="loading-text">No characters</td>
                        </tr>
                        <tr v-for="c in storyDetail.characters" :key="c.id">
                          <td>{{ c.name }}</td>
                          <td>{{ c.voice_id || '—' }}</td>
                          <td>{{ c.voice_style || '—' }}</td>
                          <td>{{ c.description || '—' }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- Progress -->
    <div v-if="activeTab === 'progress'" class="tab-content">
      <div class="section">
        <h3>User Progress</h3>
        <div v-if="loadingProgress" class="loading-text">Loading progress…</div>
        <table v-else class="table">
          <thead>
            <tr>
              <th>User</th>
              <th>Story</th>
              <th>Chapter</th>
              <th>Started</th>
              <th>Last Listened</th>
              <th>Completed</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!progress.length">
              <td colspan="6" class="loading-text">No progress recorded</td>
            </tr>
            <tr v-for="p in progress" :key="p.id">
              <td>{{ p.user_id }}</td>
              <td>{{ p.story_title || `Story #${p.story_id}` }}</td>
              <td>{{ p.current_chapter }}</td>
              <td>{{ p.started_at || '—' }}</td>
              <td>{{ p.last_listened || '—' }}</td>
              <td>
                <span class="badge" :class="p.completed ? 'badge--green' : 'badge--gray'">
                  {{ p.completed ? 'Done' : 'In Progress' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Character Voice Editor Modal -->
    <div v-if="voiceStoryId" class="modal-overlay" @click.self="closeVoiceEditor">
      <div class="modal">
        <div class="modal-header">
          <h3>Character Voices — Story #{{ voiceStoryId }}</h3>
          <button class="close-btn" @click="closeVoiceEditor">✕</button>
        </div>
        <div v-if="loadingChars" class="loading-text">Loading…</div>
        <table v-else class="table">
          <thead>
            <tr><th>Name</th><th>Voice ID</th><th>Style</th></tr>
          </thead>
          <tbody>
            <tr v-if="!characters.length">
              <td colspan="3" class="loading-text">No characters</td>
            </tr>
            <tr v-for="c in characters" :key="c.id">
              <td>{{ c.name }}</td>
              <td>{{ c.voice_id }}</td>
              <td>{{ c.voice_style || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="form-card" style="margin-top: 1rem;">
          <h4 style="margin: 0 0 0.8rem; color: #ccc;">Add Character</h4>
          <div class="form-row">
            <label>Name</label>
            <input v-model="newChar.name" type="text" placeholder="Character name" class="form-input" />
          </div>
          <div class="form-row">
            <label>Archetype</label>
            <select class="form-input" @change="selectArchetype">
              <option value="">— Select —</option>
              <option v-for="a in voiceArchetypes" :key="a.label" :value="a.label">{{ a.label }}</option>
            </select>
          </div>
          <div class="form-row">
            <label>Voice ID</label>
            <input v-model="newChar.voice_id" type="text" placeholder="e.g. af_bella" class="form-input" />
          </div>
          <div class="form-row">
            <label>Style</label>
            <input v-model="newChar.voice_style" type="text" placeholder="warm, gruff, etc." class="form-input" />
          </div>
          <button class="action-btn" :disabled="addingChar || !newChar.name || !newChar.voice_id" @click="addCharacter">
            {{ addingChar ? 'Adding…' : 'Add Character' }}
          </button>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.page-title { margin: 0 0 1.5rem; font-size: 1.5rem; color: #eee; }
.error-banner {
  background: rgba(220,50,50,0.15); border: 1px solid rgba(220,50,50,0.4);
  color: #ff6b6b; padding: 0.8rem 1rem; border-radius: 8px; margin-bottom: 1rem;
}
.success-banner {
  background: rgba(66,184,131,0.15); border: 1px solid rgba(66,184,131,0.4);
  color: #42b883; padding: 0.8rem 1rem; border-radius: 8px; margin-bottom: 1rem;
}
.tabs { display: flex; gap: 0.25rem; margin-bottom: 1.5rem; }
.tab-btn {
  background: #16162a; border: 1px solid #2a2a4a; color: #aaa;
  padding: 0.5rem 1rem; border-radius: 6px; cursor: pointer; font-size: 0.85rem; transition: all 0.2s;
}
.tab-btn:hover { color: #eee; border-color: #646cff; }
.tab-btn--active { background: #646cff; color: #fff; border-color: #646cff; }
.section {
  background: #1a1a2e; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem;
}
.section h3 { margin: 0 0 1rem; color: #ccc; font-size: 1.1rem; }
.section-header {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;
}
.section-header h3 { margin: 0; color: #ccc; font-size: 1.1rem; }
.loading-text { color: #888; text-align: center; padding: 2rem; }
.table { width: 100%; border-collapse: collapse; }
.table th, .table td {
  padding: 0.6rem 0.8rem; text-align: left; border-bottom: 1px solid #2a2a4a; font-size: 0.9rem;
}
.table th { color: #aaa; font-weight: 600; font-size: 0.8rem; text-transform: uppercase; }
.sub-table { margin-top: 0.5rem; }
.sub-table th, .sub-table td { font-size: 0.8rem; padding: 0.4rem 0.6rem; }
.story-row { cursor: pointer; transition: background 0.15s; }
.story-row:hover { background: rgba(100,108,255,0.05); }
.expand-cell { width: 1.5rem; color: #888; font-size: 0.8rem; }
.detail-row td { background: #16162a; padding: 1rem; }
.detail-content { display: flex; flex-direction: column; gap: 1rem; }
.detail-section h4 { margin: 0 0 0.5rem; color: #aaa; font-size: 0.9rem; }
.badge {
  display: inline-block; padding: 0.2rem 0.5rem; border-radius: 4px;
  font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
}
.badge--green { background: rgba(66,184,131,0.15); color: #42b883; }
.badge--blue { background: rgba(100,108,255,0.15); color: #646cff; }
.badge--gray { background: rgba(136,136,136,0.15); color: #888; }
.badge--red { background: rgba(255,107,107,0.15); color: #ff6b6b; }
.action-btn {
  background: #646cff; color: #fff; border: none; border-radius: 6px;
  padding: 0.5rem 1rem; cursor: pointer; font-size: 0.85rem; transition: opacity 0.2s;
}
.action-btn:hover { opacity: 0.85; }
.action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.action-cell { display: flex; gap: 0.4rem; }
.sm-btn {
  background: none; border: 1px solid #2a2a4a; border-radius: 4px;
  padding: 0.25rem 0.5rem; cursor: pointer; font-size: 0.75rem; transition: all 0.2s;
}
.sm-btn--green { color: #42b883; border-color: rgba(66,184,131,0.4); }
.sm-btn--green:hover { background: rgba(66,184,131,0.1); }
.sm-btn--blue { color: #646cff; border-color: rgba(100,108,255,0.4); }
.sm-btn--blue:hover { background: rgba(100,108,255,0.1); }
.sm-btn--red { color: #ff6b6b; border-color: rgba(255,107,107,0.4); }
.sm-btn--red:hover { background: rgba(255,107,107,0.1); }
.form-card {
  background: #16162a; border-radius: 8px; padding: 1.2rem; margin-bottom: 1.5rem;
}
.form-row { display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.7rem; }
.form-row label { color: #aaa; font-size: 0.85rem; min-width: 80px; }
.form-input {
  background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 4px;
  color: #eee; padding: 0.4rem 0.6rem; font-size: 0.85rem; flex: 1;
}
.form-input:focus { border-color: #646cff; outline: none; }
.form-input--sm { max-width: 80px; flex: none; }
.toggle-label { color: #ccc; font-size: 0.85rem; display: flex; align-items: center; gap: 0.5rem; cursor: pointer; }
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 1000;
  display: flex; align-items: center; justify-content: center;
}
.modal {
  background: #1a1a2e; border-radius: 12px; padding: 1.5rem; width: 90%; max-width: 600px;
  max-height: 80vh; overflow-y: auto; border: 1px solid #2a2a4a;
}
.modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
.modal-header h3 { margin: 0; color: #ccc; font-size: 1.1rem; }
.close-btn {
  background: none; border: none; color: #888; font-size: 1.2rem; cursor: pointer;
}
.close-btn:hover { color: #eee; }
</style>
