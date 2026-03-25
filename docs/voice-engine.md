# Atlas Cortex — Voice & Speech Engine

## Overview

Atlas owns TTS (text-to-speech), not Home Assistant. Atlas knows the **sentiment**, **confidence**, **user profile**, and **conversation context** — it decides not just *what* to say but *how* to say it. The voice engine translates Atlas's emotional state into expressive, natural-sounding speech with human-like pauses, laughs, sighs, and tonal variation.

### Why TTS Lives with Atlas

| If TTS is at HA... | If TTS is at Atlas... |
|--------------------|-----------------------|
| Atlas sends plain text → HA runs Piper → flat, robotic | Atlas knows the emotion → injects tags/style → natural, expressive |
| No way to convey laughter, sarcasm, warmth | `<laugh>`, `<sigh>`, emotional descriptors baked into output |
| HA picks the voice → same for everyone | Atlas picks voice per user, per emotion, per context |
| Avatar sync requires separate coordination | TTS + avatar + emotion all in one pipeline |
| HA becomes a bottleneck for voice quality | Atlas controls the full experience end-to-end |

---

## TTS Provider Abstraction

Like the LLM provider interface, TTS is abstracted behind a provider interface so users can choose their engine:

```python
class TTSProvider:
    """Abstract interface for any TTS backend."""
    
    async def synthesize(self, text, voice=None, emotion=None, 
                         speed=1.0, stream=True, **kwargs):
        """Convert text to audio. Returns async generator of audio chunks if stream=True."""
        raise NotImplementedError
    
    async def list_voices(self):
        """List available voices. Returns list of {id, name, gender, style, language}."""
        raise NotImplementedError
    
    def supports_emotion(self):
        """Whether this provider supports emotional speech synthesis."""
        return False
    
    def supports_streaming(self):
        """Whether this provider supports chunked audio streaming."""
        return False
    
    def supports_phonemes(self):
        """Whether this provider outputs phoneme timing for avatar lip-sync."""
        return False
    
    def get_emotion_format(self):
        """How to encode emotion for this provider."""
        return None  # 'tags' | 'description' | 'ssml' | None
```

### Supported Providers

| Provider | Status | Emotion Support | Streaming | Quality | Resource | Best For |
|----------|--------|----------------|-----------|---------|----------|----------|
| **Qwen3-TTS** (primary) | ✅ Active | ✅ Instruction-based style/emotion control | ✅ | Excellent — 1.7B params, 10 languages, 9 speakers | GPU: 6GB+ VRAM | Primary voice engine, highest quality |
| **Fish Audio S2** | ✅ Active | ✅ Multi-speaker dialogue | ✅ | Excellent — voice cloning, multi-character | GPU: 4GB+ VRAM | Story narration, character voices |
| **Orpheus TTS** | ✅ Available | ✅ Inline tags: `<laugh>`, `<sigh>`, emotion descriptors | ✅ | Excellent — human-like paralinguals | 3B: 6-8GB (Q4) | Emotional speech, GPU backup |
| **Kokoro** | ✅ Available | ⚠️ Voice-based (no inline tags) | ✅ | Excellent — natural, expressive, 82M params | CPU: ~100MB RAM | Fast CPU fallback |
| **Piper** | ✅ Available | ❌ Basic SSML only | ✅ | Good — fast, robotic for complex emotion | CPU only, ~100MB RAM | Ultra-low-latency last resort |

### Current Production Stack

Qwen3-TTS is the primary TTS engine, running as a Docker container (`atlas-qwen-tts`, port 8766). It provides:
- **9 built-in speakers** across English, Chinese, Japanese, and Korean
- **Instruction-based control** — describe emotion/style in natural language
- **Voice design** — create new voices from text descriptions
- **Voice cloning** — clone any voice from 3 seconds of audio

Kokoro remains the fast CPU fallback for latency-sensitive paths (fillers, instant answers).

