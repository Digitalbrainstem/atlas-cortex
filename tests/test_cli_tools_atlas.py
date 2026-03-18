"""Tests for Atlas CLI integration tools and multi-modal tools."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.cli.tools import ToolResult

# ---------------------------------------------------------------------------
# Atlas integration tools
# ---------------------------------------------------------------------------
from cortex.cli.tools.atlas import (
    HAControlTool,
    MemoryStoreTool,
    MemoryTool,
    NotifyTool,
    ReminderTool,
    RoutineTool,
    TimerTool,
)
from cortex.cli.tools.multimodal import (
    EmbedTextTool,
    ImageGenerateTool,
    OCRTool,
    SpeechToTextTool,
    TextToSpeechTool,
    VisionAnalyzeTool,
)


# ===================================================================
# Helpers
# ===================================================================

def _schema_ok(tool: Any) -> None:
    """Assert that to_function_schema() produces a valid OpenAI-compatible schema."""
    schema = tool.to_function_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == tool.tool_id
    assert isinstance(fn["description"], str) and fn["description"]
    assert "properties" in fn["parameters"]


# ===================================================================
# Schema validation for all tools
# ===================================================================


class TestAllToolSchemas:
    """Every tool must produce a valid OpenAI function-calling schema."""

    @pytest.mark.parametrize(
        "tool_cls",
        [
            HAControlTool,
            TimerTool,
            ReminderTool,
            RoutineTool,
            NotifyTool,
            MemoryTool,
            MemoryStoreTool,
            VisionAnalyzeTool,
            ImageGenerateTool,
            EmbedTextTool,
            OCRTool,
            SpeechToTextTool,
            TextToSpeechTool,
        ],
    )
    def test_schema(self, tool_cls: type) -> None:
        _schema_ok(tool_cls())


# ===================================================================
# TimerTool
# ===================================================================


class TestTimerTool:
    async def test_set_timer(self) -> None:
        mock_engine = MagicMock()
        mock_engine.start_timer.return_value = 42

        tool = TimerTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(TimerEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "set", "duration_seconds": 1500, "label": "pomodoro"})
        assert result.success
        assert "42" in result.output
        assert "pomodoro" in result.output
        assert result.metadata["timer_id"] == 42
        mock_engine.start_timer.assert_called_once()

    async def test_list_timers_empty(self) -> None:
        mock_engine = MagicMock()
        mock_engine.list_timers.return_value = []

        tool = TimerTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(TimerEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "list"})
        assert result.success
        assert "No active" in result.output

    async def test_list_timers_with_entries(self) -> None:
        mock_engine = MagicMock()
        mock_engine.list_timers.return_value = [
            {"id": 1, "label": "focus", "remaining": 300},
            {"id": 2, "label": "break", "remaining": 60},
        ]

        tool = TimerTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(TimerEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "list"})
        assert result.success
        assert "#1" in result.output
        assert "focus" in result.output
        assert result.metadata["count"] == 2

    async def test_cancel_timer(self) -> None:
        mock_engine = MagicMock()
        mock_engine.cancel_timer.return_value = True

        tool = TimerTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(TimerEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "cancel", "timer_id": 7})
        assert result.success
        assert "cancelled" in result.output

    async def test_set_requires_duration(self) -> None:
        mock_engine = MagicMock()
        tool = TimerTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(TimerEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "set"})
        assert not result.success
        assert "duration_seconds" in result.error

    async def test_cancel_requires_id(self) -> None:
        mock_engine = MagicMock()
        tool = TimerTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(TimerEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "cancel"})
        assert not result.success
        assert "timer_id" in result.error


# ===================================================================
# ReminderTool
# ===================================================================


class TestReminderTool:
    async def test_set_reminder(self) -> None:
        mock_engine = MagicMock()
        mock_engine.create_reminder.return_value = 10

        tool = ReminderTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(ReminderEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({
                "action": "set",
                "message": "Buy groceries",
                "trigger_at": "2025-12-01T14:00:00",
            })
        assert result.success
        assert "10" in result.output
        assert "Buy groceries" in result.output
        mock_engine.create_reminder.assert_called_once()

    async def test_list_reminders_empty(self) -> None:
        mock_engine = MagicMock()
        mock_engine.list_reminders.return_value = []

        tool = ReminderTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(ReminderEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "list"})
        assert result.success
        assert "No active" in result.output

    async def test_list_reminders_with_entries(self) -> None:
        mock_engine = MagicMock()
        mock_engine.list_reminders.return_value = [
            {"id": 3, "message": "Call dentist", "trigger_at": "2025-12-01T10:00:00"},
        ]

        tool = ReminderTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(ReminderEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "list"})
        assert result.success
        assert "Call dentist" in result.output

    async def test_delete_reminder(self) -> None:
        mock_engine = MagicMock()
        mock_engine.delete_reminder.return_value = True

        tool = ReminderTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(ReminderEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "delete", "reminder_id": 3})
        assert result.success
        assert "deleted" in result.output

    async def test_set_requires_message(self) -> None:
        mock_engine = MagicMock()
        tool = ReminderTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(ReminderEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "set"})
        assert not result.success
        assert "message" in result.error

    async def test_invalid_datetime(self) -> None:
        mock_engine = MagicMock()
        tool = ReminderTool()
        with (
            patch.dict("sys.modules", {"cortex.scheduling": MagicMock(ReminderEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({
                "action": "set",
                "message": "test",
                "trigger_at": "not-a-date",
            })
        assert not result.success
        assert "Invalid datetime" in result.error


# ===================================================================
# RoutineTool
# ===================================================================


class TestRoutineTool:
    async def test_list_routines(self) -> None:
        mock_engine = MagicMock()
        mock_engine.list_routines = AsyncMock(return_value=[
            {"id": 1, "name": "Morning", "enabled": True},
        ])

        tool = RoutineTool()
        with (
            patch.dict("sys.modules", {"cortex.routines": MagicMock(), "cortex.routines.engine": MagicMock(RoutineEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "list"})
        assert result.success
        assert "Morning" in result.output

    async def test_run_routine(self) -> None:
        mock_engine = MagicMock()
        mock_engine.run_routine = AsyncMock(return_value=99)

        tool = RoutineTool()
        with (
            patch.dict("sys.modules", {"cortex.routines": MagicMock(), "cortex.routines.engine": MagicMock(RoutineEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "run", "routine_id": 1})
        assert result.success
        assert "run_id=99" in result.output

    async def test_create_routine(self) -> None:
        mock_engine = MagicMock()
        mock_engine.create_routine = AsyncMock(return_value=5)

        tool = RoutineTool()
        with (
            patch.dict("sys.modules", {"cortex.routines": MagicMock(), "cortex.routines.engine": MagicMock(RoutineEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "create", "routine_name": "Bedtime"})
        assert result.success
        assert "Bedtime" in result.output

    async def test_delete_routine(self) -> None:
        mock_engine = MagicMock()
        mock_engine.delete_routine = AsyncMock(return_value=True)

        tool = RoutineTool()
        with (
            patch.dict("sys.modules", {"cortex.routines": MagicMock(), "cortex.routines.engine": MagicMock(RoutineEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "delete", "routine_id": 1})
        assert result.success
        assert "deleted" in result.output

    async def test_run_requires_id(self) -> None:
        mock_engine = MagicMock()
        tool = RoutineTool()
        with (
            patch.dict("sys.modules", {"cortex.routines": MagicMock(), "cortex.routines.engine": MagicMock(RoutineEngine=MagicMock(return_value=mock_engine))}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"action": "run"})
        assert not result.success
        assert "routine_id" in result.error


# ===================================================================
# NotifyTool
# ===================================================================


class TestNotifyTool:
    async def test_send_notification(self) -> None:
        mock_send = AsyncMock(return_value=2)

        tool = NotifyTool()
        with patch.dict("sys.modules", {
            "cortex.notifications": MagicMock(),
            "cortex.notifications.channels": MagicMock(send_notification=mock_send),
        }):
            result = await tool.execute({
                "title": "Alert",
                "message": "Something happened",
                "level": "warning",
            })
        assert result.success
        assert "Alert" in result.output
        assert result.metadata["delivered"] == 2

    async def test_send_default_level(self) -> None:
        mock_send = AsyncMock(return_value=1)

        tool = NotifyTool()
        with patch.dict("sys.modules", {
            "cortex.notifications": MagicMock(),
            "cortex.notifications.channels": MagicMock(send_notification=mock_send),
        }):
            result = await tool.execute({"title": "Info", "message": "Hello"})
        assert result.success
        assert result.metadata["level"] == "info"


# ===================================================================
# MemoryTool
# ===================================================================


class TestMemoryTool:
    async def test_search_with_results(self) -> None:
        from cortex.memory.types import MemoryHit

        hits = [
            MemoryHit(doc_id="d1", user_id="u", text="User likes pizza", score=0.9),
            MemoryHit(doc_id="d2", user_id="u", text="User is left-handed", score=0.6),
        ]
        mock_hot_query = MagicMock(return_value=hits)

        tool = MemoryTool()
        with (
            patch.dict("sys.modules", {"cortex.memory": MagicMock(), "cortex.memory.hot": MagicMock(hot_query=mock_hot_query)}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"query": "preferences"})
        assert result.success
        assert "pizza" in result.output
        assert result.metadata["count"] == 2

    async def test_search_no_results(self) -> None:
        mock_hot_query = MagicMock(return_value=[])

        tool = MemoryTool()
        with (
            patch.dict("sys.modules", {"cortex.memory": MagicMock(), "cortex.memory.hot": MagicMock(hot_query=mock_hot_query)}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            result = await tool.execute({"query": "nonexistent"})
        assert result.success
        assert "No memories" in result.output

    async def test_search_custom_max_results(self) -> None:
        mock_hot_query = MagicMock(return_value=[])

        tool = MemoryTool()
        with (
            patch.dict("sys.modules", {"cortex.memory": MagicMock(), "cortex.memory.hot": MagicMock(hot_query=mock_hot_query)}),
            patch("cortex.db.get_db", return_value=MagicMock()),
        ):
            await tool.execute({"query": "test", "max_results": 3})
        mock_hot_query.assert_called_once()
        call_kwargs = mock_hot_query.call_args
        assert call_kwargs[1].get("top_k", call_kwargs[0][3] if len(call_kwargs[0]) > 3 else None) is not None


# ===================================================================
# MemoryStoreTool
# ===================================================================


class TestMemoryStoreTool:
    async def test_store_memory(self) -> None:
        mock_ms = MagicMock()
        mock_ms.remember = AsyncMock()

        tool = MemoryStoreTool()
        with patch.dict("sys.modules", {
            "cortex.memory": MagicMock(),
            "cortex.memory.controller": MagicMock(get_memory_system=MagicMock(return_value=mock_ms)),
        }):
            result = await tool.execute({"content": "User prefers dark mode", "category": "preference"})
        assert result.success
        assert "dark mode" in result.output

    async def test_store_no_memory_system(self) -> None:
        tool = MemoryStoreTool()
        with patch.dict("sys.modules", {
            "cortex.memory": MagicMock(),
            "cortex.memory.controller": MagicMock(get_memory_system=MagicMock(return_value=None)),
        }):
            result = await tool.execute({"content": "something"})
        assert not result.success
        assert "not initialised" in result.error


# ===================================================================
# HAControlTool — graceful fallback
# ===================================================================


class TestHAControlTool:
    async def test_fallback_no_env(self) -> None:
        tool = HAControlTool()
        with patch.dict("os.environ", {}, clear=True):
            result = await tool.execute({"action": "turn_on", "entity_id": "light.living_room"})
        assert not result.success
        # Either import fails or env vars are missing
        assert result.error

    async def test_fallback_no_module(self) -> None:
        """HA module not installed → graceful error."""
        tool = HAControlTool()
        # Force ImportError by removing the module
        import sys
        saved = sys.modules.get("cortex.integrations.ha.client")
        sys.modules["cortex.integrations.ha.client"] = None  # type: ignore[assignment]
        try:
            result = await tool.execute({"action": "turn_on", "entity_id": "light.x"})
            assert not result.success
        finally:
            if saved is not None:
                sys.modules["cortex.integrations.ha.client"] = saved
            else:
                sys.modules.pop("cortex.integrations.ha.client", None)

    async def test_successful_call(self) -> None:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.call_service = AsyncMock(return_value={})

        tool = HAControlTool()
        with (
            patch.dict("os.environ", {"HA_URL": "http://ha:8123", "HA_TOKEN": "tok"}),
            patch("cortex.integrations.ha.client.HAClient", return_value=mock_client),
        ):
            result = await tool.execute({"action": "turn_on", "entity_id": "light.kitchen"})
        assert result.success
        assert "turn_on" in result.output
        assert "kitchen" in result.output


# ===================================================================
# VisionAnalyzeTool
# ===================================================================


class TestVisionAnalyzeTool:
    async def test_file_not_found(self) -> None:
        tool = VisionAnalyzeTool()
        result = await tool.execute({"image_path": "/nonexistent/img.png"})
        assert not result.success
        assert "not found" in result.error

    async def test_vision_model_unavailable(self, tmp_path: Path) -> None:
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        tool = VisionAnalyzeTool()
        with patch.dict("os.environ", {"VISION_MODEL_URL": "http://localhost:99999"}):
            result = await tool.execute({"image_path": str(img)})
        assert not result.success
        assert "Vision model not available" in result.output

    async def test_vision_model_success(self, tmp_path: Path) -> None:
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": "A cat sitting on a table"}

        tool = VisionAnalyzeTool()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute({"image_path": str(img), "question": "What is this?"})
        assert result.success
        assert "cat" in result.output


# ===================================================================
# EmbedTextTool
# ===================================================================


class TestEmbedTextTool:
    async def test_embed_success(self) -> None:
        mock_provider = MagicMock()
        mock_provider.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])

        tool = EmbedTextTool()
        with patch.dict("sys.modules", {
            "cortex.providers": MagicMock(get_provider=MagicMock(return_value=mock_provider)),
        }):
            result = await tool.execute({"text": "Hello world"})
        assert result.success
        assert "3 dimensions" in result.output
        assert result.metadata["embedding"] == [0.1, 0.2, 0.3]

    async def test_embed_provider_unavailable(self) -> None:
        tool = EmbedTextTool()
        with patch.dict("sys.modules", {
            "cortex.providers": MagicMock(get_provider=MagicMock(side_effect=Exception("no provider"))),
        }):
            result = await tool.execute({"text": "test"})
        assert not result.success
        assert "not available" in result.output


# ===================================================================
# OCRTool
# ===================================================================


class TestOCRTool:
    async def test_file_not_found(self) -> None:
        tool = OCRTool()
        result = await tool.execute({"file_path": "/no/such/file.pdf"})
        assert not result.success
        assert "not found" in result.error

    async def test_pdf_with_pdftotext(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")

        tool = OCRTool()
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"Extracted text content", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await tool.execute({"file_path": str(pdf)})
        assert result.success
        assert "Extracted text content" in result.output
        assert result.metadata["source"] == "pdftotext"

    async def test_pdf_pdftotext_unavailable(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")

        tool = OCRTool()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await tool.execute({"file_path": str(pdf)})
        assert not result.success
        assert "metadata" in result.output.lower() or result.metadata.get("path")

    async def test_image_ocr_fallback(self, tmp_path: Path) -> None:
        img = tmp_path / "screenshot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        tool = OCRTool()
        with patch.dict("os.environ", {"VISION_MODEL_URL": "http://localhost:99999"}):
            result = await tool.execute({"file_path": str(img)})
        assert not result.success
        assert "OCR not available" in result.output


# ===================================================================
# SpeechToTextTool
# ===================================================================


class TestSpeechToTextTool:
    async def test_file_not_found(self) -> None:
        tool = SpeechToTextTool()
        result = await tool.execute({"audio_path": "/no/audio.wav"})
        assert not result.success
        assert "not found" in result.error

    async def test_stt_unavailable(self, tmp_path: Path) -> None:
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"RIFF" + b"\x00" * 100)

        tool = SpeechToTextTool()
        with patch.dict("os.environ", {"STT_URL": "http://localhost:99999"}):
            result = await tool.execute({"audio_path": str(audio)})
        assert not result.success
        assert "not available" in result.output

    async def test_stt_success(self, tmp_path: Path) -> None:
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"RIFF" + b"\x00" * 100)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"text": "Hello world"}

        tool = SpeechToTextTool()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute({"audio_path": str(audio)})
        assert result.success
        assert "Hello world" in result.output


# ===================================================================
# TextToSpeechTool
# ===================================================================


class TestTextToSpeechTool:
    async def test_tts_unavailable(self) -> None:
        tool = TextToSpeechTool()
        with patch.dict("os.environ", {"TTS_URL": "http://localhost:99999"}):
            result = await tool.execute({
                "text": "Hello",
                "output_path": "/tmp/test_tts_out.wav",
            })
        assert not result.success
        assert "not available" in result.output

    async def test_tts_success(self, tmp_path: Path) -> None:
        out = tmp_path / "out.wav"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b"RIFF" + b"\x00" * 100

        tool = TextToSpeechTool()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute({
                "text": "Hello world",
                "output_path": str(out),
                "voice": "af_bella",
            })
        assert result.success
        assert "saved" in result.output.lower()
        assert out.exists()


# ===================================================================
# ImageGenerateTool
# ===================================================================


class TestImageGenerateTool:
    async def test_no_url_configured(self) -> None:
        tool = ImageGenerateTool()
        with patch.dict("os.environ", {}, clear=True):
            result = await tool.execute({"prompt": "a cat", "output_path": "/tmp/cat.png"})
        assert not result.success
        assert "not available" in result.output.lower() or "not configured" in result.output.lower()

    async def test_generation_success(self, tmp_path: Path) -> None:
        out = tmp_path / "gen.png"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b"\x89PNG" + b"\x00" * 50

        tool = ImageGenerateTool()
        with (
            patch.dict("os.environ", {"IMAGE_GEN_URL": "http://localhost:7860/generate"}),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute({
                "prompt": "a sunset",
                "output_path": str(out),
                "width": 1024,
                "height": 768,
            })
        assert result.success
        assert out.exists()


# ===================================================================
# Registry integration
# ===================================================================


class TestRegistryIntegration:
    def test_default_registry_includes_atlas_tools(self) -> None:
        from cortex.cli.tools import get_default_registry

        reg = get_default_registry()
        atlas_ids = {"ha_control", "timer", "reminder", "routine", "notify", "memory_search", "memory_store"}
        registered = {t.tool_id for t in reg.list_tools()}
        assert atlas_ids.issubset(registered), f"Missing: {atlas_ids - registered}"

    def test_default_registry_includes_multimodal_tools(self) -> None:
        from cortex.cli.tools import get_default_registry

        reg = get_default_registry()
        mm_ids = {"vision_analyze", "image_generate", "embed_text", "ocr", "speech_to_text", "text_to_speech"}
        registered = {t.tool_id for t in reg.list_tools()}
        assert mm_ids.issubset(registered), f"Missing: {mm_ids - registered}"
