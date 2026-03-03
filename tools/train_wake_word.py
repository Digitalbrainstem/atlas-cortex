#!/usr/bin/env python3
"""Train a custom "atlas" wake word model for openwakeword.

Uses Kokoro TTS (running on Unraid) to generate synthetic positive clips,
and phonetically similar words for adversarial negatives.
Bypasses piper-sample-generator entirely (no CUDA required).

Usage:
    python tools/train_wake_word.py --kokoro-url http://192.168.3.8:8880 --output-dir ./wake_word_training
    python tools/train_wake_word.py --generate-only   # Just generate clips
    python tools/train_wake_word.py --train-only       # Just train (clips already exist)
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import random
import sys
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import requests
import scipy.io.wavfile
import scipy.signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Kokoro TTS clip generation ────────────────────────────────────

# English voices only (for clear "atlas" pronunciation)
ENGLISH_VOICES = [
    "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
]

TARGET_PHRASES = ["atlas"]
SPEED_VARIATIONS = [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15]

# Adversarial negatives — phonetically similar but should NOT trigger
ADVERSARIAL_NEGATIVES = [
    "alice", "at last", "lattice", "at loss", "at less",
    "attic", "axis", "actress", "access", "about us",
    "adalus", "atticus", "dallas", "palace", "malice",
    "that glass", "that last", "that class", "catalyst",
]

EXTRA_NEGATIVES = [
    "hello", "goodbye", "hey there", "what time is it",
    "turn on the lights", "play some music", "set a timer",
    "how are you", "good morning", "good night",
    "okay google", "hey siri", "alexa", "hey cortana",
    "the weather today", "remind me", "call mom",
]


def generate_kokoro_clip(text, voice, speed, kokoro_url):
    """Generate a single WAV clip via Kokoro TTS API."""
    try:
        resp = requests.post(
            f"{kokoro_url}/v1/audio/speech",
            json={"model": "kokoro", "input": text, "voice": voice,
                  "speed": speed, "response_format": "wav"},
            timeout=30,
        )
        return resp.content if resp.status_code == 200 else None
    except Exception:
        return None


def resample_to_16k(wav_data):
    """Read WAV bytes and resample to 16kHz mono int16."""
    try:
        bio = io.BytesIO(wav_data)
        sr, audio = scipy.io.wavfile.read(bio)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != 16000:
            n_samples = int(len(audio) * 16000 / sr)
            audio = scipy.signal.resample(audio, n_samples)
        return np.clip(audio, -32768, 32767).astype(np.int16)
    except Exception:
        return None


def generate_clips(kokoro_url, output_dir, n_pos_train=3000, n_pos_test=500,
                   n_neg_train=3000, n_neg_test=500, max_workers=4):
    """Generate all training clips using Kokoro TTS."""
    dirs = {
        "positive_train": os.path.join(output_dir, "atlas", "positive_train"),
        "positive_test": os.path.join(output_dir, "atlas", "positive_test"),
        "negative_train": os.path.join(output_dir, "atlas", "negative_train"),
        "negative_test": os.path.join(output_dir, "atlas", "negative_test"),
    }
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    def _gen_batch(texts, out_dir, n_target, label):
        existing = len([f for f in os.listdir(out_dir) if f.endswith(".wav")])
        if existing >= 0.95 * n_target:
            logger.info("  %s: %d clips exist (target %d), skipping", label, existing, n_target)
            return
        remaining = n_target - existing
        logger.info("  %s: generating %d clips...", label, remaining)

        tasks = [(random.choice(texts), random.choice(ENGLISH_VOICES),
                  random.choice(SPEED_VARIATIONS)) for _ in range(remaining)]
        generated = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(generate_kokoro_clip, t, v, s, kokoro_url): (t, v, s)
                       for t, v, s in tasks}
            for f in as_completed(futures):
                wav_data = f.result()
                if wav_data:
                    audio = resample_to_16k(wav_data)
                    if audio is not None and len(audio) >= 4000:
                        scipy.io.wavfile.write(
                            os.path.join(out_dir, f"{uuid.uuid4().hex}.wav"), 16000, audio)
                        generated += 1
                        if generated % 100 == 0:
                            logger.info("    %s: %d/%d", label, generated, remaining)
        logger.info("  %s: %d clips generated", label, generated)

    logger.info("Generating positive clips...")
    _gen_batch(TARGET_PHRASES, dirs["positive_train"], n_pos_train, "pos_train")
    _gen_batch(TARGET_PHRASES, dirs["positive_test"], n_pos_test, "pos_test")

    all_negatives = ADVERSARIAL_NEGATIVES + EXTRA_NEGATIVES
    logger.info("Generating negative clips...")
    _gen_batch(all_negatives, dirs["negative_train"], n_neg_train, "neg_train")
    _gen_batch(all_negatives, dirs["negative_test"], n_neg_test, "neg_test")
    logger.info("Clip generation complete!")


# ── Data augmentation ─────────────────────────────────────────────

def augment_single(audio, total_length=32000):
    """Apply random augmentation to a single clip."""
    # Random pitch shift
    if random.random() < 0.3:
        semitones = random.uniform(-2, 2)
        factor = 2 ** (semitones / 12.0)
        n_samples = int(len(audio) / factor)
        audio = scipy.signal.resample(audio.astype(np.float32), n_samples)
        audio = np.clip(audio, -32768, 32767).astype(np.int16)
    # Random noise
    if random.random() < 0.5:
        noise = np.random.normal(0, random.uniform(0.001, 0.02) * 32768, len(audio))
        audio = np.clip(audio.astype(np.float32) + noise, -32768, 32767).astype(np.int16)
    # Random volume
    if random.random() < 0.4:
        audio = np.clip(audio.astype(np.float32) * random.uniform(0.5, 1.5), -32768, 32767).astype(np.int16)
    # Pad/trim to fixed length
    if len(audio) >= total_length:
        start = random.randint(0, max(0, len(audio) - total_length))
        audio = audio[start:start + total_length]
    else:
        pad_left = random.randint(0, total_length - len(audio))
        audio = np.pad(audio, (pad_left, total_length - len(audio) - pad_left), mode='constant')
    return audio


# ── Feature computation ──────────────────────────────────────────

def compute_features_batch(wav_dir, total_length=32000, augmentation_rounds=2):
    """Compute openwakeword embedding features for all clips in a directory using batch API."""
    from openwakeword.utils import AudioFeatures
    af = AudioFeatures()

    wav_files = sorted(Path(wav_dir).glob("*.wav"))
    if not wav_files:
        return np.array([])

    all_features = []
    batch_size = 128

    for round_idx in range(augmentation_rounds):
        logger.info("    Round %d/%d (%d clips)...", round_idx + 1, augmentation_rounds, len(wav_files))
        clips = []
        for wav_path in wav_files:
            try:
                sr, audio = scipy.io.wavfile.read(str(wav_path))
                if audio.ndim > 1:
                    audio = audio.mean(axis=1).astype(np.int16)
                clips.append(augment_single(audio, total_length))
            except Exception as e:
                logger.warning("Failed to load: %s: %s", wav_path.name, e)

        if not clips:
            continue

        clip_array = np.array(clips, dtype=np.int16)
        logger.info("    Computing embeddings for %d clips in batches of %d...", len(clip_array), batch_size)
        features = af.embed_clips(clip_array, batch_size=batch_size)
        logger.info("    Batch embeddings shape: %s", features.shape)
        all_features.append(features)

    if not all_features:
        return np.array([])

    return np.concatenate(all_features, axis=0).astype(np.float32)


# ── Model training ───────────────────────────────────────────────

def train_model(output_dir, max_steps=25000, layer_size=64, batch_size=256, lr=0.001):
    """Train the openwakeword DNN classifier."""
    import torch
    import torch.nn as nn

    feature_dir = os.path.join(output_dir, "atlas")

    pos_train = np.load(os.path.join(feature_dir, "positive_features_train.npy"))
    pos_test = np.load(os.path.join(feature_dir, "positive_features_test.npy"))
    neg_train = np.load(os.path.join(feature_dir, "negative_features_train.npy"))
    neg_test = np.load(os.path.join(feature_dir, "negative_features_test.npy"))

    logger.info("Pos train: %s, test: %s | Neg train: %s, test: %s",
                pos_train.shape, pos_test.shape, neg_train.shape, neg_test.shape)

    X_train = np.concatenate([pos_train, neg_train])
    y_train = np.concatenate([np.ones(len(pos_train)), np.zeros(len(neg_train))]).astype(np.float32)
    X_test = np.concatenate([pos_test, neg_test])
    y_test = np.concatenate([np.ones(len(pos_test)), np.zeros(len(neg_test))]).astype(np.float32)

    perm = np.random.permutation(len(X_train))
    X_train, y_train = X_train[perm], y_train[perm]

    input_shape = X_train.shape[1:]

    class WakeWordDNN(nn.Module):
        def __init__(self, in_dim, hid_dim):
            super().__init__()
            self.net = nn.Sequential(
                nn.Flatten(),
                nn.Linear(in_dim, hid_dim), nn.ReLU(), nn.LayerNorm(hid_dim),
                nn.Linear(hid_dim, hid_dim), nn.ReLU(), nn.LayerNorm(hid_dim),
                nn.Linear(hid_dim, hid_dim), nn.ReLU(), nn.LayerNorm(hid_dim),
                nn.Linear(hid_dim, 1), nn.Sigmoid(),
            )
        def forward(self, x):
            return self.net(x)

    model = WakeWordDNN(input_shape[0] * input_shape[1], layer_size)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.BCELoss()

    X_train_t = torch.from_numpy(X_train).float()
    y_train_t = torch.from_numpy(y_train).float().unsqueeze(1)
    X_test_t = torch.from_numpy(X_test).float()
    y_test_t = torch.from_numpy(y_test).float().unsqueeze(1)

    best_val_acc = 0
    best_state = None

    for step in range(max_steps):
        model.train()
        idx = np.random.choice(len(X_train_t), batch_size, replace=False)
        pred = model(X_train_t[idx])
        loss = criterion(pred, y_train_t[idx])
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        if (step + 1) % 500 == 0:
            model.eval()
            with torch.no_grad():
                val_pred = model(X_test_t)
                val_binary = (val_pred > 0.5).float()
                val_acc = (val_binary == y_test_t).float().mean().item()
                tp = ((val_binary == 1) & (y_test_t == 1)).sum().item()
                fp = ((val_binary == 1) & (y_test_t == 0)).sum().item()
                fn = ((val_binary == 0) & (y_test_t == 1)).sum().item()
                tn = ((val_binary == 0) & (y_test_t == 0)).sum().item()
                recall = tp / (tp + fn + 1e-8)
                fpr = fp / (fp + tn + 1e-8)

            logger.info("Step %5d — loss: %.4f, acc: %.3f, recall: %.3f, FPR: %.4f",
                        step + 1, loss.item(), val_acc, recall, fpr)
            if val_acc > best_val_acc:
                best_val_acc = val_acc
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
            if best_val_acc > 0.95 and step > 5000:
                logger.info("Early stopping (val_acc=%.3f)", best_val_acc)
                break

    if best_state:
        model.load_state_dict(best_state)

    # Export ONNX
    model.eval()
    onnx_path = os.path.join(feature_dir, "atlas.onnx")
    dummy = torch.randn(1, *input_shape)
    torch.onnx.export(model, dummy, onnx_path,
                      input_names=["input"], output_names=["output"],
                      dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
                      opset_version=11)
    logger.info("Model saved: %s (val_acc=%.3f)", onnx_path, best_val_acc)
    return onnx_path


def main():
    parser = argparse.ArgumentParser(description="Train custom 'atlas' wake word")
    parser.add_argument("--kokoro-url", default="http://192.168.3.8:8880")
    parser.add_argument("--output-dir", default="./wake_word_training")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--train-only", action="store_true")
    parser.add_argument("--features-only", action="store_true")
    parser.add_argument("--n-positive", type=int, default=3000)
    parser.add_argument("--n-negative", type=int, default=3000)
    parser.add_argument("--max-steps", type=int, default=25000)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    feature_dir = os.path.join(output_dir, "atlas")

    if not args.train_only and not args.features_only:
        logger.info("=" * 60)
        logger.info("STEP 1: Generating clips via Kokoro TTS")
        logger.info("=" * 60)
        try:
            resp = requests.get(f"{args.kokoro_url}/v1/audio/voices", timeout=5)
            logger.info("Kokoro reachable (%d voices)", len(resp.json()) if resp.ok else 0)
        except Exception as e:
            logger.error("Kokoro unreachable at %s: %s", args.kokoro_url, e)
            sys.exit(1)

        generate_clips(args.kokoro_url, output_dir,
                       n_pos_train=args.n_positive, n_pos_test=args.n_positive // 5,
                       n_neg_train=args.n_negative, n_neg_test=args.n_negative // 5,
                       max_workers=args.workers)

    if args.generate_only:
        return

    if not args.train_only:
        logger.info("=" * 60)
        logger.info("STEP 2: Computing features")
        logger.info("=" * 60)
        for split in ["positive_train", "positive_test", "negative_train", "negative_test"]:
            feat_file = os.path.join(feature_dir, f"{split}_features.npy"
                                     if "train" not in split and "test" not in split
                                     else f"{'positive' if 'positive' in split else 'negative'}_features_{'train' if 'train' in split else 'test'}.npy")
            if os.path.exists(feat_file):
                logger.info("  %s already exists, skipping", split)
                continue
            wav_dir = os.path.join(feature_dir, split)
            logger.info("  Computing features for %s...", split)
            features = compute_features_batch(wav_dir, augmentation_rounds=2)
            if features.size > 0:
                np.save(feat_file, features)
                logger.info("  Saved: %s %s", split, features.shape)
            else:
                logger.error("  No features for %s!", split)
                sys.exit(1)

    if args.features_only:
        return

    logger.info("=" * 60)
    logger.info("STEP 3: Training model")
    logger.info("=" * 60)
    onnx_path = train_model(output_dir, max_steps=args.max_steps)
    logger.info("=" * 60)
    logger.info("DONE! Model at: %s", onnx_path)
    logger.info("Deploy: copy atlas.onnx to satellite /opt/atlas-satellite/models/")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
