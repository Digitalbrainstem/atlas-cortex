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

// Voice state
const isRecording = ref(false)
const ttsEnabled = ref(false)
const avatarVisible = ref(false)
const sttAvailable = ref(true)
const ttsAvailable = ref(true)
const isPlayingAudio = ref(false)

let ws = null
let reconnectTimer = null
let mediaRecorder = null
let audioChunks = []
let audioContext = null

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
    // Auto-request TTS if enabled
    if (ttsEnabled.value && data.full_text) {
      requestTTS(data.full_text)
    }
  } else if (data.type === 'error') {
    isStreaming.value = false
    messages.value.push({
      role: 'system',
      content: data.text || 'An error occurred.',
      timestamp: new Date(),
    })
    scrollToBottom()
  } else if (data.type === 'transcript') {
    // STT transcription result
    if (data.text) {
      inputText.value = data.text
      send()
    }
  } else if (data.type === 'tts_audio') {
    playAudio(data.data, data.sample_rate || 24000)
  } else if (data.type === 'tts_error') {
    ttsAvailable.value = false
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

// ── Voice Input ──────────────────────────────────────────────────

async function toggleRecording() {
  if (isRecording.value) {
    stopRecording()
  } else {
    await startRecording()
  }
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true },
    })

    audioChunks = []
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' })

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data)
    }

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop())
      if (audioChunks.length === 0) return

      // Convert to raw PCM via AudioContext decoding
      const blob = new Blob(audioChunks, { type: 'audio/webm' })
      try {
        if (!audioContext) audioContext = new AudioContext({ sampleRate: 16000 })
        const arrayBuf = await blob.arrayBuffer()
        const audioBuf = await audioContext.decodeAudioData(arrayBuf)
        const pcm = audioBuf.getChannelData(0)

        // Convert float32 to int16
        const int16 = new Int16Array(pcm.length)
        for (let i = 0; i < pcm.length; i++) {
          const s = Math.max(-1, Math.min(1, pcm[i]))
          int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff
        }

        // Send as base64 chunks
        const bytes = new Uint8Array(int16.buffer)
        ws.send(JSON.stringify({ type: 'audio_start' }))

        const chunkSize = 32000
        for (let i = 0; i < bytes.length; i += chunkSize) {
          const chunk = bytes.slice(i, i + chunkSize)
          const b64 = btoa(String.fromCharCode(...chunk))
          ws.send(JSON.stringify({ type: 'audio_data', data: b64 }))
        }

        ws.send(JSON.stringify({ type: 'audio_end', sample_rate: 16000 }))
      } catch (err) {
        console.warn('Audio encoding failed:', err)
        sttAvailable.value = false
      }
    }

    mediaRecorder.start(250)
    isRecording.value = true
  } catch (err) {
    console.warn('Microphone access denied:', err)
    sttAvailable.value = false
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop()
  }
  isRecording.value = false
}

// ── Voice Output ─────────────────────────────────────────────────

function requestTTS(text) {
  if (!connected.value || !ws) return
  ws.send(JSON.stringify({ type: 'tts_request', text }))
}

async function playAudio(base64Data, sampleRate) {
  try {
    if (!audioContext) audioContext = new AudioContext({ sampleRate })

    const binaryStr = atob(base64Data)
    const bytes = new Uint8Array(binaryStr.length)
    for (let i = 0; i < binaryStr.length; i++) {
      bytes[i] = binaryStr.charCodeAt(i)
    }

    // Convert int16 PCM to float32
    const int16 = new Int16Array(bytes.buffer)
    const float32 = new Float32Array(int16.length)
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768.0
    }

    const buffer = audioContext.createBuffer(1, float32.length, sampleRate)
    buffer.getChannelData(0).set(float32)

    const source = audioContext.createBufferSource()
    source.buffer = buffer
    source.connect(audioContext.destination)

    isPlayingAudio.value = true
    source.onended = () => { isPlayingAudio.value = false }
    source.start()
  } catch (err) {
    console.warn('Audio playback failed:', err)
    isPlayingAudio.value = false
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
  if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop()
  if (audioContext) audioContext.close().catch(() => {})
})
</script>