```
User speaks → STT processes audio (GPU: whisper.cpp)
              → Atlas pipeline runs (CPU)
              → LLM generates response text (GPU: Ollama)
              → Qwen3-TTS generates speech (GPU, port 8766)
              → Fallback: Kokoro (CPU, port 8880) or Piper (CPU, port 10200)
              → Audio streams to satellite while TTS still generating
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TTS_PROVIDER` | `qwen3_tts` | Primary TTS engine (`qwen3_tts`, `orpheus`, `kokoro`, `piper`, `auto`) |
| `QWEN_TTS_HOST` | `localhost` | Qwen3-TTS server hostname |
| `QWEN_TTS_PORT` | `8766` | Qwen3-TTS server port |
| `KOKORO_HOST` | `localhost` | Kokoro server hostname |
| `KOKORO_PORT` | `8880` | Kokoro server port |
| `KOKORO_VOICE` | `af_bella` | Default Kokoro voice ID |
| `FISH_AUDIO_HOST` | `localhost` | Fish Audio S2 hostname |
| `FISH_AUDIO_PORT` | `8860` | Fish Audio S2 port |

### Fallback Chain

1. **Qwen3-TTS** (default) — GPU, highest quality, 9 speakers, 10 languages
2. **Fish Audio S2** — GPU, multi-character story narration
3. **Orpheus** — GPU, emotional tags (`<laugh>`, `<sigh>`)
4. **Kokoro** — CPU, fast fallback (~200ms)
5. **Piper** — CPU, ultra-fast last resort

---

## Emotion-to-Speech Pipeline

Atlas's sentiment analysis and confidence scoring feed directly into the TTS engine:

```
┌─────────────────────────────────────────────────────────────────┐
│                  Emotion-to-Speech Pipeline                      │
│                                                                  │
│  Atlas Pipeline Output:                                          │
│    text: "Sure! I turned off the living room lights."           │
│    sentiment: positive (0.72)                                    │
│    confidence: high (0.95)                                       │
│    user_profile: { preferred_voice: "tara", age_group: "adult" }│
│    context: casual evening conversation                          │
│                                                                  │
│       │                                                          │
│       ▼                                                          │
│  Emotion Composer                                                │
│    • Maps sentiment → TTS emotion format                        │
│    • Adjusts for user profile (warmer for toddlers, etc.)       │
│    • Adds paralinguals where appropriate                         │
│    • Selects voice based on user preference                     │
│       │                                                          │
│       ▼                                                          │
│  TTS Provider (Kokoro):                                         │
│    Input: "Sure! I turned off the                               │
│            living room lights."                                  │
│    Voice: af_bella (warm, natural)                              │
│    Output: audio stream (24kHz PCM)                             │
│       │                                                          │
│       ├──▶ Audio → satellite speaker                            │
│       └──▶ Phonemes → avatar viseme animation                   │
└─────────────────────────────────────────────────────────────────┘
```

### Emotion Composer

The emotion composer translates Atlas's internal state into provider-specific format:

```python
class EmotionComposer:
    """Translates Atlas sentiment/context into TTS emotion instructions."""
    
    def compose(self, text, sentiment, confidence, user_profile, context):
        """Add emotion markup to text for TTS provider.
        
        Returns:
            str: Emotion-annotated text for tag/ssml/plain providers.
            tuple[str, str]: (text, description) for description-based providers (Parler).
        """
        
        provider = get_tts_provider()
        fmt = provider.get_emotion_format()
        
        if fmt == 'tags':  # Orpheus
            return self._compose_orpheus(text, sentiment, confidence, context)
        elif fmt == 'description':  # Parler
            return self._compose_parler(text, sentiment, confidence, user_profile)
        elif fmt == 'ssml':  # Piper (limited)
            return self._compose_ssml(text, sentiment)
        else:
            return text  # plain text fallback
    
    def _compose_orpheus(self, text, sentiment, confidence, context):
        """Orpheus: inline emotion tags."""
        
        # Determine base emotion
        emotion = self._sentiment_to_orpheus_emotion(sentiment)
        
        # Add paralinguals based on context
        if context.get('is_joke') or sentiment.label == 'amused':
            text = f"{text} <chuckle>"
        
        if sentiment.label == 'frustrated_user' and confidence > 0.8:
            # Atlas is empathetic
            text = f"<sigh> {text}"
        
        if context.get('is_whisper') or context.get('is_secret'):
            return f"whisper: {text}"
        
        if context.get('is_excited'):
            return f"happy, fast: {text}"
        
        # Default: prepend emotion descriptor
        if emotion:
            return f"{emotion}: {text}"
        return text
    
    def _compose_parler(self, text, sentiment, confidence, user_profile):
        """Parler: natural language voice description."""
        
        # Build description from user profile + sentiment
        voice = user_profile.get('preferred_voice_description', 
                                 'A warm, clear adult voice')
        
        emotion_adj = {
            'positive': 'friendly and warm',
            'negative': 'calm and empathetic',
            'neutral': 'conversational and clear',
            'excited': 'energetic and lively',
            'frustrated': 'patient and understanding',
        }.get(sentiment.category, 'natural and clear')
        
        description = f"{voice}, {emotion_adj} tone, moderate pace"
        
        return text, description  # Parler takes text + description separately
    
    def _sentiment_to_orpheus_emotion(self, sentiment):
        """Map VADER sentiment to Orpheus emotion descriptors."""
        
        mapping = {
            'very_positive': 'happy',
            'positive': 'warm',
            'neutral': None,          # no emotion tag = natural
            'negative': 'concerned',
            'very_negative': 'sad',
            'excited': 'excited',
            'amused': 'amused',
            'empathetic': 'gentle',
            'serious': 'serious',
            'encouraging': 'enthusiastic',
        }
        return mapping.get(sentiment.label)
```

