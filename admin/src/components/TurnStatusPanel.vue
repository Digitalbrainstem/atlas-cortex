<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const status = ref(null)
const queue = ref(null)
const turns = ref([])
const user = ref('')
const error = ref('')
const loading = ref(false)

async function refresh() {
  loading.value = true
  error.value = ''
  status.value = null
  queue.value = null
  turns.value = []
  try {
    const [store, scheduler] = await Promise.all([
      api.get('/admin/system/turns'), api.get('/admin/pipeline/queue'),
    ])
    status.value = store
    queue.value = scheduler
    if (user.value.trim()) {
      const data = await api.get(`/admin/system/turns/user?user_id=${encodeURIComponent(user.value.trim())}&limit=${store.policy.prefetch}`)
      turns.value = data.turns
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function control(turn, action) {
  const warning = turn.state === 'uncertain'
    ? 'The previous action may already have happened. Retrying can duplicate it. Confirm retry?'
    : `Confirm ${action} for this turn?`
  if (!window.confirm(warning)) return
  loading.value = true
  error.value = ''
  try {
    await api.post(`/admin/system/turns/${encodeURIComponent(turn.turn_id)}/${action}`, {
      user_id: user.value.trim(), revision: turn.revision,
      retry_uncertain: action === 'recover' && turn.state === 'uncertain',
    })
    await refresh()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <section class="section" aria-label="Durable conversations">
    <h3>Durable conversations</h3>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">Loading durable state...</p>
    <template v-if="status">
      <p>Admission: {{ status.admission_available ? 'available' : 'unavailable' }}.
        Coordinator: {{ queue?.running ? 'running' : 'not running' }}.
        Active: {{ queue?.active ?? 'unknown' }}. Waiting: {{ queue?.depth ?? 'unknown' }}.</p>
      <p>Stored payload: {{ status.payload_bytes }} bytes. State counts: {{ status.counts }}</p>
      <details>
        <summary>Effective resource settings (read-only; restart after configuration changes)</summary>
        <dl v-for="field in status.config_fields" :key="field.key">
          <dt>{{ field.label || field.key }}</dt>
          <dd>{{ field.default }} <small>{{ field.help_text }}</small></dd>
        </dl>
      </details>
    </template>
    <form @submit.prevent="refresh">
      <label>User ID <input v-model="user" :disabled="loading" autocomplete="off"></label>
      <button :disabled="loading">Refresh scoped state</button>
    </form>
    <p>No transcript bodies are returned. Recovery never automatically retries an uncertain action.</p>
    <p v-if="!loading && user.trim() && !turns.length && !error">No retained turns for this user.</p>
    <ul>
      <li v-for="turn in turns" :key="turn.turn_id">
        {{ turn.turn_id }} (revision {{ turn.revision }}): {{ turn.state }};
        delivery {{ turn.delivery_state }}; action {{ turn.action_outcome }}
        <span v-if="turn.error_code">; {{ turn.error_code }}</span>
        <button v-if="['accepted', 'llm_pending', 'llm_running', 'routing'].includes(turn.state)"
          :disabled="loading" @click="control(turn, 'cancel')">Cancel</button>
        <button v-if="turn.can_retry && ['recovery_pending', 'uncertain'].includes(turn.state)"
          :disabled="loading" @click="control(turn, 'recover')">Confirm recovery</button>
        <span v-if="!turn.can_retry && ['recovery_pending', 'uncertain'].includes(turn.state)">
          Verify the external action manually before reissuing it; automatic retry is unavailable.
        </span>
        <span v-if="turn.delivery_state === 'awaiting_confirmation'">
          Confirm old playback on the original device; it will not autoplay.
        </span>
      </li>
    </ul>
  </section>
</template>
