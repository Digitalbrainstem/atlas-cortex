"""Multi-modal tools: vision, image generation, embeddings, OCR, STT, TTS.

These tools interface with model servers (typically running on a local GPU).
Every tool gracefully falls back when the backing service is unavailable.
"""

# Module ownership: Agent tool infrastructure — multi-modal capabilities
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)

_DEFAULT_OLLAMA_URL = "http://localhost:11434"


def _image_metadata(path: Path) -> dict[str, Any]:
    """Return basic file metadata; optionally include image dimensions."""
    meta: dict[str, Any] = {
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }
    try:
        from PIL import Image

        with Image.open(path) as img:
            meta["width"], meta["height"] = img.size
            meta["format"] = img.format or "unknown"
    except Exception:
        pass
    return meta


# ---------------------------------------------------------------------------
# Vision / Image Analysis
# ---------------------------------------------------------------------------


class VisionAnalyzeTool(AgentTool):
    """Analyze an image: describe contents, extract text, identify UI elements."""

    tool_id = "vision_analyze"
    description = "Analyze an image: describe contents, extract text, identify UI elements"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "Path to the image file",
            },
            "question": {
                "type": "string",
                "description": "Optional question to ask about the image",
            },
        },
        "required": ["image_path"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        import httpx

        image_path = Path(params["image_path"])
        if not image_path.is_file():
            return ToolResult(success=False, output="", error=f"File not found: {image_path}")

        question = params.get("question", "Describe this image in detail.")
        model_url = os.environ.get("VISION_MODEL_URL", _DEFAULT_OLLAMA_URL)
        vision_model = os.environ.get("VISION_MODEL", "llava")

        try:
            image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError as exc:
            return ToolResult(success=False, output="", error=f"Cannot read image: {exc}")

        payload = {
            "model": vision_model,
            "prompt": question,
            "images": [image_data],
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{model_url}/api/generate", json=payload)
                resp.raise_for_status()
                result = resp.json()
        except Exception:
            meta = _image_metadata(image_path)
            return ToolResult(
                success=False,
                output=f"Vision model not available. File metadata: {meta}",
                error="Could not connect to vision model endpoint",
                metadata=meta,
            )

        answer = result.get("response", "")
        return ToolResult(
            success=True,
            output=answer,
            metadata={"model": vision_model, "image": str(image_path)},
        )


# ---------------------------------------------------------------------------
# Image Generation
# ---------------------------------------------------------------------------


class ImageGenerateTool(AgentTool):
    """Generate an image from a text description."""

    tool_id = "image_generate"
    description = "Generate an image from a text description"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Text description of the image to generate",
            },
            "output_path": {
                "type": "string",
                "description": "File path to save the generated image",
            },
            "width": {
                "type": "integer",
                "description": "Image width in pixels (default: 512)",
            },
            "height": {
                "type": "integer",
                "description": "Image height in pixels (default: 512)",
            },
        },
        "required": ["prompt", "output_path"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        import httpx

        prompt: str = params["prompt"]
        output_path = Path(params["output_path"])
        width: int = params.get("width", 512)
        height: int = params.get("height", 512)

        gen_url = os.environ.get("IMAGE_GEN_URL", "")
        if not gen_url:
            return ToolResult(
                success=False,
                output="Image generation not available — IMAGE_GEN_URL not configured",
                error="IMAGE_GEN_URL environment variable not set",
            )

        payload = {
            "prompt": prompt,
            "width": width,
            "height": height,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(gen_url, json=payload)
                resp.raise_for_status()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(resp.content)
        except Exception as exc:
            return ToolResult(
                success=False,
                output="Image generation not available",
                error=f"Image generation failed: {exc}",
            )

        return ToolResult(
            success=True,
            output=f"Image saved to {output_path}",
            metadata={
                "path": str(output_path),
                "width": width,
                "height": height,
            },
        )


# ---------------------------------------------------------------------------
# Text Embeddings
# ---------------------------------------------------------------------------


class EmbedTextTool(AgentTool):
    """Generate text embeddings for semantic search."""

    tool_id = "embed_text"
    description = "Generate text embeddings for semantic search"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to generate an embedding for",
            },
        },
        "required": ["text"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        text: str = params["text"]

        try:
            from cortex.providers import get_provider
        except ImportError:
            return ToolResult(
                success=False,
                output="Embedding model not available",
                error="LLM provider module not installed",
            )

        try:
            provider = get_provider()
            embedding = await provider.embed(text)
        except Exception as exc:
            return ToolResult(
                success=False,
                output="Embedding model not available",
                error=f"Embedding failed: {exc}",
            )

        return ToolResult(
            success=True,
            output=f"Embedding generated ({len(embedding)} dimensions)",
            metadata={"dimensions": len(embedding), "embedding": embedding},
        )


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


class OCRTool(AgentTool):
    """Extract text from an image or PDF."""

    tool_id = "ocr"
    description = "Extract text from an image or PDF"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the image or PDF file",
            },
        },
        "required": ["file_path"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        file_path = Path(params["file_path"])
        if not file_path.is_file():
            return ToolResult(success=False, output="", error=f"File not found: {file_path}")

        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            return await self._ocr_pdf(file_path)
        return await self._ocr_image(file_path)

    async def _ocr_pdf(self, file_path: Path) -> ToolResult:
        """Try pdftotext first, fall back to metadata."""
        import asyncio

        try:
            proc = await asyncio.create_subprocess_exec(
                "pdftotext", str(file_path), "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            text = stdout.decode(errors="replace").strip()
            if text:
                return ToolResult(
                    success=True,
                    output=text,
                    metadata={"source": "pdftotext", "path": str(file_path)},
                )
        except Exception:
            log.debug("pdftotext not available, returning metadata")

        meta = {"path": str(file_path), "size_bytes": file_path.stat().st_size}
        return ToolResult(
            success=False,
            output=f"Could not extract text from PDF. File metadata: {meta}",
            error="pdftotext not available and vision OCR not supported for PDFs",
            metadata=meta,
        )

    async def _ocr_image(self, file_path: Path) -> ToolResult:
        """Use vision model to extract text from an image."""
        import httpx

        model_url = os.environ.get("VISION_MODEL_URL", _DEFAULT_OLLAMA_URL)
        vision_model = os.environ.get("VISION_MODEL", "llava")

        try:
            image_data = base64.b64encode(file_path.read_bytes()).decode("ascii")
        except OSError as exc:
            return ToolResult(success=False, output="", error=f"Cannot read image: {exc}")

        payload = {
            "model": vision_model,
            "prompt": "Extract all text from this image. Return only the extracted text, preserving layout where possible.",
            "images": [image_data],
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{model_url}/api/generate", json=payload)
                resp.raise_for_status()
                result = resp.json()
        except Exception:
            meta = _image_metadata(file_path)
            return ToolResult(
                success=False,
                output=f"OCR not available. File metadata: {meta}",
                error="Could not connect to vision model for OCR",
                metadata=meta,
            )

        text = result.get("response", "")
        return ToolResult(
            success=True,
            output=text,
            metadata={"source": "vision_ocr", "model": vision_model, "path": str(file_path)},
        )


# ---------------------------------------------------------------------------
# Speech-to-Text
# ---------------------------------------------------------------------------


class SpeechToTextTool(AgentTool):
    """Transcribe audio to text."""

    tool_id = "speech_to_text"
    description = "Transcribe audio to text"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "audio_path": {
                "type": "string",
                "description": "Path to the audio file",
            },
        },
        "required": ["audio_path"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        import httpx

        audio_path = Path(params["audio_path"])
        if not audio_path.is_file():
            return ToolResult(success=False, output="", error=f"File not found: {audio_path}")

        stt_url = os.environ.get("STT_URL", "http://localhost:8178")

        try:
            audio_bytes = audio_path.read_bytes()
        except OSError as exc:
            return ToolResult(success=False, output="", error=f"Cannot read audio file: {exc}")

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{stt_url}/inference",
                    files={"file": (audio_path.name, audio_bytes)},
                )
                resp.raise_for_status()
                result = resp.json()
        except Exception:
            return ToolResult(
                success=False,
                output="Speech-to-text not available",
                error="Could not connect to STT endpoint",
            )

        text = result.get("text", "")
        return ToolResult(
            success=True,
            output=text,
            metadata={"audio_file": str(audio_path)},
        )


# ---------------------------------------------------------------------------
# Text-to-Speech
# ---------------------------------------------------------------------------


class TextToSpeechTool(AgentTool):
    """Convert text to speech audio."""

    tool_id = "text_to_speech"
    description = "Convert text to speech audio"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to convert to speech",
            },
            "output_path": {
                "type": "string",
                "description": "File path to save the audio output",
            },
            "voice": {
                "type": "string",
                "description": "Voice ID to use (default: af_bella)",
            },
        },
        "required": ["text", "output_path"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        import httpx

        text: str = params["text"]
        output_path = Path(params["output_path"])
        voice: str = params.get("voice", "af_bella")

        tts_url = os.environ.get("TTS_URL", "http://localhost:8880")

        # OpenAI-compatible TTS endpoint
        payload = {
            "model": voice,
            "input": text,
            "voice": voice,
            "response_format": "wav",
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{tts_url}/v1/audio/speech",
                    json=payload,
                )
                resp.raise_for_status()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(resp.content)
        except Exception:
            return ToolResult(
                success=False,
                output="Text-to-speech not available",
                error="Could not connect to TTS endpoint",
            )

        return ToolResult(
            success=True,
            output=f"Audio saved to {output_path}",
            metadata={
                "path": str(output_path),
                "voice": voice,
                "text_length": len(text),
            },
        )
