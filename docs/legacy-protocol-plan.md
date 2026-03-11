# Legacy Protocol — Implementation Plan

## Problem Statement

Principle XIV of the Core Principles defines the Legacy Protocol: a family succession
system that ensures household continuity when the admin is gone. Atlas must gracefully
transfer stewardship to designated legacy contacts through a compassionate, secure,
tiered handoff process.

This is a FROZEN-zone module — safety-critical, human-review required for changes.

## Design Decisions (confirmed with user)

- **Notification**: Hybrid — local satellite alerts always + optional outbound (SMTP, Pushover, Ntfy)
- **Access model**: Atlas-gated — verification unlocks access; vault encrypted at rest
- **Verification**: All methods available, admin picks which to require (security Q's, passphrase, voice ID, network presence, multi-factor)
- **Activation**: All methods available (dead-man's switch, manual, third-party trigger, scheduled, gradual escalation)
- **Dead-man's switch**: Admin configurable, 30-day default, with escalation sequence (warning → grace → activate)
- **Storage**: Separate from main DB — `data/legacy/vault.db` (structured) + `data/legacy/files/` (encrypted media)
- **Experience**: Tiered — essentials first (alarm codes, WiFi, thermostat, personal message), then guided walkthrough over days/weeks. Always available to help after activation.

## Architecture

```
cortex/legacy/
├── __init__.py          # Public API, FROZEN zone registration
├── config.py            # Configuration: contacts, verification methods, activation rules
├── vault.py             # Encrypted vault: Fernet AES, separate SQLite + filesystem
├── deadman.py           # Dead-man's switch: heartbeat tracking, countdown, escalation
├── activation.py        # State machine: dormant → warning → grace → verify → active → complete
├── verification.py      # Identity verification: security Q's, passphrase, voice, presence, MFA
├── transition.py        # Tiered handoff experience: essentials → guided → ongoing access
├── notifications.py     # Legacy-specific notification dispatch (local + outbound channels)
├── voice_cache.py       # Pre-cached compassionate voice phrases (encrypted at rest)
└── fast_paths.py        # Dormant Layer 1/2 patterns activated during legacy mode

cortex/admin/legacy.py   # Admin API routes for Legacy Protocol configuration

data/legacy/
├── vault.db             # Encrypted SQLite — contacts, credentials, configs, state
├── vault.key            # Fernet key material (salt + verification data)
└── files/               # Encrypted media — audio messages, documents, photos
    ├── <uuid>.enc       # Each file individually encrypted
    └── manifest.json.enc # File index (encrypted)
```

### State Machine

```
DORMANT ──→ WARNING ──→ GRACE ──→ PENDING_VERIFICATION ──→ ACTIVE ──→ COMPLETE
   ↑           │          │              │                     │
   └───────────┴──────────┘              │                     │
        (admin cancels)                  │                     ↓
                                         │              ACTIVE_ONGOING
                                         │           (always available)
                                         │
                                    MANUAL_ACTIVATE ──→ PENDING_VERIFICATION
                                    THIRD_PARTY_TRIGGER ──→ PENDING_VERIFICATION
                                    SCHEDULED_ACTIVATE ──→ PENDING_VERIFICATION
```

**States:**
- `DORMANT` — Protocol configured but inactive. Heartbeat tracking active.
- `WARNING` — Dead-man threshold crossed. Atlas attempts to reach admin (notifications, satellite announcements). Admin can cancel.
- `GRACE` — Warning period expired without admin response. Final countdown before activation. Admin can still cancel.
- `PENDING_VERIFICATION` — Activated. Awaiting legacy contact identity verification.
- `ACTIVE` — Verified. Essentials delivered. Guided walkthrough available.
- `ACTIVE_ONGOING` — Post-walkthrough. Legacy contact has full access. Atlas remains available as guide.
- `COMPLETE` — Optional: legacy contact marks transition as done (for audit trail).

### Activation Phrases

The activation passphrase is fully admin-configurable — no baked-in default. During
setup, the Admin UI presents suggested templates and the admin writes their own.
Longer phrases are preferred to minimize false-positive risk.

**Suggested templates (shown in Admin UI setup wizard):**
- `"{Wake}, I need to activate the Lazarus Pit for {Name}"` — clear intent, codename, identifies who
- `"{Wake}, activate the Lazarus Pit for {Name}"` — shorter variant
- `"{Wake}, it's time for the Lazarus Pit"` — softer, no one has to say "gone"
- `"{Wake}, {Name} needs the Lazarus Pit"` — feels like doing something *for* them
- `"{Wake}, open the Lazarus Pit for {Name}"` — echoes "open the Batcave"
- `"{Wake}, initiate Lazarus Protocol for {Name}"` — military/formal tone
- `"{Wake}, I need to activate {Name}'s legacy"` — no codename, still clear
- `"{Wake}, begin the legacy protocol for {Name}"` — clinical, low emotion
- Or write your own — "Use something meaningful to your family."

**Protocol name extraction:** Atlas parses the admin's activation phrase during setup
to extract the codename (e.g., "Lazarus Pit" from "activate the Lazarus Pit for Nick").
If it can't determine a codename, it defaults to "Legacy Protocol". This extracted name
is used in all voice responses (e.g., "I am activating the Lazarus Pit" vs. "I am
activating the Legacy Protocol").

**Matching:** The activation phrase triggers `PENDING_VERIFICATION` — it opens the
front door, not the vault. Atlas then walks the contact through identity verification.

### Pre-Cached Legacy Voice Modules

Compassionate audio phrases pre-synthesized at configuration time and encrypted in the
vault. When the Legacy Protocol activates, they're decrypted into memory — giving Atlas
an instant, warm voice with zero TTS latency for the first interaction (when TTS services
may not yet be warmed up, or the person is grieving and shouldn't wait).

#### Voice Selection

The legacy voice is **separate from the system default voice**. It is auto-selected
during protocol setup based on:

1. **Prefer emotion-capable voices** — Orpheus voices support dynamic emotion tags
   (`gentle`, `warm`, `sad`) which are critical for compassionate delivery.
2. **Prefer soft/gentle style** — `orpheus_mia` (gentle, female) is ideal. Fallback
   chain: `orpheus_tara` (warm) → `bf_emma` (warm, Kokoro) → `af_bella` (warm, Kokoro).
3. **Match user's language** — query `tts_voices` table for matching `language` field.
4. **Admin override** — admin can pick any voice during setup if they prefer a specific one.

The legacy voice is stored in vault config (`legacy_tts_voice`) and used exclusively
for legacy mode interactions. The system default voice is never used during legacy mode.

If Orpheus is available, phrases are synthesized with emotion tags:
- Acknowledgment phrases: `<emotion: gentle, sad>`
- Comfort phrases: `<emotion: warm, gentle>`
- Orientation/instruction phrases: `<emotion: warm, calm>`
- Ongoing/closing phrases: `<emotion: warm>`

#### Phrase Categories

Phrases are templated with `{legacy_name}` (contact's name), `{admin_name}`, and
`{protocol_name}` (extracted codename or "Legacy Protocol"). All are pre-synthesized
at setup with the chosen legacy voice.

**Stage 1 — Activation Acknowledgment** (played immediately when passphrase matches):
- `activation_ack` — "I'm so sorry, {legacy_name}. I am activating the {protocol_name}. This will take a moment — I'll let you know when everything is ready."
- `activation_ack_alt` — "{legacy_name}, I hear you. I'm beginning the {protocol_name} now. Please give me just a moment to prepare."

**Stage 2 — Processing / Verification** (played during verification steps):
- `verification_intro` — "Before I can give you access, I need to confirm your identity. {admin_name} set this up to protect you and the family. It won't take long."
- `verification_patience` — "Take your time. There's no rush with any of this."
- `verification_success` — "Thank you, {legacy_name}. I've confirmed who you are. I'm ready to help."
- `verification_retry` — "That didn't quite match. Let's try again — no pressure."

**Stage 3 — Ready / Essentials** (played when vault access is granted):
- `ready` — "Everything is ready, {legacy_name}. I'm going to start with the essentials — things you might need right away."
- `essentials_intro` — "Here's what {admin_name} wanted you to have first."
- `personal_message_intro` — "{admin_name} left a personal message for you. Would you like to hear it now, or would you prefer to wait?"

**Stage 4 — Guided Walkthrough** (played when contact is ready for full tour):
- `walkthrough_intro` — "Whenever you're ready, I can walk you through the house — room by room, system by system. There's no timeline on this."
- `walkthrough_room` — "Let me show you how {admin_name} had things set up in the {room}."

**Stage 5 — Ongoing Comfort** (available anytime after activation):
- `comfort` — "Take your time with all of this. There's no rush. I'll be here whenever you need me."
- `ongoing` — "I'm always here, {legacy_name}. Anytime you need help with anything in the house, just ask."
- `closing` — "You're doing great. This house is in good hands."
- `return_greeting` — "Welcome back, {legacy_name}. What can I help you with today?"

#### Storage & Generation

**Voice selection:** Auto-selected at setup (emotion-capable preferred), stored in vault
config. Admin can override.

**Generation trigger:** Auto-generated when admin completes Legacy Protocol setup.
Regenerated if admin changes the legacy voice, contact name, or protocol name.
Admin UI has a "Preview & Regenerate" button.

**Storage:** Same pattern as filler cache — JSON with base64 PCM audio, encrypted
with vault master key, stored in `data/legacy/files/voice_cache.enc`.
Decrypted to memory on protocol activation.

**Format:** 24kHz 16-bit mono PCM (matches filler cache). ~86KB per phrase.
~16 phrases × 86KB ≈ 1.4MB encrypted on disk.

### Legacy Avatar Mode

When the Legacy Protocol activates, the avatar switches to a **compassionate mode** —
not sad, not grinning, but calm and present. This affects both the expression and
optionally the skin.

#### Compassionate Expression

A new `compassionate` expression preset added to `cortex/avatar/expressions.py`:
- Slight eyebrow raise (attentive, not alarmed): `eyebrow_raise: 0.15`
- Minimal eye squint (soft, warm gaze): `eye_squint: 0.1`
- Mouth neutral-to-slight-warmth (not smiling, not frowning): `mouth_smile: 0.05`
- Very slight head tilt (engaged, listening): `head_tilt: 0.1`
- Slow blink rate (calm, unhurried): `blink_rate: 0.2`

This sits between `neutral` (all zeros, feels flat/robotic) and `happy` (too cheerful).
It communicates: "I'm here. I'm calm. Take your time."

#### Expression Behavior by Stage

| Protocol State | Avatar Expression | Rationale |
|----------------|-------------------|-----------|
| `PENDING_VERIFICATION` | `compassionate` | Warm, calm presence during a hard moment |
| `ACTIVE` (essentials) | `compassionate` | Steady, not overly emotional |
| `ACTIVE` (personal message) | `sad` at 0.4 intensity | Gentle empathy, not dramatic |
| `ACTIVE` (walkthrough) | `compassionate` | Patient, guiding |
| `ACTIVE_ONGOING` | `compassionate` → normal | Gradually returns to standard expression mapping as interactions normalize |
| Comfort phrases | `compassionate` | Always compassionate for comfort |

#### Sentiment Override

During legacy mode, the standard sentiment-to-expression mapping is bypassed.
Instead, `cortex/avatar/controller.set_expression()` checks protocol state and
defaults to `compassionate` unless the interaction specifically calls for another
expression (e.g., a joke request during ongoing mode could still use `silly`).

As the legacy contact settles in over days/weeks, Atlas gradually relaxes back to
normal expression mapping — the transition from `ACTIVE` to `ACTIVE_ONGOING` widens
the expression range incrementally.

#### Optional: Legacy Skin

Admin can optionally designate a specific avatar skin for legacy mode (e.g., a softer
color palette, subdued animation). This is stored in vault config (`legacy_skin_id`).
If not configured, the current default skin is used with the compassionate expression.

### Legacy Fast-Path Patterns

Dormant Layer 1 / Layer 2 patterns that "wake up" when protocol state is ACTIVE.
Provides instant answers for common legacy-contact questions without LLM round-trip.

**Layer 1 fast-paths (instant, no LLM):**
- "What's the WiFi password?" → vault lookup → instant response
- "What's the alarm code?" → vault lookup → instant response
- "How do I lock/unlock the door?" → vault lookup → step-by-step
- "How do I control the lights?" → vault lookup → room-by-room guide
- "How do I adjust the thermostat?" → vault lookup → instructions
- "Is there a personal message?" → trigger personal message playback
- "What did [admin] want me to know?" → trigger essentials tier

**Layer 2 learned patterns (seeded, not LLM-learned):**
- "Show me credentials for {system}" → vault category lookup
- "Walk me through {room}" → guided room walkthrough
- "What services are running?" → system status summary
- "How do I get help?" → explain what Atlas can do in legacy mode

**Activation:** These patterns check `protocol_state` before matching. When state is
DORMANT, they're invisible. When ACTIVE/ACTIVE_ONGOING, they intercept before standard
pipeline processing.

**Priority:** Legacy fast-paths run BEFORE standard Layer 1/2 patterns (first match wins).

### Vault Encryption Design

```
Installation Secret (CORTEX_LEGACY_SECRET env var, or derived from CORTEX_JWT_SECRET)
        │
        ▼
    PBKDF2-HMAC-SHA256 (100k iterations, random salt)
        │
        ▼
    Vault Master Key (Fernet / AES-128-CBC + HMAC)
        │
        ├──→ Encrypts sensitive columns in vault.db (column-level Fernet)
        ├──→ Encrypts each file in data/legacy/files/
        └──→ Encrypts manifest.json
```

- **At rest**: All legacy data encrypted. Stealing the DB file yields nothing.
- **At runtime**: Atlas decrypts in-memory after verification gate passes.
- **Key storage**: Salt stored in `data/legacy/vault.key`. Key derived at runtime.
- **No external dependency**: Uses Python `cryptography` library (Fernet).

### Database Schema (vault.db — separate from main cortex.db)

```sql
-- Legacy contacts designated by admin
CREATE TABLE legacy_contacts (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    relationship    TEXT DEFAULT '',
    priority        INTEGER DEFAULT 1,
    notification_method TEXT DEFAULT 'local',
    notification_target TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Verification methods per contact
CREATE TABLE verification_methods (
    id              TEXT PRIMARY KEY,
    contact_id      TEXT NOT NULL REFERENCES legacy_contacts(id) ON DELETE CASCADE,
    method_type     TEXT NOT NULL,
    method_order    INTEGER DEFAULT 1,
    is_required     INTEGER DEFAULT 1,
    config_data     TEXT DEFAULT '{}',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Answers/secrets for verification (encrypted at rest)
CREATE TABLE verification_secrets (
    id              TEXT PRIMARY KEY,
    method_id       TEXT NOT NULL REFERENCES verification_methods(id) ON DELETE CASCADE,
    secret_data     TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Protocol configuration
CREATE TABLE protocol_config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Protocol state machine (singleton)
CREATE TABLE protocol_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    state           TEXT NOT NULL DEFAULT 'DORMANT',
    state_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    activated_by    TEXT DEFAULT '',
    verified_contact_id TEXT DEFAULT '',
    warning_sent_at TIMESTAMP,
    grace_started_at TIMESTAMP,
    activation_at   TIMESTAMP,
    notes           TEXT DEFAULT ''
);

-- Admin heartbeat tracking
CREATE TABLE admin_heartbeats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source          TEXT DEFAULT 'login'
);

-- Vault: stored credentials and configs
CREATE TABLE vault_entries (
    id              TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    is_essential    INTEGER DEFAULT 0,
    sort_order      INTEGER DEFAULT 100,
    tags            TEXT DEFAULT '[]',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Vault: media files reference
CREATE TABLE vault_files (
    id              TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    file_type       TEXT NOT NULL,
    mime_type       TEXT DEFAULT '',
    size_bytes      INTEGER DEFAULT 0,
    encryption_iv   TEXT NOT NULL,
    description     TEXT DEFAULT '',
    is_personal_message INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Audit trail (append-only)
CREATE TABLE legacy_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,
    severity        TEXT DEFAULT 'info',
    details         TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Integration Points

| System | Integration |
|--------|-------------|
| **Auth** (`cortex/auth.py`) | Record heartbeat on every `authenticate()` call |
| **Speaker ID** (`cortex/voice/identity.py`) | Enroll legacy contact voice; verify during activation |
| **Notifications** (`cortex/notifications/`) | Outbound channels (SMTP, Pushover, Ntfy) + local alerts |
| **Scheduler** (`cortex/scheduler/`) | Dead-man's switch monitor as background service |
| **Satellites** | Local announcements during WARNING/GRACE states |
| **Admin UI** (`admin/`) | Legacy Protocol configuration section |
| **Selfmod zones** (`cortex/selfmod/`) | Register `legacy/*` as FROZEN zone |
| **Integrity** (`cortex/integrity/`) | Add vault.db to integrity monitoring |
| **Pipeline** | Legacy-guide persona injected into system prompt when ACTIVE |

---

## Phases

### Phase 1: Foundation & Vault
- [ ] Create `cortex/legacy/` module structure with `__init__.py`
- [ ] Implement `vault.py` — Fernet encryption, key derivation, vault DB init
- [ ] Create vault.db schema (all tables above)
- [ ] Implement `config.py` — CRUD for contacts, verification methods, protocol settings
- [ ] Register `legacy/*` as FROZEN in selfmod zones
- [ ] Add `notification_log` table to main db.py (fixes existing bug)
- [ ] Tests: vault encryption round-trip, schema creation, config CRUD

### Phase 2: Dead-Man's Switch & Activation
- [ ] Implement `deadman.py` — heartbeat recording, threshold checking, countdown
- [ ] Wire heartbeat into `cortex/auth.py` authenticate() — record on every admin login
- [ ] Implement `activation.py` — full state machine with transitions and guards
- [ ] Support all activation methods: deadman, manual, third-party trigger, scheduled
- [ ] Implement gradual escalation: dormant → warning → grace → activate
- [ ] Register dead-man monitor as background service via scheduler
- [ ] Tests: state transitions, heartbeat tracking, escalation timing, cancellation

### Phase 3: Identity Verification
- [ ] Implement `verification.py` — verify against configured methods
- [ ] Security questions: compare answer hashes (bcrypt, case-insensitive normalize)
- [ ] Passphrase: compare hash (bcrypt)
- [ ] Voice recognition: integrate with SpeakerIdentifier.identify()
- [ ] Network presence: check if request comes from local network
- [ ] Multi-factor: chain required methods, all must pass
- [ ] Admin setup: enroll legacy contact voice samples via existing speaker ID
- [ ] Tests: each verification method, MFA chain, failure scenarios

### Phase 4: Transition Experience, Voice Cache & Avatar
- [ ] Implement `transition.py` — tiered access controller
- [ ] Tier 1 (Essentials): auto-present on first verified access
- [ ] Tier 2 (Guided walkthrough): room-by-room smart home tour, credential details
- [ ] Ongoing access: legacy contact can ask about any system anytime
- [ ] Personal message playback: audio and/or text from admin
- [ ] Implement `voice_cache.py` — pre-cached compassionate voice phrases
  - Pre-synthesize ~16 phrases across 5 stages (ack, verification, ready, walkthrough, comfort)
  - Encrypt and store in `data/legacy/files/voice_cache.enc`
  - Decrypt into memory on protocol activation (zero TTS latency for first interaction)
  - Auto-generated at setup, regenerated on voice/name/protocol change
- [ ] Implement `fast_paths.py` — dormant Layer 1/2 patterns for legacy mode
  - Layer 1: "WiFi password?", "alarm code?", "thermostat?", "personal message?" → vault instant lookup
  - Layer 2: "credentials for {system}", "walk me through {room}", "what services are running?"
  - Check protocol_state before matching — invisible when DORMANT, active when ACTIVE
  - Priority: run BEFORE standard pipeline patterns (first match wins)
- [ ] Wire fast_paths into pipeline layer1 and layer2 as pre-check
- [ ] Add `compassionate` expression preset to `cortex/avatar/expressions.py`
  - Calm, present, warm but not smiling (eyebrow_raise: 0.15, mouth_smile: 0.05, slow blink)
  - Sits between `neutral` (robotic) and `happy` (too cheerful)
- [ ] Legacy avatar mode: bypass standard sentiment→expression mapping during legacy mode
  - Default to `compassionate` expression during PENDING_VERIFICATION and ACTIVE states
  - Personal message playback uses `sad` at 0.4 intensity (gentle empathy)
  - Gradual return to normal expression mapping as contact settles in (ACTIVE_ONGOING)
  - Optional legacy skin support (admin-configurable, stored in vault config)
- [ ] Pipeline integration: inject legacy-guide persona into system prompt when ACTIVE
- [ ] Tests: tier progression, voice cache encrypt/decrypt, fast-path matching, compassionate expression, avatar mode switching

### Phase 5: Notifications & Outbound Channels
- [ ] Implement `notifications.py` — legacy-specific notification dispatch
- [ ] Create outbound channel implementations: EmailChannel, PushoverChannel, NtfyChannel
- [ ] Register channels based on contact notification_method configs
- [ ] Local satellite announcements during WARNING/GRACE states
- [ ] Fire notifications on every state transition
- [ ] Tests: channel dispatch, fallback on failure, notification content

### Phase 6: Admin API & Configuration UI
- [ ] Create `cortex/admin/legacy.py` — full REST API
- [ ] Endpoints: contacts CRUD, verification setup, vault entries, file upload, protocol config
- [ ] Endpoints: manual activation, cancellation, state view, audit log
- [ ] Endpoints: test notification channels, test verification (dry run)
- [ ] Admin UI: Legacy Protocol section (Vue 3 components)
- [ ] Admin UI: contact management, verification builder, vault editor, status dashboard
- [ ] Tests: API endpoints, auth protection, validation

### Phase 7: Security Hardening & Integrity
- [ ] Add vault.db to integrity monitoring checksums
- [ ] Rate-limit verification attempts (lockout after N failures)
- [ ] Audit trail: every operation logged, append-only, tamper-evident
- [ ] Legacy data excluded from normal backup/export (separate backup path)
- [ ] Legal isolation: legacy queries never touch main cortex.db
- [ ] Security review: encryption key lifecycle, memory handling
- [ ] Tests: rate limiting, audit completeness, isolation verification

### Phase 8: Integration Testing & Documentation
- [ ] End-to-end test: full lifecycle from config → deadman → warning → verify → access
- [ ] Edge cases: multiple contacts, failed verification, cancelled activation, re-activation
- [ ] Documentation: admin guide for Legacy Protocol setup
- [ ] Documentation: architecture doc in docs/legacy-protocol.md
- [ ] Update LLM_BOOTSTRAP_PROMPT.md with legacy module
