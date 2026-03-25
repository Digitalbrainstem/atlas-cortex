<!-- Public chat interface — profile picker + auth + chat.
     Full-screen, mobile-responsive, no admin chrome. -->
<script setup>
import { ref, onMounted, nextTick, watch } from 'vue'

const state = ref('picker') // picker, auth, chat
const users = ref([])
const selectedUser = ref(null)
const authInput = ref('')
const authError = ref('')
const sessionToken = ref('')
const currentUser = ref(null)
const messages = ref([])
const inputText = ref('')
const streaming = ref(false)
const ws = ref(null)
const messagesEl = ref(null)

function getDeviceFingerprint() {
  const data = [
    navigator.userAgent,
    screen.width, screen.height,
    navigator.language,
    new Date().getTimezoneOffset(),
  ].join('|')
  let hash = 0
  for (let i = 0; i < data.length; i++) {
    hash = ((hash << 5) - hash) + data.charCodeAt(i)
    hash |= 0
  }
  return 'dev_' + Math.abs(hash).toString(36)
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  })
}

watch(messages, scrollToBottom, { deep: true })

onMounted(async () => {
  // Check for existing session
  const saved = localStorage.getItem('atlas_chat_token')
  if (saved) {
    try {
      const r = await fetch(`/api/chat/session?token=${encodeURIComponent(saved)}`)
      const data = await r.json()
      if (data.ok) {
        sessionToken.value = saved
        currentUser.value = data.user
        state.value = 'chat'
        connectWebSocket()
        return
      }
    } catch (e) { /* token invalid, continue to picker */ }
    localStorage.removeItem('atlas_chat_token')
  }

  // Load user list
  try {
    const r = await fetch('/api/chat/users')
    const data = await r.json()
    users.value = data.users || []
  } catch (e) {
    users.value = []
  }

  // If only one user with no auth, auto-login
  if (users.value.length === 1 && !users.value[0].requires_auth) {
    await selectUser(users.value[0])
  }
})

async function selectUser(user) {
  selectedUser.value = user

  if (!user.requires_auth) {
    await authenticate({})
  } else {
    state.value = 'auth'
    authInput.value = ''
    authError.value = ''
  }
}

async function authenticate(extra = {}) {
  const body = {
    user_id: selectedUser.value.user_id,
    device_fingerprint: getDeviceFingerprint(),
    trust_device: true,
    ...extra,
  }

  if (selectedUser.value.auth_method === 'pin') {
    body.pin = authInput.value
  } else if (selectedUser.value.auth_method === 'password') {
    body.password = authInput.value
  }

  try {
    const r = await fetch('/api/chat/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await r.json()

    if (data.ok) {
      sessionToken.value = data.token
      currentUser.value = data.user
      localStorage.setItem('atlas_chat_token', data.token)
      state.value = 'chat'
      connectWebSocket()
    } else {
      authError.value = data.error || 'Authentication failed'
    }
  } catch (e) {
    authError.value = 'Connection error'
  }
}

function connectWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const socket = new WebSocket(`${proto}//${location.host}/ws/chat`)

  socket.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data)
      if (msg.type === 'token') {
        const last = messages.value[messages.value.length - 1]
        if (last && last.role === 'assistant') {
          last.content += msg.text
        } else {
          messages.value.push({ role: 'assistant', content: msg.text })
        }
      } else if (msg.type === 'start') {
        messages.value.push({ role: 'assistant', content: '' })
        streaming.value = true
      } else if (msg.type === 'end') {
        streaming.value = false
      }
    } catch (e) { /* ignore parse errors */ }
  }

  socket.onclose = () => {
    // Reconnect after a short delay
    setTimeout(() => {
      if (state.value === 'chat') connectWebSocket()
    }, 3000)
  }

  ws.value = socket
}

function sendMessage() {
  if (!inputText.value.trim() || !ws.value || ws.value.readyState !== WebSocket.OPEN) return
  const text = inputText.value.trim()
  messages.value.push({ role: 'user', content: text })
  ws.value.send(JSON.stringify({
    type: 'chat',
    message: text,
    user_id: currentUser.value?.user_id || 'guest',
    history: messages.value.slice(-20),
  }))
  inputText.value = ''
}