### Paralingual Injection Rules

Atlas adds non-speech sounds where they feel natural:

| Context | Paralingual | Example |
|---------|-------------|---------|
| Telling a joke or being playful | `<chuckle>` at end | "That's a terrible pun. <chuckle>" |
| Empathizing with frustration | `<sigh>` at start | "<sigh> Yeah, that's annoying. Let me fix it." |
| Surprise or discovery | `<gasp>` (brief) | "<gasp> Oh, I found something interesting!" |
| Thinking / uncertainty | Filled pause | "Hmm... let me check on that." |
| Whispering a secret / night mode | `whisper:` prefix | "whisper: The kids are asleep — keeping it quiet." |
| Late night / calm mode | Slower, softer tone | `calm, slow: You should probably get some sleep.` |
| Excited good news | `happy, fast:` | "happy, fast: Your package just arrived!" |

**Rules:**
- Never use the same paralingual twice in a row
- Maximum 1 paralingual per response for short answers
- Long responses can have 2-3, spaced naturally
- Age-appropriate: no sarcastic sighs for toddlers
- Paralinguals are **optional** — most responses have none (just emotion tone)

---

## Voice Selection

### Per-User Voice Preference

Users can choose their preferred Atlas voice:

```
"Atlas, I want you to sound like Tara"
→ UPDATE user_profiles SET preferred_voice = 'tara' WHERE user_id = 'derek'

"Atlas, try a different voice"
→ Play samples of available voices, let user pick

"Atlas, use your default voice"
→ SET preferred_voice = NULL (use system default)
```

### Voice Registry

```sql
CREATE TABLE tts_voices (
    id TEXT PRIMARY KEY,               -- 'af_bella', 'orpheus_tara', 'piper_amy'
    provider TEXT NOT NULL,            -- 'kokoro' | 'orpheus' | 'piper'
    display_name TEXT NOT NULL,        -- 'Bella', 'Tara', 'Amy'
    gender TEXT,                       -- 'female' | 'male' | 'neutral'
    language TEXT DEFAULT 'en',
    accent TEXT,                       -- 'american' | 'british' | 'australian'
    style TEXT,                        -- 'warm' | 'professional' | 'casual' | 'energetic'
    supports_emotion BOOLEAN DEFAULT TRUE,
    sample_audio_path TEXT,            -- path to preview audio clip
    is_default BOOLEAN DEFAULT FALSE,
    metadata TEXT                      -- provider-specific config (JSON)
);

-- Kokoro voices (loaded dynamically from Kokoro server at startup)
-- Naming convention: {lang}{gender}_{name} — e.g. af_bella (American Female Bella)
-- Common voices: af_bella (default), am_adam, bf_emma, bm_daniel

-- Orpheus built-in voices
INSERT INTO tts_voices (id, provider, display_name, gender, style) VALUES
('orpheus_tara', 'orpheus', 'Tara', 'female', 'warm'),
('orpheus_leah', 'orpheus', 'Leah', 'female', 'energetic'),
('orpheus_jess', 'orpheus', 'Jess', 'female', 'casual'),
('orpheus_leo', 'orpheus', 'Leo', 'male', 'professional'),
('orpheus_dan', 'orpheus', 'Dan', 'male', 'casual'),
('orpheus_mia', 'orpheus', 'Mia', 'female', 'gentle'),
('orpheus_zac', 'orpheus', 'Zac', 'male', 'energetic'),
('orpheus_anna', 'orpheus', 'Anna', 'female', 'professional');
```

