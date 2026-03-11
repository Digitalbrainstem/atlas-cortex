"""Typed pipeline events.

The pipeline generator yields these events instead of raw strings.
Callers (orchestrator, satellite, server) decide how to handle each event type.

OWNERSHIP: This module defines ALL event types the pipeline can emit.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PipelineEvent:
    """Base class for all pipeline events."""


@dataclass
class TextToken(PipelineEvent):
    """A text token from the LLM or instant/plugin answer."""
    text: str


@dataclass
class FillerToken(PipelineEvent):
    """A filler phrase to play while waiting for LLM response."""
    text: str
    sentiment: str = "question"


@dataclass
class ExpressionEvent(PipelineEvent):
    """Request avatar expression change."""
    expression: str
    intensity: float = 1.0
    sentiment: str = ""
    text: str = ""


@dataclass
class SpeakingEvent(PipelineEvent):
    """Avatar speaking state change."""
    speaking: bool
    user_id: str | None = None


@dataclass
class VisemeEvent(PipelineEvent):
    """Request viseme generation for a text segment."""
    text: str


@dataclass
class TTSEvent(PipelineEvent):
    """Request TTS synthesis for a text segment."""
    text: str
    expression: str | None = None
    is_filler: bool = False


@dataclass
class LayerResult(PipelineEvent):
    """Metadata: which layer handled the request and timing info."""
    layer: str  # "instant", "tool", "llm"
    confidence: float = 0.0
    response_time_ms: int = 0
    layer_times: dict[str, float] = field(default_factory=dict)


__all__ = [
    "PipelineEvent",
    "TextToken",
    "FillerToken",
    "ExpressionEvent",
    "SpeakingEvent",
    "VisemeEvent",
    "TTSEvent",
    "LayerResult",
]