function logout() {
  localStorage.removeItem('atlas_chat_token')
  state.value = 'picker'
  currentUser.value = null
  sessionToken.value = ''
  messages.value = []
  if (ws.value) {
    ws.value.onclose = null
    ws.value.close()
    ws.value = null
  }
}
</script>

<template>
  <!-- Profile Picker -->
  <div v-if="state === 'picker'" class="fullscreen center-content">
    <div class="logo">🧠</div>
    <h1>Atlas</h1>
    <p class="subtitle">Who's chatting?</p>
    <div class="user-grid">
      <button
        v-for="user in users" :key="user.user_id"
        class="user-card"
        @click="selectUser(user)"
      >
        <span class="user-avatar">{{ user.avatar_url || '👤' }}</span>
        <span class="user-name">{{ user.display_name }}</span>
        <span v-if="user.requires_auth" class="lock">🔒</span>
      </button>
      <button
        class="user-card guest"
        @click="selectUser({ user_id: 'guest', display_name: 'Guest', auth_method: 'none', requires_auth: false })"
      >
        <span class="user-avatar">👋</span>
        <span class="user-name">Guest</span>
      </button>
    </div>
  </div>

  <!-- Auth Input -->
  <div v-if="state === 'auth'" class="fullscreen center-content">
    <div class="logo">🔒</div>
    <h2>{{ selectedUser?.display_name }}</h2>
    <p class="subtitle" v-if="selectedUser?.auth_method === 'pin'">Enter your PIN</p>
    <p class="subtitle" v-else-if="selectedUser?.auth_method === 'password'">Enter your password</p>
    <p class="subtitle" v-else-if="selectedUser?.auth_method === 'passkey'">Use your fingerprint or passkey</p>

    <input
      v-if="selectedUser?.auth_method === 'pin'"
      v-model="authInput"
      type="tel"
      maxlength="6"
      placeholder="• • • •"
      class="pin-input"
      autofocus
      @keydown.enter="authenticate()"
    >
    <input
      v-if="selectedUser?.auth_method === 'password'"
      v-model="authInput"
      type="password"
      placeholder="Password"
      class="password-input"
      autofocus
      @keydown.enter="authenticate()"
    >

    <button
      v-if="selectedUser?.auth_method !== 'passkey'"
      class="btn-primary"
      @click="authenticate()"
    >Continue</button>

    <p v-if="authError" class="error">{{ authError }}</p>
    <button class="btn-back" @click="state = 'picker'">← Back</button>
  </div>

  <!-- Chat Interface -->
  <div v-if="state === 'chat'" class="fullscreen chat-layout">
    <div class="chat-header">
      <span class="chat-user">{{ currentUser?.display_name }}</span>
      <span class="chat-title">Atlas</span>
      <button class="btn-logout" @click="logout()">Switch User</button>
    </div>

    <div class="chat-messages" ref="messagesEl">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="empty-icon">🧠</div>
        <p>Say something to Atlas</p>
      </div>
      <div
        v-for="(msg, i) in messages" :key="i"
        :class="['message', msg.role]"
      >
        <div class="bubble">{{ msg.content }}</div>
      </div>
    </div>

    <div class="chat-input-area">
      <input
        v-model="inputText"
        placeholder="Talk to Atlas..."
        @keydown.enter="sendMessage()"
        autofocus
      >
      <button @click="sendMessage()" :disabled="!inputText.trim() || streaming">Send</button>
    </div>
  </div>
</template>

<style scoped>
* { margin: 0; padding: 0; box-sizing: border-box; }