### Contextual Voice Adaptation

| Context | Adaptation |
|---------|-----------|
| Talking to toddler | Slower pace, warmer tone, simpler intonation |
| Talking to teen | Casual, slightly faster, modern inflection |
| Late night | Quieter, calmer, slower |
| User is frustrated | Patient, steady, empathetic |
| Delivering exciting news | Slightly faster, more energy |
| Reading something serious | Even pace, clear enunciation |

---

## Audio Streaming & Latency

### The Latency Problem

TTS must feel instant. Every millisecond of silence after Atlas generates text feels unnatural.

### Streaming Solution

```
LLM generates text token by token
    │
    ▼ (sentence boundary detected)
First complete sentence extracted
    │
    ▼
Emotion composer adds tags
    │
    ▼
TTS provider starts generating audio for sentence 1
    │
    ├──▶ Audio chunk 1 → satellite speaker (starts playing)
    ├──▶ Phonemes → avatar starts animating
    │
Meanwhile: LLM still generating sentence 2...
    │
    ▼ (sentence 2 complete)
TTS generates audio for sentence 2
    │
    ├──▶ Audio chunk 2 → queued, plays after chunk 1
    ...
```

### Sentence-Boundary Streaming

```python
async def stream_speech(text_stream, emotion, voice, user_profile):
    """Stream TTS audio as sentences complete, while LLM is still generating."""
    
    buffer = ""
    tts = get_tts_provider()
    
    async for token in text_stream:
        buffer += token
        
        # Check for sentence boundary
        sentence, remaining = extract_complete_sentence(buffer)
        
        if sentence:
            buffer = remaining
            
            # Compose emotion for this sentence
            tagged = emotion_composer.compose(
                sentence, emotion, user_profile.confidence, 
                user_profile, context
            )
            
            # Generate and stream audio
            async for audio_chunk in tts.synthesize(
                tagged, voice=voice, stream=True
            ):
                yield {
                    'audio': audio_chunk,
                    'text': sentence,
                    'phonemes': audio_chunk.get('phonemes'),  # for avatar
                    'emotion': emotion.label,
                }
    
    # Flush remaining buffer
    if buffer.strip():
        tagged = emotion_composer.compose(buffer, emotion, ...)
        async for audio_chunk in tts.synthesize(tagged, voice=voice, stream=True):
            yield {'audio': audio_chunk, 'text': buffer}
```

### Latency Targets (Measured)

Real production timing from voice pipeline benchmark (35 questions, WebSocket path):

| Stage | Measured | Target | Notes |
|-------|----------|--------|-------|
| STT (Whisper.cpp Vulkan) | **190ms avg** (176-251ms) | <300ms | ✅ Exceeds target |
| LLM first token (TTFT) | **~3800ms avg** | <500ms | ❌ Filler fills this gap |
| LLM total | **4371ms avg** (1768-6672ms) | <3000ms | Qwen 2.5 7B on Intel B580 |
| First TTS audio to user | **4620ms avg** | <2000ms | Includes filler delivery |
| Total end-to-end | **8567ms avg** (6518-11291ms) | <5000ms | Room for optimization |

### Filler Audio Strategy

Pre-generated filler phrases fill the gap between wake word and first real TTS response:

```
User says "Hey Jarvis, what time is it?"
    │
    ▼ (STT: ~190ms)
Transcribe audio → text
    │
    ├──▶ Select cached filler audio (0ms lookup)
    ├──▶ Stream filler to satellite ("Let me check on that...")
    │
    ▼ (Pipeline: 0-5ms for Layer 1, ~4000ms for Layer 3)
Generate response text
    │
    ▼ (Kokoro TTS: ~200ms base + 180ms/word)
Synthesize response audio, sentence by sentence
    │
    ├──▶ Audio stream → satellite speaker
    ...
```

Filler cache is populated at satellite connection time with 5+ pre-generated phrases.
Phrases are deduplicated to avoid repetition. Secondary filler fires if primary
response takes >3s.

### Fast-Path for Instant Answers

Layer 1/2 responses (instant answers, device commands) bypass the LLM entirely:

```
User: "Turn off the lights"
    │
    ▼ (Layer 2: ~100ms)
Text: "Done — living room lights off."
    │
    ▼ (Kokoro TTS: ~500ms for short phrase)
Audio → satellite speaker
    │
Total: ~700ms (near-instant feel)
```

---

## TTS Provider: Kokoro (Primary)

### Why Kokoro

- **82M parameter model** — lightweight, runs entirely on CPU
- **Sub-2s synthesis** for typical sentences (200ms base + 180ms/word)
- **24kHz 16-bit PCM** output — high quality audio
- **Multiple voices** with language/gender prefix naming (`af_bella`, `am_adam`, etc.)
- **Standalone Docker container** — `kokoro-tts` on port 8880
- **OpenAI-compatible API** — `/v1/audio/speech` endpoint
- **Apache 2.0 license**

### Configuration

```bash
# Environment variables
TTS_PROVIDER=kokoro          # Select Kokoro as primary (default)
KOKORO_HOST=localhost        # Kokoro server hostname
KOKORO_PORT=8880             # Kokoro server port
KOKORO_VOICE=af_bella        # Default voice
```

### Usage in Pipeline

Kokoro is used directly via `KokoroClient` in `cortex/voice/kokoro.py`:

```python
from cortex.voice.kokoro import KokoroClient

kokoro = KokoroClient(host="localhost", port=8880)
pcm_audio, info = await kokoro.synthesize(
    text="Hello there!",
    voice="af_bella",
    response_format="wav"
)
# info: {"sample_rate": 24000, "duration_ms": 1200}
```

---

## TTS Provider: Orpheus via Ollama (Alternate)

### Why Orpheus + Ollama

- **Already have Ollama running** with ROCm support
- **Orpheus has a GGUF model on Ollama** (`legraphista/Orpheus`)
- **Shared GPU management** — Ollama handles model loading/unloading
- **ROCm supported** via Docker (`docker-compose-gpu-rocm.yml` in Orpheus-FastAPI-ollama)
- **Best emotional control** — inline tags for laugh, sigh, whisper, plus emotion descriptors
- **8 built-in voices** — male and female options
- **Apache 2.0 license**

### Ollama Integration

```python
class OrpheusTTSProvider(TTSProvider):
    """Orpheus TTS via Ollama or Orpheus-FastAPI."""
    
    def __init__(self, config):
        # Option 1: Direct Ollama API
        self.ollama_url = config.get('ORPHEUS_URL', 'http://localhost:11434')
        self.model = config.get('ORPHEUS_MODEL', 'legraphista/Orpheus:latest')
        
        # Option 2: Orpheus-FastAPI server (more features)
        self.fastapi_url = config.get('ORPHEUS_FASTAPI_URL')
    
    async def synthesize(self, text, voice='tara', emotion=None, 
                         speed=1.0, stream=True, **kwargs):
        """Generate speech via Orpheus."""
        
        # Format: "voice_name: emotion_descriptor: text"
        prompt = self._format_prompt(text, voice, emotion)
        
        if self.fastapi_url:
            return self._synthesize_fastapi(prompt, stream)
        else:
            return self._synthesize_ollama(prompt, stream)
    
    def _format_prompt(self, text, voice, emotion):
        """Format text with Orpheus voice and emotion tags."""
        parts = []
        if voice:
            parts.append(voice)
        if emotion:
            parts.append(emotion)
        
        prefix = ", ".join(parts)
        if prefix:
            return f"{prefix}: {text}"
        return text
    
    def supports_emotion(self):
        return True
    
    def supports_streaming(self):
        return True
    
    def get_emotion_format(self):
        return 'tags'
```

### VRAM Management

**Multi-GPU (recommended):**
```
GPU 0 — RX 7900 XT (20GB)         GPU 1 — Arc B580 (12GB)
┌──────────────────────────┐      ┌──────────────────────────┐
│ LLM: qwen3:30b  18.6 GB │      │ Orpheus Q4:    ~6-8 GB   │
│ KV cache:       ~1-4 GB  │      │ faster-whisper: ~1 GB    │
│ Headroom:       ~1-2 GB  │      │ speaker-id:    ~0.5 GB   │
│                          │      │ Headroom:      ~2-4 GB   │
│ Ollama :11434            │      │ Ollama/IPEX :11435       │
│ HIP_VISIBLE_DEVICES=0   │      │ ONEAPI_DEVICE_SELECTOR=1 │
└──────────────────────────┘      └──────────────────────────┘
```
Both models stay resident — no switching, no latency penalty.

