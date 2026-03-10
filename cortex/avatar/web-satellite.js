/**
 * Web Satellite — browser-based microphone overlay for Atlas Avatar.
 *
 * Adds push-to-talk + VAD hands-free mic capture to the avatar display page.
 * Connects to /ws/satellite as a virtual satellite, streams audio to cortex,
 * and receives TTS audio back via the existing avatar audio playback system.
 *
 * Load in display.html: <script src="/avatar/web-satellite.js"></script>
 * Enable via URL hash: #satellite  or  #satellite,hands-free
 */
(function () {
  'use strict';

  // ── Feature gate ──────────────────────────────────────────────
  const _hash = location.hash.toLowerCase();
  if (!_hash.includes('satellite')) return;

  const handsFreeBoot = _hash.includes('hands-free') || _hash.includes('handsfree');

  // ── Constants ─────────────────────────────────────────────────
  const SAMPLE_RATE = 16000;     // 16kHz mono for STT
  const CHUNK_MS = 100;          // send audio every 100ms
  const VAD_SILENCE_MS = 1500;   // silence before auto-stop in VAD mode
  const VAD_ENERGY_THRESHOLD = 0.008; // RMS energy threshold for speech
  const SATELLITE_ID = 'web-satellite-' + Math.random().toString(36).slice(2, 8);
  const ROOM = new URLSearchParams(location.search).get('room') || 'default';

  // ── State ─────────────────────────────────────────────────────
  let ws = null;
  let micStream = null;
  let audioWorkletNode = null;
  let mediaStreamSource = null;
  let micContext = null;
  let isListening = false;
  let isConnected = false;
  let mode = handsFreeBoot ? 'vad' : 'ptt';  // 'ptt' or 'vad'
  let vadSilenceTimer = null;
  let autoListenPending = false;
  let isSpeaking = false;  // true while Atlas TTS is playing

  // ── UI Setup ──────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #ws-overlay {
      position: fixed; bottom: 20px; right: 20px; z-index: 100;
      display: flex; flex-direction: column; align-items: flex-end; gap: 8px;
      font-family: system-ui, -apple-system, sans-serif;
    }
    #ws-status {
      font-size: 0.7rem; color: #94a3b8; background: rgba(15,23,42,0.8);
      padding: 2px 8px; border-radius: 8px; backdrop-filter: blur(4px);
    }
    #ws-controls {
      display: flex; gap: 8px; align-items: center;
    }
    #ws-mic-btn {
      width: 56px; height: 56px; border-radius: 50%; border: 3px solid #334155;
      background: #1e293b; color: #e2e8f0; font-size: 1.5rem;
      cursor: pointer; transition: all 0.2s; display: flex;
      align-items: center; justify-content: center;
      box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    }
    #ws-mic-btn:hover { background: #334155; }
    #ws-mic-btn.listening {
      border-color: #ef4444; background: #7f1d1d;
      animation: ws-pulse 1.5s infinite;
    }
    #ws-mic-btn.speaking { border-color: #3b82f6; background: #1e3a5f; }
    @keyframes ws-pulse {
      0%, 100% { box-shadow: 0 0 0 0 rgba(239,68,68,0.5); }
      50% { box-shadow: 0 0 0 12px rgba(239,68,68,0); }
    }
    #ws-mode-btn {
      width: 36px; height: 36px; border-radius: 50%; border: 2px solid #334155;
      background: #1e293b; color: #94a3b8; font-size: 0.9rem;
      cursor: pointer; transition: all 0.2s; display: flex;
      align-items: center; justify-content: center;
    }
    #ws-mode-btn:hover { background: #334155; }
    #ws-mode-btn.vad { border-color: #22c55e; color: #22c55e; }
    #ws-level {
      width: 56px; height: 4px; background: #1e293b; border-radius: 2px;
      overflow: hidden;
    }
    #ws-level-bar {
      height: 100%; width: 0%; background: #22c55e; transition: width 50ms;
      border-radius: 2px;
    }
  `;
  document.head.appendChild(style);

  const overlay = document.createElement('div');
  overlay.id = 'ws-overlay';
  overlay.innerHTML = `
    <div id="ws-status">connecting…</div>
    <div id="ws-level"><div id="ws-level-bar"></div></div>
    <div id="ws-controls">
      <button id="ws-mode-btn" title="Toggle mode: Push-to-talk / Hands-free VAD">🎙️</button>
      <button id="ws-mic-btn" title="Push to talk (Space)">🎤</button>
    </div>
  `;
  document.body.appendChild(overlay);

  const statusEl = overlay.querySelector('#ws-status');
  const micBtn = overlay.querySelector('#ws-mic-btn');
  const modeBtn = overlay.querySelector('#ws-mode-btn');
  const levelBar = overlay.querySelector('#ws-level-bar');

  function updateStatus(text) { statusEl.textContent = text; }
  function updateMode() {
    modeBtn.classList.toggle('vad', mode === 'vad');
    modeBtn.title = mode === 'vad'
      ? 'Hands-free VAD (click for push-to-talk)'
      : 'Push-to-talk (click for hands-free VAD)';
    modeBtn.textContent = mode === 'vad' ? '🔊' : '🎙️';
    updateStatus(mode === 'vad' ? 'hands-free' : 'push-to-talk');
  }

  // ── WebSocket to /ws/satellite ────────────────────────────────
  function connectSatellite() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/satellite`;
    ws = new WebSocket(url);

    ws.onopen = () => {
      // Send ANNOUNCE
      ws.send(JSON.stringify({
        type: 'ANNOUNCE',
        satellite_id: SATELLITE_ID,
        hostname: 'web-satellite',
        room: ROOM,
        capabilities: ['web_browser', 'wake_word'],
      }));
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        handleServerMessage(msg);
      } catch (e) {
        console.warn('[web-sat] bad message:', e);
      }
    };

    ws.onclose = () => {
      isConnected = false;
      updateStatus('disconnected');
      // Reconnect after 3s
      setTimeout(connectSatellite, 3000);
    };

    ws.onerror = () => {
      updateStatus('connection error');
    };
  }

  function handleServerMessage(msg) {
    switch (msg.type) {
      case 'ACCEPTED':
        isConnected = true;
        updateMode();
        console.log('[web-sat] connected as', SATELLITE_ID);
        // Suppress avatar WS audio — satellite WS handles audio playback
        if (typeof window.setSuppressAvatarWsAudio === 'function') {
          window.setSuppressAvatarWsAudio(true);
        }
        // If VAD mode, start listening immediately
        if (mode === 'vad') startListeningIfIdle();
        break;

      case 'TTS_START':
        isSpeaking = true;
        micBtn.classList.add('speaking');
        // Stop listening during TTS to avoid echo
        if (isListening && mode === 'vad') stopListening('tts_start');
        // Route TTS to avatar's existing audio player
        if (typeof window.handleTtsStart === 'function') {
          window.handleTtsStart(msg);
        }
        break;

      case 'TTS_CHUNK':
        if (typeof window.handleTtsChunk === 'function') {
          window.handleTtsChunk(msg);
        }
        break;

      case 'TTS_END':
        // Don't clear isSpeaking yet — audio may still be playing through
        // the Web Audio queue. Wait for actual playback to finish.
        micBtn.classList.remove('speaking');
        if (typeof window.handleTtsEnd === 'function') {
          window.handleTtsEnd(msg);
        }
        // Wait for audio playback to fully drain before re-enabling mic
        const remainSec = estimatePlaybackRemaining();
        const guardMs = Math.max(500, remainSec * 1000 + 800);
        setTimeout(() => {
          isSpeaking = false;
          if (mode === 'vad' && !isListening) {
            startListeningIfIdle();
          }
        }, guardMs);
        break;

      case 'PLAY_FILLER':
        // Fillers play as TTS chunks through avatar audio
        break;

      case 'PIPELINE_ERROR':
        updateStatus(msg.detail || 'error');
        setTimeout(() => {
          if (mode === 'vad' && !isListening) startListeningIfIdle();
          else updateMode();
        }, 1500);
        break;

      default:
        console.log('[web-sat] msg:', msg.type);
    }
  }

  function estimatePlaybackRemaining() {
    // Rough estimate: check if avatar audio context has queued playback
    if (window.audioCtx && window.nextPlayTime) {
      const remaining = window.nextPlayTime - window.audioCtx.currentTime;
      return Math.max(0, remaining);
    }
    return 1.5; // default fallback
  }

  // ── Microphone capture ────────────────────────────────────────
  async function ensureMic() {
    if (micStream) return true;

    // navigator.mediaDevices may be undefined on insecure (non-HTTPS) origins
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      console.error('[web-sat] getUserMedia not available — likely needs HTTPS or localhost');
      updateStatus('⚠️ mic needs HTTPS — see console');
      alert(
        'Microphone access requires HTTPS or localhost.\n\n' +
        'Options:\n' +
        '1. Open chrome://flags/#unsafely-treat-insecure-origin-as-secure\n' +
        '   and add: http://' + location.host + '\n' +
        '2. Use Chrome/Edge instead of Firefox\n' +
        '3. Access via http://localhost:5100 if on the same machine'
      );
      return false;
    }

    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: SAMPLE_RATE,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        }
      });

      micContext = new AudioContext({ sampleRate: SAMPLE_RATE });
      mediaStreamSource = micContext.createMediaStreamSource(micStream);

      // Use ScriptProcessorNode (simpler, widely supported)
      const processor = micContext.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = onAudioProcess;
      mediaStreamSource.connect(processor);
      processor.connect(micContext.destination);

      updateStatus('mic ready');
      return true;
    } catch (e) {
      console.error('[web-sat] mic error:', e.name, e.message);
      if (e.name === 'NotAllowedError') {
        updateStatus('⚠️ mic blocked — click 🔒 in address bar');
      } else if (e.name === 'NotFoundError') {
        updateStatus('⚠️ no mic found');
      } else {
        updateStatus('⚠️ mic error: ' + e.name);
      }
      return false;
    }
  }

  let _audioChunkBuffer = [];
  let _lastChunkSent = 0;

  function onAudioProcess(e) {
    if (!isListening) return;

    const input = e.inputBuffer.getChannelData(0);
    // Convert Float32 → Int16 PCM
    const int16 = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }

    // Calculate RMS energy for level meter and VAD
    let sum = 0;
    for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
    const rms = Math.sqrt(sum / input.length);
    levelBar.style.width = Math.min(100, rms * 5000) + '%';

    // VAD: detect silence
    if (mode === 'vad') {
      if (rms > VAD_ENERGY_THRESHOLD) {
        // Speech detected — reset silence timer
        if (vadSilenceTimer) { clearTimeout(vadSilenceTimer); vadSilenceTimer = null; }
      } else if (!vadSilenceTimer && isListening) {
        // Start silence timer
        vadSilenceTimer = setTimeout(() => {
          if (isListening) stopListening('vad_silence');
        }, VAD_SILENCE_MS);
      }
    }

    // Buffer and send chunks
    _audioChunkBuffer.push(int16);
    const now = performance.now();
    if (now - _lastChunkSent >= CHUNK_MS) {
      sendAudioChunks();
      _lastChunkSent = now;
    }
  }

  function sendAudioChunks() {
    if (!ws || ws.readyState !== WebSocket.OPEN || _audioChunkBuffer.length === 0) return;

    // Merge buffered chunks
    const totalLen = _audioChunkBuffer.reduce((s, c) => s + c.length, 0);
    const merged = new Int16Array(totalLen);
    let offset = 0;
    for (const chunk of _audioChunkBuffer) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    _audioChunkBuffer = [];

    // Convert to base64
    const bytes = new Uint8Array(merged.buffer);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    const b64 = btoa(binary);

    ws.send(JSON.stringify({
      type: 'AUDIO_CHUNK',
      audio: b64,
    }));
  }

  // ── Listening control ─────────────────────────────────────────
  async function startListening() {
    if (isListening || isSpeaking) return;
    if (!(await ensureMic())) return;

    isListening = true;
    _audioChunkBuffer = [];
    _lastChunkSent = performance.now();
    micBtn.classList.add('listening');
    updateStatus('listening…');

    // Resume mic context if suspended
    if (micContext && micContext.state === 'suspended') {
      await micContext.resume();
    }

    // Send AUDIO_START
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'AUDIO_START',
        format: 'pcm_16k_16bit_mono',
        format_info: { rate: SAMPLE_RATE, width: 2, channels: 1 },
      }));
    }
  }

  function stopListening(reason = 'manual') {
    if (!isListening) return;
    isListening = false;

    // Flush remaining audio
    sendAudioChunks();

    micBtn.classList.remove('listening');
    levelBar.style.width = '0%';
    if (vadSilenceTimer) { clearTimeout(vadSilenceTimer); vadSilenceTimer = null; }

    updateStatus('processing…');

    // Send AUDIO_END
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'AUDIO_END',
        reason: reason,
      }));
    }
  }

  function startListeningIfIdle() {
    if (!isConnected || isListening || isSpeaking) return;
    startListening();
  }

  // ── Button handlers ───────────────────────────────────────────
  micBtn.addEventListener('mousedown', (e) => {
    e.preventDefault();
    if (mode === 'ptt') {
      startListening();
    } else {
      // In VAD mode, click toggles listening
      if (isListening) stopListening('manual');
      else startListening();
    }
  });

  micBtn.addEventListener('mouseup', () => {
    if (mode === 'ptt' && isListening) {
      stopListening('ptt_release');
    }
  });

  micBtn.addEventListener('mouseleave', () => {
    if (mode === 'ptt' && isListening) {
      stopListening('ptt_release');
    }
  });

  // Touch support
  micBtn.addEventListener('touchstart', (e) => {
    e.preventDefault();
    if (mode === 'ptt') startListening();
    else {
      if (isListening) stopListening('manual');
      else startListening();
    }
  });
  micBtn.addEventListener('touchend', (e) => {
    e.preventDefault();
    if (mode === 'ptt' && isListening) stopListening('ptt_release');
  });

  modeBtn.addEventListener('click', () => {
    if (isListening) stopListening('mode_switch');
    mode = mode === 'ptt' ? 'vad' : 'ptt';
    updateMode();
    if (mode === 'vad' && isConnected && !isSpeaking) {
      startListeningIfIdle();
    }
  });

  // Keyboard: Space = push-to-talk
  document.addEventListener('keydown', (e) => {
    if (e.code === 'Space' && !e.repeat && !e.target.matches('input,textarea,button')) {
      e.preventDefault();
      if (mode === 'ptt') startListening();
    }
  });
  document.addEventListener('keyup', (e) => {
    if (e.code === 'Space' && !e.target.matches('input,textarea,button')) {
      e.preventDefault();
      if (mode === 'ptt' && isListening) stopListening('ptt_release');
    }
  });

  // ── Boot ──────────────────────────────────────────────────────
  updateMode();
  connectSatellite();

})();
