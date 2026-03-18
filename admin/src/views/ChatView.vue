<script setup>
import { ref, nextTick, onMounted, onUnmounted, computed } from 'vue'
import AppLayout from '../components/AppLayout.vue'

// ── State ────────────────────────────────────────────────────────

const messages = ref([])
const inputText = ref('')
const isStreaming = ref(false)
const connected = ref(false)
const inputEl = ref(null)
const messagesEl = ref(null)

let ws = null
let reconnectTimer = null

// ── WebSocket ────────────────────────────────────────────────────

function getWsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/ws/chat`
}

function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return
  }
  ws = new WebSocket(getWsUrl())

  ws.onopen = () => {
    connected.value = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  ws.onclose = () => {
    connected.value = false
    isStreaming.value = false
    reconnectTimer = setTimeout(connect, 3000)
  }

  ws.onerror = () => {
    connected.value = false
  }

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data)
    handleMessage(data)
  }
}

function handleMessage(data) {
  if (data.type === 'start') {
    isStreaming.value = true
    messages.value.push({
      role: 'assistant',
      content: '',
      timestamp: new Date(),
    })
  } else if (data.type === 'token') {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') {
      last.content += data.text
    }
    scrollToBottom()
  } else if (data.type === 'end') {
    isStreaming.value = false
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') {
      last.content = data.full_text
    }
    scrollToBottom()
  } else if (data.type === 'error') {
    isStreaming.value = false
    messages.value.push({
      role: 'system',
      content: data.text || 'An error occurred.',
      timestamp: new Date(),
    })
    scrollToBottom()
  }
}

// ── Send ─────────────────────────────────────────────────────────

function send() {
  const text = inputText.value.trim()
  if (!text || isStreaming.value || !connected.value) return

  messages.value.push({
    role: 'user',
    content: text,
    timestamp: new Date(),
  })

  // Build conversation history (excluding the current message)
  const history = messages.value
    .filter(m => m.role !== 'system')
    .slice(0, -1)
    .map(m => ({ role: m.role, content: m.content }))

  ws.send(JSON.stringify({
    message: text,
    user_id: 'web_user',
    history,
  }))

  inputText.value = ''
  scrollToBottom()
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

// ── Helpers ──────────────────────────────────────────────────────

function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  })
}

function clearChat() {
  messages.value = []
}

function copyMessage(content) {
  navigator.clipboard.writeText(content)
}

function formatTime(date) {
  if (!date) return ''
  const d = date instanceof Date ? date : new Date(date)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

const statusLabel = computed(() => connected.value ? 'Connected' : 'Disconnected')

// ── Lifecycle ────────────────────────────────────────────────────

onMounted(() => {
  connect()
  if (inputEl.value) inputEl.value.focus()
})

onUnmounted(() => {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (ws) ws.close()
})
</script>

<template>
  <AppLayout>
    <div class="chat-page">
      <div class="chat-header">
        <div class="chat-title">
          <span class="chat-icon">💬</span>
          <h1>Chat</h1>
        </div>
        <div class="chat-actions">
          <span class="status-dot" :class="{ online: connected }"></span>
          <span class="status-label">{{ statusLabel }}</span>
          <button class="btn btn-secondary btn-sm" @click="clearChat" :disabled="isStreaming">
            Clear
          </button>
        </div>
      </div>

      <div class="chat-messages" ref="messagesEl">
        <div v-if="messages.length === 0" class="empty-state">
          <span class="empty-icon">🧠</span>
          <p>Start a conversation with Atlas</p>
        </div>

        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="message-row"
          :class="msg.role"
        >
          <div class="message-bubble" :class="msg.role">
            <div class="message-content" v-text="msg.content"></div>
            <div class="message-meta">
              <span class="message-time">{{ formatTime(msg.timestamp) }}</span>
              <button
                v-if="msg.role === 'assistant' && msg.content"
                class="copy-btn"
                @click="copyMessage(msg.content)"
                title="Copy"
              >📋</button>
            </div>
          </div>
        </div>

        <div v-if="isStreaming" class="typing-indicator">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          <span class="typing-label">Atlas is thinking…</span>
        </div>
      </div>

      <div class="chat-input-area">
        <textarea
          ref="inputEl"
          v-model="inputText"
          class="chat-input"
          placeholder="Type a message…"
          rows="1"
          @keydown="onKeydown"
          :disabled="!connected"
        ></textarea>
        <button
          class="send-btn"
          @click="send"
          :disabled="!inputText.trim() || isStreaming || !connected"
        >
          <span>➤</span>
        </button>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 4rem);
  max-width: 900px;
  margin: 0 auto;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.chat-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.chat-title h1 {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--text-primary);
}

.chat-icon { font-size: 1.5rem; }

.chat-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--danger);
  flex-shrink: 0;
}

.status-dot.online { background: var(--success); }

.status-label {
  font-size: 0.75rem;
  color: var(--text-muted);
}

/* ── Messages area ─────────────────────────────────────────── */

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  gap: 0.75rem;
  color: var(--text-muted);
}

.empty-icon { font-size: 3rem; }

.empty-state p { font-size: 1rem; }

/* ── Message bubbles ───────────────────────────────────────── */

.message-row {
  display: flex;
}

.message-row.user { justify-content: flex-end; }
.message-row.assistant { justify-content: flex-start; }
.message-row.system { justify-content: center; }

.message-bubble {
  max-width: 75%;
  padding: 0.625rem 0.875rem;
  border-radius: var(--radius-lg);
  word-break: break-word;
  white-space: pre-wrap;
}

.message-bubble.user {
  background: var(--accent);
  color: #fff;
  border-bottom-right-radius: 4px;
}

.message-bubble.assistant {
  background: var(--bg-card);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-bottom-left-radius: 4px;
}

.message-bubble.system {
  background: transparent;
  color: var(--text-muted);
  font-size: 0.8rem;
  font-style: italic;
}

.message-content {
  font-size: 0.875rem;
  line-height: 1.5;
}

.message-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.25rem;
}

.message-time {
  font-size: 0.65rem;
  color: var(--text-muted);
  opacity: 0.7;
}

.message-bubble.user .message-time {
  color: rgba(255, 255, 255, 0.7);
}

.copy-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.7rem;
  opacity: 0;
  transition: opacity 0.15s;
  padding: 0;
}

.message-bubble:hover .copy-btn { opacity: 1; }

/* ── Typing indicator ──────────────────────────────────────── */

.typing-indicator {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.5rem 0;
  color: var(--text-muted);
  font-size: 0.8rem;
}

.typing-indicator .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-muted);
  animation: bounce 1.2s infinite;
}

.typing-indicator .dot:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator .dot:nth-child(3) { animation-delay: 0.4s; }

.typing-label { margin-left: 0.25rem; }

@keyframes bounce {
  0%, 80%, 100% { transform: translateY(0); }
  40% { transform: translateY(-4px); }
}

/* ── Input area ────────────────────────────────────────────── */

.chat-input-area {
  display: flex;
  align-items: flex-end;
  gap: 0.5rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.chat-input {
  flex: 1;
  resize: none;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-input);
  color: var(--text-primary);
  padding: 0.625rem 0.75rem;
  font-family: var(--font);
  font-size: 0.875rem;
  line-height: 1.5;
  max-height: 120px;
  outline: none;
  transition: border-color 0.15s;
}

.chat-input:focus { border-color: var(--accent); }
.chat-input::placeholder { color: var(--text-muted); }

.send-btn {
  width: 40px;
  height: 40px;
  border: none;
  border-radius: var(--radius);
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
  flex-shrink: 0;
  transition: background 0.15s;
}

.send-btn:hover:not(:disabled) { background: var(--accent-hover); }
.send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* ── Responsive ────────────────────────────────────────────── */

@media (max-width: 768px) {
  .chat-page { height: calc(100vh - 5rem); }
  .message-bubble { max-width: 88%; }
}
</style>
