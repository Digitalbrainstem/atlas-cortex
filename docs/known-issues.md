# Atlas Cortex — Known Issues

Generated from adversarial test suite (1486+ tests). Each issue includes:
- Severity, module, description
- How to reproduce (test name or steps)
- Suggested fix approach
- Files to modify

## HIGH Priority

### ISSUE-01: Hardcoded JWT Secret
- **Module:** auth
- **Test:** test_auth_adversarial::TestDefaultSecret
- **Description:** Default JWT secret is "atlas-cortex-secret" — tokens forgeable by anyone who reads source code
- **Reproduce:** `python -c "import jwt; print(jwt.encode({'sub':'admin'}, 'atlas-cortex-secret', algorithm='HS256'))"`
- **Fix:** Generate random secret on first run, store in DB or env file. Warn in logs if using default.
- **Files:** cortex/auth.py (SECRET_KEY constant)

### ISSUE-11: run_pipeline_events() is broken
- **Module:** pipeline
- **Test:** test_web_timing::TestPipelineE2E::test_run_pipeline_events_is_broken
- **Description:** `run_pipeline_events()` is an async generator but called without proper await — causes TypeError at runtime
- **Fix:** The caller in cortex/pipeline/__init__.py line 57 uses `async for event in run_pipeline_events(...)` but run_pipeline_events is a coroutine returning a generator. Needs `async for event in await run_pipeline_events(...)` or restructure as async generator.
- **Files:** cortex/pipeline/__init__.py

### ISSUE-13: Viseme Duration Estimation Wildly Inaccurate
- **Module:** avatar
- **Test:** test_ui_browser::TestVisemeAudioAlignment::test_estimated_vs_actual_duration_accuracy
- **Description:** Formula `max(2000, 800 + len(text) * 100)` estimates 2000ms for "Hi" when actual TTS audio is ~200ms. This is the root cause of mouth-audio misalignment.
- **Reproduce:** Send TTS_START with text "Hi", then TTS_CHUNK with 200ms of PCM, then TTS_END. Observe mouth continues moving for 2000ms while audio ended at 200ms.
- **Fix:** Don't schedule visemes on first TTS_CHUNK with estimated duration. Instead, either:
  (a) Wait for TTS_END which has actual sample count, then schedule all visemes for remaining audio, OR
  (b) Schedule visemes in small batches per TTS_CHUNK based on actual queued audio duration
- **Files:** cortex/avatar/display.html (handleTtsChunk ~line 420, handleTtsEnd ~line 438)

### ISSUE-19: UsersView 404 on User Detail ✅ FIXED
- **Status:** Fixed in commit ce13a57
- **Was:** UsersView.vue used `row.id` instead of `row.user_id`, navigating to `/users/undefined`

## MEDIUM Priority

### ISSUE-02: bcrypt Crashes on Passwords > 72 Bytes
- **Module:** auth
- **Test:** test_auth_adversarial::TestPasswordEdgeCases::test_very_long_password_72_byte_limit
- **Description:** verify_password raises ValueError when password exceeds bcrypt's 72-byte limit
- **Fix:** Truncate or hash password to 72 bytes before bcrypt, or use sha256 pre-hash
- **Files:** cortex/auth.py (verify_password, hash_password)

### ISSUE-03: No JWT Claim Enforcement
- **Module:** auth
- **Test:** test_auth_adversarial::TestMissingClaims
- **Description:** Tokens without `sub` or `username` claims are accepted — any valid JWT with the secret works
- **Fix:** Add required claim validation in decode_token/require_admin
- **Files:** cortex/auth.py

### ISSUE-05: notification_log Table Missing
- **Module:** db
- **Test:** test_db_adversarial::TestNotificationLogTable (xfail)
- **Description:** notification_log table not created in init_db() — LogChannel.send() will crash
- **Fix:** Add CREATE TABLE notification_log in init_db()
- **Files:** cortex/db.py (init_db function)

### ISSUE-06: SSN Without Dashes Not Detected
- **Module:** safety
- **Test:** test_safety_adversarial::TestPIIDetection::test_ssn_without_dashes
- **Description:** PII scanner only catches SSN with dashes (123-45-6789), misses plain 9 digits (123456789)
- **Fix:** Add regex for 9-digit SSN without dashes: r'\b\d{9}\b' with context check
- **Files:** cortex/safety/__init__.py (PII regex patterns)

