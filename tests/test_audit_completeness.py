"""Audit completeness test — verifies all refactor phases are properly wired.

This test validates that:
1. Dormant systems are wired into the pipeline (not dead code)
2. Deprecation warnings fire on legacy imports
3. New modules exist and have proper structure
4. Ownership headers are present
5. Scheduler has all required services registered

Run with: python -m pytest tests/test_audit_completeness.py -v
"""
from __future__ import annotations

import ast
import importlib
import warnings
from pathlib import Path

import pytest

CORTEX_ROOT = Path(__file__).parent.parent / "cortex"


# ── Phase 1: Speech service exists and consolidates TTS/STT ──────

class TestPhase1Speech:
    def test_speech_module_exists(self):
        assert (CORTEX_ROOT / "speech" / "__init__.py").exists()

    def test_speech_tts_exists(self):
        assert (CORTEX_ROOT / "speech" / "tts.py").exists()

    def test_speech_stt_exists(self):
        assert (CORTEX_ROOT / "speech" / "stt.py").exists()

    def test_speech_voices_exists(self):
        assert (CORTEX_ROOT / "speech" / "voices.py").exists()

    def test_speech_cache_exists(self):
        assert (CORTEX_ROOT / "speech" / "cache.py").exists()

    def test_speech_exports_synthesize(self):
        """cortex.speech must export synthesize_speech."""
        from cortex.speech import synthesize_speech
        assert callable(synthesize_speech)

    def test_speech_exports_transcribe(self):
        """cortex.speech must export transcribe."""
        from cortex.speech import transcribe
        assert callable(transcribe)

    def test_broadcast_uses_speech_not_voice_providers(self):
        """avatar/broadcast.py must NOT import cortex.voice.providers."""
        code = (CORTEX_ROOT / "avatar" / "broadcast.py").read_text()
        assert "cortex.voice.providers" not in code, \
            "broadcast.py still uses old cortex.voice.providers — should use cortex.speech"

    def test_jokes_uses_speech_not_voice_providers(self):
        """content/jokes.py cache_tts must NOT import cortex.voice.providers."""
        code = (CORTEX_ROOT / "content" / "jokes.py").read_text()
        # Check that the cache_tts function doesn't use old providers
        # (the jokes module may still use cortex.voice for other things like resolve_default_voice)
        assert "from cortex.voice.providers import get_tts_provider" not in code, \
            "jokes.py still uses old cortex.voice.providers — should use cortex.speech"


# ── Phase 2: Avatar controller and submodules ─────────────────────

class TestPhase2Avatar:
    def test_controller_exists(self):
        assert (CORTEX_ROOT / "avatar" / "controller.py").exists()

    def test_expressions_exists(self):
        assert (CORTEX_ROOT / "avatar" / "expressions.py").exists()

    def test_visemes_exists(self):
        assert (CORTEX_ROOT / "avatar" / "visemes.py").exists()

    def test_broadcast_exists(self):
        assert (CORTEX_ROOT / "avatar" / "broadcast.py").exists()

    def test_skins_exists(self):
        assert (CORTEX_ROOT / "avatar" / "skins").is_dir()


# ── Phase 3: Pipeline events ──────────────────────────────────────