.fullscreen { min-height: 100vh; background: #0f172a; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
.center-content { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 2rem; }

.logo { font-size: 4rem; margin-bottom: 1rem; }
h1 { font-size: 2.5rem; margin-bottom: 0.5rem; font-weight: 800; }
h2 { font-size: 2rem; margin-bottom: 0.5rem; font-weight: 700; }
.subtitle { color: #94a3b8; margin-bottom: 2rem; font-size: 1.1rem; }

.user-grid { display: flex; flex-wrap: wrap; gap: 1rem; justify-content: center; max-width: 600px; }
.user-card {
  display: flex; flex-direction: column; align-items: center; gap: 0.5rem;
  padding: 1.5rem 2rem; border-radius: 1rem; border: 2px solid #334155;
  background: #1e293b; color: #e2e8f0; cursor: pointer; min-width: 120px;
  font-size: 1rem; position: relative; transition: all 0.2s;
}
.user-card:hover { border-color: #3b82f6; }
.user-card:active { background: #3b82f6; border-color: #3b82f6; }
.user-avatar { font-size: 2.5rem; }
.user-name { font-weight: 600; }
.lock { position: absolute; top: 8px; right: 8px; font-size: 0.8rem; }
.guest { border-style: dashed; opacity: 0.7; }
.guest:hover { opacity: 1; }

.pin-input {
  font-size: 2rem; text-align: center; letter-spacing: 1rem;
  padding: 1rem; width: 220px; border-radius: 0.5rem;
  border: 2px solid #475569; background: #0f172a; color: #e2e8f0;
  margin-bottom: 1rem; outline: none;
}
.pin-input:focus { border-color: #3b82f6; }
.password-input {
  font-size: 1.2rem; padding: 1rem; width: 300px; border-radius: 0.5rem;
  border: 2px solid #475569; background: #0f172a; color: #e2e8f0;
  margin-bottom: 1rem; outline: none;
}
.password-input:focus { border-color: #3b82f6; }

.btn-primary {
  padding: 1rem 3rem; border-radius: 0.5rem; border: none;
  background: #3b82f6; color: white; font-size: 1.1rem; cursor: pointer;
  margin-bottom: 1rem; font-weight: 600; transition: background 0.2s;
}
.btn-primary:hover { background: #2563eb; }
.btn-back { background: none; border: none; color: #64748b; cursor: pointer; font-size: 1rem; margin-top: 0.5rem; }
.btn-back:hover { color: #94a3b8; }
.error { color: #ef4444; margin-top: 0.5rem; font-size: 0.95rem; }

/* Chat layout */
.chat-layout { display: flex; flex-direction: column; height: 100vh; }
.chat-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.75rem 1rem; background: #1e293b; border-bottom: 1px solid #334155;
  flex-shrink: 0;
}
.chat-user { font-size: 0.85rem; color: #94a3b8; }
.chat-title { font-weight: 700; font-size: 1.1rem; }
.btn-logout {
  background: none; border: 1px solid #475569; color: #94a3b8;
  padding: 0.3rem 0.8rem; border-radius: 0.3rem; cursor: pointer; font-size: 0.8rem;
}
.btn-logout:hover { border-color: #64748b; color: #e2e8f0; }

.chat-messages { flex: 1; overflow-y: auto; padding: 1rem; }
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; opacity: 0.4; }
.empty-icon { font-size: 4rem; margin-bottom: 1rem; }
.message { margin-bottom: 1rem; display: flex; }
.message.user { justify-content: flex-end; }
.message.assistant { justify-content: flex-start; }
.bubble {
  max-width: 80%; padding: 0.8rem 1.2rem; border-radius: 1rem;
  line-height: 1.5; white-space: pre-wrap; word-break: break-word;
}
.message.user .bubble { background: #3b82f6; color: white; border-bottom-right-radius: 0.3rem; }
.message.assistant .bubble { background: #1e293b; color: #e2e8f0; border-bottom-left-radius: 0.3rem; }

.chat-input-area {
  display: flex; gap: 0.5rem; padding: 1rem;
  background: #1e293b; border-top: 1px solid #334155; flex-shrink: 0;
}
.chat-input-area input {
  flex: 1; padding: 0.8rem 1rem; border-radius: 0.5rem;
  border: 1px solid #475569; background: #0f172a; color: #e2e8f0;
  font-size: 1rem; outline: none;
}
.chat-input-area input:focus { border-color: #3b82f6; }
.chat-input-area button {
  padding: 0.8rem 1.5rem; border-radius: 0.5rem; border: none;
  background: #3b82f6; color: white; font-size: 1rem; cursor: pointer;
  font-weight: 600; transition: background 0.2s;
}
.chat-input-area button:hover:not(:disabled) { background: #2563eb; }
.chat-input-area button:disabled { background: #475569; cursor: not-allowed; }

@media (max-width: 600px) {
  .user-grid { flex-direction: column; width: 100%; }
  .user-card { min-width: 100%; }
  .bubble { max-width: 90%; }
  .password-input { width: 100%; }
}
</style>