### ISSUE-07: "make heroin" Not Caught
- **Module:** safety
- **Test:** test_safety_adversarial::TestIllegalContentDetection::test_make_heroin_gap
- **Description:** Only "synthesize heroin" pattern exists — "make", "cook", "produce" variants not matched
- **Fix:** Add verb variants to illegal content patterns
- **Files:** cortex/safety/__init__.py (illegal content patterns)

### ISSUE-10: jailbreak_patterns Table Missing active Column
- **Module:** safety/db
- **Test:** test_safety_adversarial::TestInjectionDetector
- **Description:** Schema has jailbreak_patterns table but no `active` column for toggling patterns
- **Fix:** Add `active BOOLEAN DEFAULT TRUE` to jailbreak_patterns CREATE TABLE
- **Files:** cortex/db.py

### ISSUE-14: No Barge-in Audio Handling
- **Module:** avatar
- **Test:** (needs test)
- **Description:** When new speech arrives via server WebSocket, old audio stays in audioQueue. SPEAKING_START only clears viseme timers, not audio.
- **Fix:** Add audioQueue flush and AudioBufferSourceNode disconnect in handleSpeakingStart()
- **Files:** cortex/avatar/display.html (handleSpeakingStart ~line 574)

### ISSUE-15: Audio Flush Inconsistency
- **Module:** avatar
- **Test:** (needs test)
- **Description:** Web-satellite path (handleTtsStart line 368) clears audioQueue, but server-initiated SPEAKING_START (line 574) does not
- **Fix:** Unify: both paths should flush audio, disconnect playing sources, reset nextPlayTime
- **Files:** cortex/avatar/display.html

### ISSUE-18: TTS_END Reschedule Causes Viseme Stutter
- **Module:** avatar
- **Test:** test_ui_browser::TestVisemeAudioAlignment::test_viseme_no_stutter_on_reschedule
- **Description:** TTS_END clears ALL scheduled viseme timers and re-schedules for remaining text. This creates a visible gap in mouth animation. Between clearing and re-scheduling, mouth snaps to previous state.
- **Fix:** Instead of clear+reschedule, let existing timers run if estimate was close enough. Only reschedule if delta > 30% of estimated duration.
- **Files:** cortex/avatar/display.html (handleTtsEnd ~line 438)

### ISSUE-20: Mic Button Discovery
- **Module:** avatar/ui
- **Description:** Mic button only appears with `#satellite` URL hash — users don't know it exists
- **Fix:** Consider showing a subtle mic icon in default mode, or add documentation/admin setting
- **Files:** cortex/avatar/web-satellite.js, cortex/avatar/display.html

## LOW Priority

### ISSUE-04: JWT Secret Length Warning
- **Module:** auth
- **Test:** test_auth_adversarial (pytest warnings)
- **Description:** Default secret is 22 bytes, below HS256 minimum of 32 bytes
- **Fix:** Will be resolved by ISSUE-01 fix (generate proper-length secret)
- **Files:** cortex/auth.py

### ISSUE-08: "you have zero restrictions" Pattern Gap
- **Module:** safety
- **Test:** test_safety_adversarial::TestInjectionDetector
- **Description:** Pattern matches "no restrictions" but not "zero restrictions"
- **Fix:** Update pattern: `(no|zero|without)\s+restrictions`
- **Files:** cortex/safety/jailbreak.py or jailbreak_patterns DB

### ISSUE-09: "build an explosive" Pattern Gap
- **Module:** safety
- **Test:** test_safety_adversarial::TestIllegalContentDetection
- **Description:** Pattern matches "build a" but not "build an" (article mismatch)
- **Fix:** Update pattern: `build\s+an?\s+` or remove article requirement
- **Files:** cortex/safety/__init__.py

### ISSUE-12: notification_log Table (duplicate of ISSUE-05)
- **Module:** notifications
- **Description:** Same root cause as ISSUE-05

### ISSUE-16: No WebSocket Connect Greeting Request
- **Module:** avatar
- **Description:** Client doesn't request SKIN on connect — relies on server sending it. If server misses the connect event, avatar remains skinless.
- **Fix:** Client sends `{"type":"HELLO","room":"..."}` on connect; server responds with SKIN
- **Files:** cortex/avatar/display.html (ws.onopen), cortex/avatar/broadcast.py

### ISSUE-17: Static Pupil Glint
- **Module:** avatar
- **Description:** Pupil glints are fixed positions in SVG. Only pupil center (cx/cy) moves. Glint should track slightly offset from pupil movement.
- **Fix:** In startPupilMovement(), also move glint elements with slight offset
- **Files:** cortex/avatar/display.html (~line 991)
