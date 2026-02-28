<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '../components/AppLayout.vue'
import DataTable from '../components/DataTable.vue'
import { api } from '../api.js'

const router = useRouter()
const loading = ref(true)
const error = ref('')
const users = ref([])
const page = ref(1)
const totalPages = ref(1)

const columns = [
  { key: 'id', label: 'ID' },
  { key: 'display_name', label: 'Name' },
  { key: 'age_group', label: 'Age Group' },
  { key: 'vocabulary_level', label: 'Vocabulary' },
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
</script>

<template>
  <AppLayout>
    <h2 class="page-title">Users</h2>
    <div v-if="error" class="error-banner">{{ error }}</div>
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
</style>