**Single-GPU fallback:**
```
LLM active (qwen3:30b-a3b): 18.6 GB VRAM
    │ (LLM finishes, Ollama idle timeout → unload)
    ▼
Orpheus loads (Q4 GGUF): ~6-8 GB VRAM
    │ (TTS finishes, Ollama idle timeout → unload)
    ▼
GPU free (or LLM reloads for next request)
```

Configure Ollama for single-GPU mode:
```env
# Single-GPU Ollama config
OLLAMA_NUM_PARALLEL=1           # one model at a time
OLLAMA_MAX_LOADED_MODELS=1      # unload previous before loading next
OLLAMA_KEEP_ALIVE=60s           # keep model loaded for 60s after last use
```

Configure for multi-GPU mode:
```env
# GPU 0 — LLM instance (Ollama native or ROCm Docker)
OLLAMA_HOST=0.0.0.0:11434
HIP_VISIBLE_DEVICES=0
OLLAMA_NUM_PARALLEL=1
OLLAMA_KEEP_ALIVE=0             # keep model loaded forever

# GPU 1 — Voice instance (IPEX-LLM Ollama Docker or separate Ollama)
OLLAMA_HOST=0.0.0.0:11435
ONEAPI_DEVICE_SELECTOR=level_zero:0   # B580 sees itself as device 0 in its container
OLLAMA_NUM_PARALLEL=1
OLLAMA_KEEP_ALIVE=0             # Orpheus stays loaded forever
```

---

## HA Voice Pipeline Integration

### How Voice Flows

```
HA Voice Pipeline:
  STT Engine: faster-whisper (Wyoming, port 10300)
  Conversation Agent: Atlas Cortex (OpenAI-compatible, port 5100)
  TTS Engine: Atlas Cortex TTS (port 5100/v1/audio/speech)

Flow:
  1. Satellite mic → audio to HA
  2. HA → faster-whisper (STT) → text
  3. HA → Atlas Cortex (:5100) with text + satellite_id
  4. Atlas processes (pipeline layers 0-3)
  5. Atlas generates response text with emotion
  6. Atlas → Orpheus TTS → audio stream
  7. Audio → HA → satellite speaker
  8. Phonemes → avatar server → satellite display
```

### Atlas TTS API Endpoint

Atlas exposes an OpenAI-compatible speech endpoint:

```
POST /v1/audio/speech
{
    "input": "Sure! I turned off the lights.",
    "voice": "tara",
    "model": "orpheus",
    "response_format": "wav",
    
    // Atlas extensions (optional)
    "emotion": "warm",
    "speed": 1.0,
    "include_phonemes": true
}

Response: audio/wav stream (+ X-Phonemes header if requested)
```

### For HA: Atlas as TTS Provider

HA can use Atlas's TTS endpoint via the `openai_tts` integration or a custom Wyoming adapter:

```yaml
# Option A: OpenAI TTS integration in HA
# Settings → Integrations → OpenAI TTS
#   URL: http://192.168.3.8:5100/v1
#   Voice: tara
#   Model: orpheus

# Option B: Wyoming TTS adapter (custom, richer metadata)
# Atlas runs a Wyoming TTS server that wraps Orpheus
# HA discovers it via Wyoming protocol
```

---

## Night Mode / Quiet Hours

Atlas automatically adjusts voice behavior based on time and context:

| Time/Context | Voice Behavior |
|-------------|---------------|
| After 10 PM | Quieter volume instruction, slower pace, calmer emotion |
| Confirmed bedtime | Whisper mode for non-urgent responses |
| Baby sleeping (presence sensor + time) | Ultra-quiet, whisper only |
| Morning (6-9 AM) | Bright but not loud, moderate pace |
| Active household (daytime) | Normal volume, full emotional range |

```python
def apply_context_modifiers(emotion, voice_config, context):
    """Adjust TTS parameters based on time and spatial context."""
    
    hour = context.get('hour', datetime.now().hour)
    
    if 22 <= hour or hour < 6:
        # Night mode
        voice_config['speed'] = 0.9
        voice_config['volume_hint'] = 'quiet'
        if emotion.energy > 0.5:
            emotion = emotion.soften()  # reduce intensity
    
    if context.get('baby_sleeping'):
        voice_config['speed'] = 0.8
        emotion = Emotion('whisper')
    
    return emotion, voice_config
```