class TestPhase3Events:
    def test_events_module_exists(self):
        assert (CORTEX_ROOT / "pipeline" / "events.py").exists()

    def test_events_has_typed_events(self):
        from cortex.pipeline.events import TextToken, ExpressionEvent, SpeakingEvent
        assert TextToken is not None
        assert ExpressionEvent is not None
        assert SpeakingEvent is not None

    def test_pipeline_no_avatar_imports(self):
        """Pipeline must NOT import avatar directly."""
        for py_file in (CORTEX_ROOT / "pipeline").rglob("*.py"):
            tree = ast.parse(py_file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "cortex.avatar" in node.module:
                    pytest.fail(f"{py_file.name} imports {node.module} — pipeline must not import avatar")

    def test_pipeline_no_fire_avatar(self):
        """Pipeline must not contain _fire_avatar or _skip_avatar functions."""
        init_code = (CORTEX_ROOT / "pipeline" / "__init__.py").read_text()
        assert "_fire_avatar" not in init_code, "Pipeline still has _fire_avatar functions"
        assert "_skip_avatar_tts" not in init_code, "Pipeline still has _skip_avatar_tts flag"


# ── Phase 4: Orchestrator ─────────────────────────────────────────

class TestPhase4Orchestrator:
    def test_orchestrator_exists(self):
        assert (CORTEX_ROOT / "orchestrator" / "__init__.py").exists()

    def test_voice_exists(self):
        assert (CORTEX_ROOT / "orchestrator" / "voice.py").exists()

    def test_text_exists(self):
        assert (CORTEX_ROOT / "orchestrator" / "text.py").exists()

    def test_filler_exists(self):
        assert (CORTEX_ROOT / "orchestrator" / "filler.py").exists()

    def test_satellite_delegates_to_orchestrator(self):
        """satellite/websocket.py must delegate to orchestrator."""
        code = (CORTEX_ROOT / "satellite" / "websocket.py").read_text()
        assert "process_voice_pipeline" in code, \
            "satellite/websocket.py doesn't delegate to orchestrator"


# ── Phase 5: Memory + dormant system wiring ───────────────────────

class TestPhase5Memory:
    def test_memory_submodules(self):
        for name in ["controller.py", "hot.py", "cold.py", "classification.py", "pii.py"]:
            assert (CORTEX_ROOT / "memory" / name).exists(), f"Missing memory/{name}"

    def test_memory_singleton(self):
        from cortex.memory.controller import get_memory_system, set_memory_system
        assert callable(get_memory_system)
        assert callable(set_memory_system)

    def test_voice_pipeline_calls_recall(self):
        """orchestrator/voice.py must call memory recall before pipeline."""
        code = (CORTEX_ROOT / "orchestrator" / "voice.py").read_text()
        assert "mem.recall" in code, "Memory recall not wired in orchestrator/voice.py"

    def test_voice_pipeline_calls_remember(self):
        """orchestrator/voice.py must call memory remember after pipeline."""
        code = (CORTEX_ROOT / "orchestrator" / "voice.py").read_text()
        assert "mem.remember" in code, "Memory remember not wired in orchestrator/voice.py"

    def test_evolution_personality_wired(self):
        """orchestrator/voice.py must call get_personality_modifiers."""
        code = (CORTEX_ROOT / "orchestrator" / "voice.py").read_text()
        assert "get_personality_modifiers" in code, \
            "Evolution personality modifiers not wired in voice pipeline"

    def test_evolution_record_interaction_wired(self):
        """orchestrator/voice.py must call record_interaction after response."""
        code = (CORTEX_ROOT / "orchestrator" / "voice.py").read_text()
        assert "record_interaction" in code, \
            "Evolution record_interaction not wired in voice pipeline"

    def test_grounding_confidence_wired(self):
        """orchestrator/voice.py must call assess_confidence after response."""
        code = (CORTEX_ROOT / "orchestrator" / "voice.py").read_text()
        assert "assess_confidence" in code, \
            "Grounding assess_confidence not wired in voice pipeline"


# ── Phase 6: Learning, Notifications, SelfMod ────────────────────

class TestPhase6:
    def test_learning_module_exists(self):
        assert (CORTEX_ROOT / "learning" / "__init__.py").exists()

    def test_learning_exports(self):
        from cortex.learning import FallthroughAnalyzer, NightlyEvolution
        assert FallthroughAnalyzer is not None
        assert NightlyEvolution is not None

    def test_notifications_module_exists(self):
        assert (CORTEX_ROOT / "notifications" / "__init__.py").exists()
        assert (CORTEX_ROOT / "notifications" / "channels.py").exists()

    def test_notifications_exports(self):
        from cortex.notifications import send_notification, NotificationChannel, LogChannel
        assert callable(send_notification)

    def test_safety_wired_to_notifications(self):
        """Safety guardrails must send notifications on WARN/BLOCK."""
        code = (CORTEX_ROOT / "safety" / "__init__.py").read_text()
        assert "send_notification" in code, \
            "Safety not wired to notifications"

    def test_selfmod_module_exists(self):
        assert (CORTEX_ROOT / "selfmod" / "__init__.py").exists()
        assert (CORTEX_ROOT / "selfmod" / "zones.py").exists()

    def test_selfmod_zones(self):
        from cortex.selfmod import validate_change, Zone
        # Safety must be frozen
        allowed, _ = validate_change("cortex/safety/jailbreak.py")
        assert not allowed, "safety/ should be FROZEN"
        # Content must be mutable
        allowed, _ = validate_change("cortex/content/jokes.py")
        assert allowed, "content/ should be MUTABLE"
        # selfmod must be frozen
        allowed, _ = validate_change("cortex/selfmod/zones.py")
        assert not allowed, "selfmod/ should be FROZEN"

    def test_learned_patterns_wired_to_layer2(self):
        """Layer 2 must check learned command_patterns before plugin dispatch."""
        code = (CORTEX_ROOT / "pipeline" / "layer2_plugins.py").read_text()
        assert "command_patterns" in code or "_try_learned_patterns" in code, \
            "Learned patterns not wired into Layer 2 dispatch"


# ── Phase 7: Content and Scheduler ────────────────────────────────

class TestPhase7:
    def test_content_module_exists(self):
        assert (CORTEX_ROOT / "content" / "__init__.py").exists()
        assert (CORTEX_ROOT / "content" / "jokes.py").exists()

    def test_scheduler_module_exists(self):
        assert (CORTEX_ROOT / "scheduler" / "__init__.py").exists()

    def test_scheduler_knowledge_sync_registered(self):
        """server.py must register knowledge sync in scheduler."""
        code = (CORTEX_ROOT / "server.py").read_text()
        assert "knowledge" in code.lower() and "register_service" in code, \
            "Knowledge sync not registered in scheduler"

    def test_scheduler_nightly_evolution_registered(self):
        """server.py must register nightly evolution in scheduler."""
        code = (CORTEX_ROOT / "server.py").read_text()
        assert "nightly" in code.lower() and "NightlyEvolution" in code, \
            "Nightly evolution not registered in scheduler"


# ── Phase 8: Admin API Split ──────────────────────────────────────

class TestPhase8Admin:
    def test_admin_package_exists(self):
        assert (CORTEX_ROOT / "admin" / "__init__.py").exists()

    def test_admin_has_helpers(self):
        assert (CORTEX_ROOT / "admin" / "helpers.py").exists()

    def test_admin_sub_routers_exist(self):
        for name in ["auth.py", "dashboard.py", "users.py", "safety.py",
                      "devices.py", "system.py", "satellites.py", "tts.py", "avatar.py"]:
            assert (CORTEX_ROOT / "admin" / name).exists(), f"Missing admin/{name}"


# ── Phase 9: Deprecation and Enforcement ──────────────────────────

class TestPhase9:
    def test_voice_deprecation_warning(self):
        """Importing cortex.voice must raise DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.reload(importlib.import_module("cortex.voice"))
            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)
                            and "cortex.voice" in str(x.message)]
            assert dep_warnings, "cortex.voice does not emit DeprecationWarning"

    def test_jokes_deprecation_warning(self):
        """Importing cortex.jokes must raise DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.reload(importlib.import_module("cortex.jokes"))
            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)
                            and "cortex.jokes" in str(x.message)]
            assert dep_warnings, "cortex.jokes does not emit DeprecationWarning"

    def test_admin_api_deprecation_warning(self):
        """Importing cortex.admin_api must raise DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.reload(importlib.import_module("cortex.admin_api"))
            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)
                            and "cortex.admin_api" in str(x.message)]
            assert dep_warnings, "cortex.admin_api does not emit DeprecationWarning"

    def test_ownership_headers_present(self):
        """All cortex/*/__init__.py must have 'Module ownership:' comment."""
        missing = []
        for init_file in sorted(CORTEX_ROOT.glob("*/__init__.py")):
            module_name = init_file.parent.name
            content = init_file.read_text()
            if "Module ownership:" not in content:
                missing.append(f"cortex/{module_name}/__init__.py")
        if missing:
            pytest.fail(f"Missing 'Module ownership:' header in:\n" + "\n".join(missing))

    def test_bootstrap_prompt_updated(self):
        """LLM_BOOTSTRAP_PROMPT.md must reference new modules."""
        bootstrap = Path(__file__).parent.parent / "LLM_BOOTSTRAP_PROMPT.md"
        content = bootstrap.read_text()
        for module in ["orchestrator/", "speech/", "admin/", "notifications/", "selfmod/"]:
            assert module in content, \
                f"LLM_BOOTSTRAP_PROMPT.md missing reference to {module}"