<template>
  <AppLayout>
    <div class="chat-page" :class="{ 'with-avatar': avatarVisible }">
      <div class="chat-main">
        <div class="chat-header">
          <div class="chat-title">
            <span class="chat-icon">💬</span>
            <h1>Chat</h1>
          </div>
          <div class="chat-actions">
            <span class="status-dot" :class="{ online: connected }"></span>
            <span class="status-label">{{ statusLabel }}</span>
            <button
              class="voice-toggle"
              :class="{ active: ttsEnabled }"
              @click="ttsEnabled = !ttsEnabled"
              title="Toggle voice output"
            >🔊</button>
            <button
              class="voice-toggle"
              :class="{ active: avatarVisible }"
              @click="avatarVisible = !avatarVisible"
              title="Toggle avatar"
            >🎭</button>
            <button class="btn btn-secondary btn-sm" @click="clearChat" :disabled="isStreaming">
              Clear
            </button>
          </div>
        </div>

        <div class="chat-messages" ref="messagesEl">
          <div v-if="messages.length === 0" class="empty-state">
            <span class="empty-icon">🧠</span>
            <p>Start a conversation with Atlas</p>
            <p class="empty-hint">Type a message or click 🎤 to speak</p>
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
                <button
                  v-if="msg.role === 'assistant' && msg.content && ttsAvailable"
                  class="copy-btn"
                  @click="requestTTS(msg.content)"
                  title="Read aloud"
                >🔊</button>
              </div>
            </div>
          </div>

          <div v-if="isStreaming" class="typing-indicator">
            <span class="dot"></span><span class="dot"></span><span class="dot"></span>
            <span class="typing-label">Atlas is thinking…</span>
          </div>
        </div>

        <div class="chat-input-area">
          <button
            v-if="sttAvailable"
            class="mic-btn"
            :class="{ recording: isRecording }"
            @click="toggleRecording"
            :disabled="!connected"
            :title="isRecording ? 'Stop recording' : 'Start recording'"
          >
            <span class="mic-icon">🎤</span>
          </button>
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

      <!-- Avatar Panel -->
      <div v-if="avatarVisible" class="avatar-panel">
        <div class="avatar-container">
          <div class="avatar-face" :class="{ speaking: isPlayingAudio }">
            <svg viewBox="0 0 200 200" class="avatar-svg">
              <!-- Head -->
              <circle cx="100" cy="100" r="80" fill="#1e293b" stroke="#3b82f6" stroke-width="2"/>
              <!-- Eyes -->
              <ellipse cx="70" cy="85" rx="10" ry="12" fill="#e2e8f0">
                <animate v-if="isPlayingAudio" attributeName="ry" values="12;11;12" dur="0.5s" repeatCount="indefinite"/>
              </ellipse>
              <ellipse cx="130" cy="85" rx="10" ry="12" fill="#e2e8f0">
                <animate v-if="isPlayingAudio" attributeName="ry" values="12;11;12" dur="0.5s" repeatCount="indefinite"/>
              </ellipse>
              <!-- Pupils -->
              <circle cx="72" cy="85" r="5" fill="#0f172a"/>
              <circle cx="132" cy="85" r="5" fill="#0f172a"/>
              <!-- Mouth -->
              <ellipse cx="100" cy="130" rx="20" :ry="isPlayingAudio ? 10 : 3" fill="#3b82f6" class="avatar-mouth">
                <animate v-if="isPlayingAudio" attributeName="ry" values="3;10;6;12;3" dur="0.4s" repeatCount="indefinite"/>
              </ellipse>
              <!-- Glow ring when speaking -->
              <circle v-if="isPlayingAudio" cx="100" cy="100" r="85" fill="none" stroke="#3b82f6" stroke-width="1.5" opacity="0.5">
                <animate attributeName="r" values="85;90;85" dur="1.5s" repeatCount="indefinite"/>
                <animate attributeName="opacity" values="0.5;0.2;0.5" dur="1.5s" repeatCount="indefinite"/>
              </circle>
            </svg>
          </div>
          <div class="avatar-status">
            <span v-if="isPlayingAudio" class="avatar-state speaking">Speaking</span>
            <span v-else-if="isRecording" class="avatar-state listening">Listening</span>
            <span v-else class="avatar-state idle">Ready</span>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.chat-page {
  display: flex;
  gap: 1rem;
  height: calc(100vh - 4rem);
  max-width: 1200px;
  margin: 0 auto;
}

.chat-main {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
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

.voice-toggle {
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  width: 32px;
  height: 32px;
  cursor: pointer;
  font-size: 0.9rem;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
  opacity: 0.6;
}

.voice-toggle:hover { opacity: 1; border-color: var(--border-light); }
.voice-toggle.active { opacity: 1; border-color: var(--accent); background: rgba(59, 130, 246, 0.1); }

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
.empty-hint { font-size: 0.8rem !important; opacity: 0.6; }

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

.mic-btn {
  width: 40px;
  height: 40px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
  flex-shrink: 0;
  transition: all 0.15s;
}

.mic-btn:hover:not(:disabled) { border-color: var(--accent); }
.mic-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.mic-btn.recording {
  background: rgba(239, 68, 68, 0.15);
  border-color: var(--danger);
  animation: pulse-recording 1.5s infinite;
}

@keyframes pulse-recording {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }
  50% { box-shadow: 0 0 0 6px rgba(239, 68, 68, 0); }
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

/* ── Avatar panel ──────────────────────────────────────────── */

.avatar-panel {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: 2rem;
}

.avatar-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
}

.avatar-face {
  width: 200px;
  height: 200px;
  border-radius: 50%;
  overflow: hidden;
  border: 2px solid var(--border);
  transition: border-color 0.3s;
}

.avatar-face.speaking {
  border-color: var(--accent);
}

.avatar-svg {
  width: 100%;
  height: 100%;
}

.avatar-status {
  text-align: center;
}

.avatar-state {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.avatar-state.idle { color: var(--text-muted); }
.avatar-state.listening { color: var(--accent); }
.avatar-state.speaking { color: var(--success); }

/* ── Responsive ────────────────────────────────────────────── */

@media (max-width: 768px) {
  .chat-page {
    flex-direction: column;
    height: calc(100vh - 3.5rem);
  }

  .chat-page.with-avatar {
    flex-direction: column;
  }

  .avatar-panel {
    width: 100%;
    padding: 0.5rem 0;
    flex-direction: row;
    justify-content: center;
    flex-shrink: 0;
    order: -1;
  }

  .avatar-face {
    width: 80px;
    height: 80px;
  }

  .avatar-container {
    flex-direction: row;
    gap: 0.5rem;
  }

  .message-bubble { max-width: 88%; }

  .chat-input-area {
    position: sticky;
    bottom: 0;
    background: var(--bg-primary);
    padding-bottom: 0.5rem;
  }
}
</style>