---

## Voice Discovery at Install

During C0 (installer), the voice engine is discovered and configured:

```
[Voice Engine Setup]

Scanning for TTS options...
  ✓ Kokoro available at :8880 (primary, fast CPU synthesis)
  ✓ Ollama available — can run Orpheus TTS (emotional, 3B model)
  ✓ Piper available at :10200 (fast fallback)

Recommended setup:
  Primary TTS: Kokoro (natural speech, CPU, sub-2s synthesis)
  Alternate TTS: Orpheus via Ollama (emotional speech, GPU)
  Fallback TTS: Piper (fast, for instant confirmations)

  Pull Orpheus model? (~4GB quantized) [Y/n]
  > Y

  Pulling legraphista/Orpheus:Q4... ████████████ 4.2 GB  ✓
  
  Available voices: Tara, Leah, Jess, Leo, Dan, Mia, Zac, Anna
  Default voice: Tara (warm female)
  
  Play sample? [Y/n] > Y
  🔊 "Hi! I'm Atlas. I'll be your assistant."
  
  Keep Tara as default? [Y/n/try another] > Y
```

---

## Integration Points

| System | Integration |
|--------|-------------|
| **Architecture (Layer 3)** | LLM output → sentence splitter → Kokoro TTS → audio stream |
| **Architecture (Layer 1/2)** | Instant answers → Kokoro fast path → audio in <700ms |
| **Filler Cache** | Pre-generated filler audio at satellite connect → 0ms lookup on use |
| **Sentence Streaming** | Each sentence synthesized and streamed independently as LLM tokens arrive |
| **Auto-Listen** | Response ends with `?` → satellite auto-transitions to LISTENING |
| **Hallucination Filter** | Whisper noise patterns detected and blocked before pipeline |
| **Avatar (C7)** | TTS phoneme output → viseme mapping → lip-sync animation |
| **Personality (personality.md)** | Emotion tone shapes TTS delivery |
| **User Profiles (C6)** | Preferred voice, age-appropriate delivery, parental volume controls |
| **Context Management (C10)** | Long responses: sentence-boundary streaming keeps audio flowing |
| **Spatial (I3)** | Route audio to correct satellite speaker |
| **Night Mode** | Time-of-day + room context → automatic volume/pace adjustment |

---

## Phase C11: Voice & Speech Engine

### C11.1 — TTS Provider Interface
- Abstract `TTSProvider` class: `synthesize()`, `list_voices()`, `supports_emotion()`
- Provider implementations: Orpheus (Ollama), Piper (CPU fallback), Parler, Coqui
- Provider selected/discovered at install (C0)
- Configuration in `cortex.env`

### C11.2 — Orpheus TTS Integration
- Pull `legraphista/Orpheus` Q4 GGUF into Ollama
- Or deploy Orpheus-FastAPI-ollama container with ROCm
- Verify audio generation, streaming, emotion tags
- VRAM management: time-multiplexed with LLM model

### C11.3 — Emotion Composer
- Map VADER sentiment → Orpheus/Parler emotion format
- Paralingual injection (laugh, sigh, whisper) based on context
- Age-appropriate emotion filtering
- Night mode / quiet hours adaptation
- Never repeat same paralingual consecutively

### C11.4 — Voice Registry & Selection
- `tts_voices` table with provider, gender, style, language
- Per-user voice preference (stored in user profile)
- Voice preview/audition via conversation ("try a different voice")
- Seed voices for each installed provider

### C11.5 — Sentence-Boundary Streaming
- Detect sentence boundaries in LLM token stream
- Pipeline: sentence complete → emotion tag → TTS → audio stream
- Overlap: sentence N plays while sentence N+1 generates
- Fast path: Layer 1/2 responses → Piper CPU → <200ms

### C11.6 — Atlas TTS API Endpoint
- `POST /v1/audio/speech` (OpenAI-compatible)
- Extensions: `emotion`, `include_phonemes` for avatar sync
- Wyoming TTS adapter for HA integration
- HA can use Atlas as both conversation agent AND TTS engine

### C11.7 — Avatar Phoneme Bridge
- Extract phoneme timing from Orpheus/Piper output
- Feed to avatar server (C7) for viseme animation
- Synchronized: audio playback + lip movement + emotion expression
