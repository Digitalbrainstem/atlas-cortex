<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const router = useRouter()
const loading = ref(true)
const error = ref('')
const success = ref('')
const users = ref([])
const page = ref(1)
const totalPages = ref(1)

const showCreate = ref(false)
const newUser = ref({ display_name: '', user_id: '' })
const creating = ref(false)

const columns = [
  { key: 'id', label: 'ID' },
  { key: 'display_name', label: 'Name' },
  { key: 'age_group', label: 'Age Group' },
  { key: 'vocabulary_level', label: 'Vocabulary' },
  { key: 'preferred_voice', label: 'Voice' },
  { key: 'created_at', label: 'Created' },
]

async function fetchUsers() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/admin/users?page=${page.value}`)
    users.value = data.users || data.items || data
    totalPages.value = data.total_pages || data.pages || 1
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(fetchUsers)

function onRowClick(row) {
  router.push(`/users/${row.id}`)
}

function prevPage() {
  if (page.value > 1) {
    page.value--
    fetchUsers()
  }
}

function nextPage() {
  if (page.value < totalPages.value) {
    page.value++
    fetchUsers()
  }
}

async function createUser() {
  creating.value = true
  error.value = ''
  success.value = ''
  try {
    const data = await api.post('/admin/users', {
      display_name: newUser.value.display_name,
      user_id: newUser.value.user_id || undefined,
    })
    success.value = `User "${data.display_name}" created`
    showCreate.value = false
    newUser.value = { display_name: '', user_id: '' }
    fetchUsers()
    setTimeout(() => { success.value = '' }, 3000)
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <AppLayout>
    <div class="header-row">
      <h2 class="page-title">Users</h2>
      <button class="btn btn-primary" @click="showCreate = true">+ Create User</button>
    </div>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="success" class="success-banner">{{ success }}</div>

    <!-- Create user modal -->
    <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
      <div class="modal">
        <h3>Create User</h3>
        <div class="form-group">
          <label class="form-label">Display Name *</label>
          <input v-model="newUser.display_name" class="form-input" placeholder="Derek" />
        </div>
        <div class="form-group">
          <label class="form-label">User ID (optional, auto-generated if empty)</label>
          <input v-model="newUser.user_id" class="form-input" placeholder="user-derek" />
        </div>
        <div class="modal-actions">
          <button class="btn" @click="showCreate = false">Cancel</button>
          <button class="btn btn-primary" :disabled="creating || !newUser.display_name" @click="createUser">
            {{ creating ? 'Creating…' : 'Create' }}
          </button>
        </div>
      </div>
    </div>

    <DataTable
      :columns="columns"
      :rows="users"
      :loading="loading"
      @row-click="onRowClick"
    />
    <div class="pagination">
      <button class="btn" :disabled="page <= 1" @click="prevPage">← Previous</button>
      <span class="page-info">Page {{ page }} of {{ totalPages }}</span>
      <button class="btn" :disabled="page >= totalPages" @click="nextPage">Next →</button>
    </div>
  </AppLayout>
</template>

<style scoped>
.header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}
.page-title {
  margin: 0;
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
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  margin-top: 1.5rem;
}
.page-info {
  color: #aaa;
  font-size: 0.9rem;
}
.btn {
  background: #1a1a2e;
  border: 1px solid #2a2a4a;
  color: #ccc;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
}
.btn:hover:not(:disabled) {
  border-color: #646cff;
  color: #fff;
}
.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.btn-primary {
  background: #646cff;
  border-color: #646cff;
  color: #fff;
}
.btn-primary:hover:not(:disabled) {
  background: #535bf2;
}
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.modal {
  background: #1a1a2e;
  border-radius: 10px;
  padding: 1.5rem;
  width: 400px;
  max-width: 90vw;
}
.modal h3 {
  margin: 0 0 1rem;
  color: #eee;
}
.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin-bottom: 1rem;
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
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.8rem;
  margin-top: 1rem;
}
</style>
