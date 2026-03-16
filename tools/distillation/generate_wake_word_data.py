#!/usr/bin/env python3
"""Generate synthetic wake word training data using Qwen3-TTS.

Generates diverse audio samples of "Atlas" (positive) and similar-sounding
words (negative) for training an openwakeword model.

Usage:
    export PATH=/mnt/fastpool/miniconda3/bin:$PATH
    python3 tools/distillation/generate_wake_word_data.py \
        --output /mnt/fastpool/wake_word_data \
        --positive-count 2000 \
        --negative-count 2000
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# ─── Phrases ──────────────────────────────────────────────────────────────────

POSITIVE_PHRASES = [
    "Atlas",
    "Atlas.",
    "Atlas!",
    "Atlas?",
    "Hey Atlas",
    "Hey Atlas!",
    "OK Atlas",
    "Hi Atlas",
    "Atlas, hello",
    "Yo Atlas",
]

NEGATIVE_SIMILAR = [
    # Phonetically similar — "at" onset
    "at last", "at least", "at loss", "at rest", "attach",
    "attack", "attend", "attempt", "attic", "attract",
    "attribute", "attest", "attain", "attitude", "attorney",
    # "al" onset
    "Alice", "Alex", "Alexis", "Alias", "Alistair",
    "alive", "align", "allocate", "allow", "almost",
    "alpha", "alphas", "already", "also", "alter",
    "altitude", "always", "album", "alert", "Alfred",
    # "atl" cluster
    "Atlantis", "Atlanta", "Atlantic", "athlete", "athletic",
    # Similar vowel pattern (æ-ə-s)
    "access", "axis", "actress", "Agnes", "ashes",
    "Abbas", "Amos", "Alvis", "Arthas", "anus",
    # Similar rhythm / stress
    "August", "Albert", "Allen", "Alvin", "Angus",
    "Abbot", "Astrid", "Amber", "Anders", "Arthur",
    # Rhyming / near-rhyme
    "cactus", "lattice", "status", "practice", "mattress",
    "compass", "canvas", "Thomas", "Dallas", "palace",
    "balance", "malice", "chalice", "solace", "menace",
    # Common assistant triggers (should NOT activate Atlas)
    "Hey Siri", "OK Google", "Alexa", "Hey Google",
    "Computer", "Hey Cortana", "Jarvis", "Bixby",
    # Embedded "atlas" in sentences (should NOT trigger)
    "the atlas is on the shelf",
    "grab that atlas for me",
    "check the world atlas",
    "an atlas of the human body",
    # Partial matches
    "add this", "apt list", "act less", "add less",
    "hatlass", "cat glass", "bat last", "sat class",
    "flat grass", "thatlass", "hadlass", "mad lass",
]

NEGATIVE_GENERAL = [
    # General speech that should not trigger
    "What time is it",
    "Turn off the lights",
    "Play some music",
    "How is the weather",
    "Good morning",
    "Hello there",
    "Thank you",
    "Set a timer",
    "Call mom",
    "Send a message",
    "What is the temperature",
    "Open the door",
    "Close the window",
    "I need help",
    "Where are my keys",
    "The cat is sleeping",
    "Make some coffee",
    "Read my emails",
    "Schedule a meeting",
    "Remind me later",
]

# Speed variations for diversity
SPEEDS = [0.85, 0.9, 0.95, 1.0, 1.0, 1.05, 1.1, 1.15]


def load_model(device: str = "cuda"):
    """Load Qwen3-TTS-0.6B CustomVoice model."""
    from qwen_tts import Qwen3TTSModel

    log.info("Loading Qwen3-TTS-12Hz-0.6B-CustomVoice on %s...", device)
    model = Qwen3TTSModel.from_pretrained(
        "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        device_map="auto",
        dtype=torch.bfloat16 if device == "cuda" else torch.float32,
    )
    log.info("Model loaded! VRAM: %.1f MB", torch.cuda.memory_allocated() / 1e6 if device == "cuda" else 0)
    return model


def get_speakers(model) -> list[str]:
    """Get available speaker IDs."""
    try:
        speakers = list(model.model.get_supported_speakers())
        log.info("Available speakers (%d): %s", len(speakers), speakers)
        return speakers
    except Exception:
        return ["serena", "ryan", "aiden", "vivian", "eric", "dylan"]


def synthesize(model, text: str, speaker: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
    """Generate audio for a text string."""
    wavs, sr = model.generate_custom_voice(
        text=text,
        speaker=speaker,
        speed=speed,
    )
    return wavs[0], sr


# ─── Noise Augmentation ──────────────────────────────────────────────────────

def load_noise_sources(noise_dirs: list[str]) -> list[tuple[np.ndarray, int]]:
    """Load all WAV noise files from given directories."""
    sources = []
    for d in noise_dirs:
        p = Path(d)
        if not p.exists():
            continue
        for wav in p.rglob("*.wav"):
            try:
                audio, sr = sf.read(str(wav), dtype="float32")
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                if len(audio) > sr:  # at least 1 second
                    sources.append((audio, sr))
            except Exception:
                pass
    log.info("Loaded %d noise sources", len(sources))
    return sources


def mix_with_noise(signal: np.ndarray, sr: int, noise: np.ndarray, noise_sr: int, snr_db: float) -> np.ndarray:
    """Mix signal with noise at a given SNR in dB."""
    # Resample noise if needed
    if noise_sr != sr:
        ratio = sr / noise_sr
        indices = np.arange(0, len(noise), 1 / ratio).astype(int)
        indices = indices[indices < len(noise)]
        noise = noise[indices]

    # Tile or crop noise to match signal length
    if len(noise) < len(signal):
        noise = np.tile(noise, (len(signal) // len(noise)) + 1)
    start = random.randint(0, max(0, len(noise) - len(signal)))
    noise_seg = noise[start:start + len(signal)]

    sig_power = np.mean(signal ** 2) + 1e-10
    noise_power = np.mean(noise_seg ** 2) + 1e-10
    scale = np.sqrt(sig_power / (noise_power * (10 ** (snr_db / 10))))
    mixed = signal + scale * noise_seg
    # Normalize to prevent clipping
    peak = np.abs(mixed).max()
    if peak > 0.99:
        mixed = mixed * 0.99 / peak
    return mixed


def generate_synthetic_noise(length: int, noise_type: str) -> np.ndarray:
    """Generate synthetic noise of a given type."""
    if noise_type == "white":
        return np.random.randn(length).astype(np.float32) * 0.3
    elif noise_type == "pink":
        white = np.random.randn(length).astype(np.float32)
        # Simple 1/f approximation via cumulative filter
        b = [0.049922035, -0.095993537, 0.050612699, -0.004709510]
        a = [1.0, -2.494956002, 2.017265875, -0.522189400]
        from scipy.signal import lfilter
        return lfilter(b, a, white).astype(np.float32) * 0.3
    elif noise_type == "babble":
        # Simulate babble as sum of shifted white noise
        babble = np.zeros(length, dtype=np.float32)
        for _ in range(6):
            shift = random.randint(0, length // 4)
            segment = np.random.randn(length).astype(np.float32) * 0.15
            babble += np.roll(segment, shift)
        return babble
    elif noise_type == "hum":
        # 50/60Hz electrical hum
        freq = random.choice([50.0, 60.0])
        t = np.arange(length, dtype=np.float32) / 24000
        return (np.sin(2 * np.pi * freq * t) * 0.05).astype(np.float32)
    return np.zeros(length, dtype=np.float32)


SYNTHETIC_NOISE_TYPES = ["white", "pink", "babble", "hum"]
SNR_RANGE = (3, 20)  # dB — 3 is very noisy, 20 is mostly clean

# Real-world noise datasets to download
NOISE_DATASETS = {
    "musan": {
        "hf_id": "FluidInference/musan",
        "desc": "MUSAN noise corpus (6h noise, 42h music)",
    },
    "caiman": {
        "hf_id": "Myrtle/CAIMAN-ASR-BackgroundNoise",
        "desc": "CAIMAN-ASR background noise (curated for ASR)",
    },
}


def download_noise_datasets(cache_dir: Path) -> list[str]:
    """Download real-world noise datasets from HuggingFace."""
    downloaded = []
    try:
        from datasets import load_dataset
    except ImportError:
        log.warning("datasets library not installed, skipping HF noise downloads")
        return downloaded

    for name, info in NOISE_DATASETS.items():
        out_dir = cache_dir / name
        if out_dir.exists() and any(out_dir.rglob("*.wav")):
            log.info("Noise dataset '%s' already cached at %s", name, out_dir)
            downloaded.append(str(out_dir))
            continue

        log.info("Downloading noise dataset: %s (%s)", name, info["desc"])
        try:
            ds = load_dataset(info["hf_id"], split="train", trust_remote_code=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            saved = 0
            for i, sample in enumerate(ds):
                if "audio" in sample and sample["audio"]:
                    audio_data = sample["audio"]
                    arr = np.array(audio_data["array"], dtype=np.float32)
                    sr = audio_data["sampling_rate"]
                    if len(arr) > sr:  # at least 1 second
                        sf.write(str(out_dir / f"{name}_{i:05d}.wav"), arr, sr)
                        saved += 1
                if saved >= 500:  # cap at 500 clips per dataset
                    break
            log.info("Saved %d clips from %s to %s", saved, name, out_dir)
            downloaded.append(str(out_dir))
        except Exception as e:
            log.warning("Failed to download %s: %s", name, e)

    return downloaded


def augment_positives(
    clean_dir: Path,
    output_dir: Path,
    noise_sources: list[tuple[np.ndarray, int]],
    augments_per_clip: int = 3,
) -> list[dict]:
    """Create noise-augmented copies of every clean positive sample."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    clean_files = sorted(clean_dir.glob("*.wav"))

    if not clean_files:
        log.warning("No clean positive files to augment in %s", clean_dir)
        return manifest

    log.info("Augmenting %d clean positives × %d copies = %d augmented samples",
             len(clean_files), augments_per_clip, len(clean_files) * augments_per_clip)

    for i, wav_path in enumerate(clean_files):
        try:
            audio, sr = sf.read(str(wav_path), dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
        except Exception as e:
            log.warning("Failed to read %s: %s", wav_path, e)
            continue

        for aug_idx in range(augments_per_clip):
            snr_db = random.uniform(*SNR_RANGE)

            # 70% real noise, 30% synthetic
            if noise_sources and random.random() < 0.7:
                noise_audio, noise_sr = random.choice(noise_sources)
                noise_type = "real"
            else:
                noise_type = random.choice(SYNTHETIC_NOISE_TYPES)
                noise_audio = generate_synthetic_noise(len(audio) * 2, noise_type)
                noise_sr = sr

            augmented = mix_with_noise(audio, sr, noise_audio, noise_sr, snr_db)

            filename = f"positive_aug_{i:05d}_{aug_idx}.wav"
            sf.write(str(output_dir / filename), augmented, sr)
            manifest.append({
                "file": filename,
                "label": "positive_augmented",
                "source": wav_path.name,
                "noise_type": noise_type,
                "snr_db": round(snr_db, 1),
                "duration_ms": int(len(augmented) / sr * 1000),
                "sample_rate": sr,
            })

        if (i + 1) % 100 == 0:
            log.info("Augmented %d/%d clean clips", i + 1, len(clean_files))

    log.info("Created %d augmented positive samples", len(manifest))
    return manifest


def generate_samples(
    model,
    phrases: list[str],
    output_dir: Path,
    label: str,
    count: int,
    speakers: list[str],
):
    """Generate audio samples with diverse voices and speeds."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    generated = 0
    start = time.time()

    while generated < count:
        phrase = random.choice(phrases)
        speaker = random.choice(speakers)
        speed = random.choice(SPEEDS)

        filename = f"{label}_{generated:05d}.wav"
        filepath = output_dir / filename

        try:
            audio, sr = synthesize(model, phrase, speaker, speed)
            sf.write(str(filepath), audio, sr)

            manifest.append({
                "file": filename,
                "label": label,
                "phrase": phrase,
                "speaker": speaker,
                "speed": speed,
                "duration_ms": int(len(audio) / sr * 1000),
                "sample_rate": sr,
            })
            generated += 1

            if generated % 50 == 0:
                elapsed = time.time() - start
                rate = generated / elapsed
                eta = (count - generated) / rate if rate > 0 else 0
                log.info(
                    "[%s] %d/%d (%.1f/min, ETA %.0fm) | last: '%s' speaker=%s speed=%.2f",
                    label, generated, count, rate * 60, eta / 60,
                    phrase, speaker, speed,
                )

        except Exception as e:
            log.warning("Failed: '%s' speaker=%s: %s", phrase, speaker, e)

    return manifest


def main(args: argparse.Namespace):
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() and not args.cpu else "cpu"
    model = load_model(device)
    speakers = get_speakers(model)

    if not speakers:
        log.error("No speakers available!")
        return

    log.info("Using %d speakers, generating %d positive + %d negative samples",
             len(speakers), args.positive_count, args.negative_count)

    # Positive samples
    log.info("=== Generating POSITIVE samples (wake word) ===")
    pos_manifest = generate_samples(
        model, POSITIVE_PHRASES,
        output_dir / "positive", "positive",
        args.positive_count, speakers,
    )

    # Negative samples — 60% hard (similar sounding), 40% general
    neg_similar_count = int(args.negative_count * 0.6)
    neg_general_count = args.negative_count - neg_similar_count

    log.info("=== Generating NEGATIVE samples (similar sounding) ===")
    neg_sim_manifest = generate_samples(
        model, NEGATIVE_SIMILAR,
        output_dir / "negative", "negative",
        neg_similar_count, speakers,
    )

    log.info("=== Generating NEGATIVE samples (general speech) ===")
    neg_gen_manifest = generate_samples(
        model, NEGATIVE_GENERAL,
        output_dir / "negative", "negative_general",
        neg_general_count, speakers,
    )

    # ─── Noise augmentation of positives ──────────────────────────────────
    log.info("=== Augmenting POSITIVE samples with noise ===")

    # Download real-world noise datasets
    noise_cache = output_dir / "_noise_cache"
    if args.skip_noise_download:
        hf_noise_dirs = []
        log.info("Skipping HF noise dataset download")
    else:
        hf_noise_dirs = download_noise_datasets(noise_cache)

    noise_dirs = hf_noise_dirs + [
        str(output_dir / ".." / "atlas-wake-word-data" / "negative" / "noise"),
        str(output_dir / ".." / "atlas-wake-word-data" / "_raw_speech_commands" / "_background_noise_"),
        "/home/betan/atlas-wake-word-data/negative/noise",
        "/home/betan/atlas-wake-word-data/_raw_speech_commands/_background_noise_",
    ]
    if args.noise_dir:
        noise_dirs.insert(0, args.noise_dir)

    noise_sources = load_noise_sources(noise_dirs)
    aug_manifest = augment_positives(
        clean_dir=output_dir / "positive",
        output_dir=output_dir / "positive_augmented",
        noise_sources=noise_sources,
        augments_per_clip=args.augments_per_clip,
    )

    # Write manifest
    manifest = pos_manifest + aug_manifest + neg_sim_manifest + neg_gen_manifest
    manifest_path = output_dir / "manifest.jsonl"
    with open(manifest_path, "w") as f:
        for entry in manifest:
            f.write(json.dumps(entry) + "\n")

    log.info("=== DONE ===")
    log.info("Positive (clean): %d samples", len(pos_manifest))
    log.info("Positive (augmented): %d samples", len(aug_manifest))
    log.info("Negative (hard): %d samples", len(neg_sim_manifest))
    log.info("Negative (general): %d samples", len(neg_gen_manifest))
    log.info("TOTAL: %d samples", len(manifest))
    log.info("Manifest: %s", manifest_path)

    if device == "cuda":
        log.info("Peak VRAM: %.1f MB", torch.cuda.max_memory_allocated() / 1e6)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate wake word training data")
    parser.add_argument("--output", default="/mnt/fastpool/wake_word_data",
                        help="Output directory")
    parser.add_argument("--positive-count", type=int, default=3000,
                        help="Number of positive (wake word) samples")
    parser.add_argument("--negative-count", type=int, default=5000,
                        help="Number of negative samples (split: 60%% hard, 40%% general)")
    parser.add_argument("--cpu", action="store_true", help="Force CPU")
    parser.add_argument("--noise-dir", default=None, help="Extra noise directory for augmentation")
    parser.add_argument("--augments-per-clip", type=int, default=3,
                        help="Noise-augmented copies per clean positive clip")
    parser.add_argument("--skip-noise-download", action="store_true",
                        help="Skip downloading HF noise datasets")
    args = parser.parse_args()
    main(args)
