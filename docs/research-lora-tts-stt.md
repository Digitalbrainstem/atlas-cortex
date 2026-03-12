# Domain-Specific LoRA Adapters for TTS and STT

## Research Summary

This document explores using LoRA adapters to improve speech model accuracy for
specialized vocabulary — specifically pronunciation in TTS (Orpheus) and recognition
in STT (Whisper). This is a **separate research thread** from the main Atlas
optimization work.

## Key Finding: Yes, This Works

LoRA adapters can significantly improve both TTS pronunciation and STT recognition
for domain-specific vocabulary. The same "skill package" concept from the main
architecture applies here — when a medical topic is detected, ALL models in the
pipeline get their medical adapters simultaneously.

---

## TTS: Teaching Orpheus to Pronounce Medical Terms

### The Problem

Standard TTS engines achieve ~78% accuracy on specialized medical vocabulary.
Names like "diphenhydramine", "mesothelioma", "acetaminophen" are frequently
mispronounced because the model was trained primarily on conversational English.

### How LoRA Fixes This

Orpheus is a decoder-only transformer (same architecture as an LLM). It has
attention layers and feed-forward networks that are LoRA-compatible. A medical
TTS LoRA adapter teaches the model:

1. **Phoneme mappings** for medical terms (grapheme-to-phoneme rules)
2. **Prosody patterns** for dosage instructions (slower, clearer)
3. **Stress patterns** for multi-syllable drug names
4. **Voice quality** adjustments (calmer tone for health topics)

### Training a TTS LoRA

**Training data needed:**
- Audio recordings of correctly pronounced medical terms
- Text-to-IPA (International Phonetic Alphabet) mappings
- Paired (text, audio) examples of medical conversations

**Training process:**
```python
# Orpheus is a transformer — LoRA attaches the same way as for LLMs
from peft import get_peft_model, LoraConfig

config = LoraConfig(
    r=8,                           # Lower rank sufficient for TTS
    lora_alpha=16,
    target_modules=["q_proj", "v_proj"],  # Attention layers
    task_type="CAUSAL_LM",        # Orpheus generates audio tokens autoregressively
)

model = get_peft_model(orpheus_model, config)
# Train on medical pronunciation dataset
# Adapter size: ~20-50MB
```

**Expected improvement:**
- General TTS medical accuracy: ~78% → ~95% with LoRA adapter
- Pronunciation of top 10,000 drug names: near-perfect with enough training data

### Alternative: Pronunciation Dictionary + SSML

A simpler (non-LoRA) approach for specific terms:
```xml
<!-- SSML markup for Orpheus/any TTS -->
<speak>
  Take <phoneme alphabet="ipa" ph="daɪˌfɛnˈhaɪdrəˌmiːn">diphenhydramine</phoneme>
  every 4-6 hours as needed.
</speak>
```

**Trade-offs:**
| Approach | Coverage | Maintenance | Quality |
|---|---|---|---|
| LoRA adapter | Broad (learns patterns) | Train once, generalizes | High |
| Pronunciation dict | Per-word only | Add each new word manually | Perfect for listed words |
| **Best: Both** | Broad + exact overrides | LoRA for general, dict for critical | Highest |

---

## STT: Teaching Whisper to Recognize Medical Speech

### The Problem

Whisper transcribes "tachycardia" as "tacky cardiac", "diphenhydramine" as
"diphen hydra mean", and "mesothelioma" as "meso thee lee oma". Word error
rate (WER) for medical vocabulary is 20-40% higher than general speech.

### How LoRA Fixes This

Whisper is an encoder-decoder transformer. LoRA adapters attach to both the
audio encoder (learns to recognize medical speech patterns) and the text decoder
(learns to produce correct medical spellings).

**Training data needed:**
- Audio clips of medical conversations (doctor dictations, patient intake)
- Correct transcriptions of those clips
- Drug names spoken in various accents with labels
- Can use synthetic data: LLM generates medical text → TTS speaks it → paired data

### Training a STT LoRA

```python
from peft import get_peft_model, LoraConfig
from transformers import WhisperForConditionalGeneration

model = WhisperForConditionalGeneration.from_pretrained("openai/whisper-medium")

config = LoraConfig(
    r=8,
    lora_alpha=16,
    target_modules=["q_proj", "v_proj"],  # Both encoder and decoder
)

model = get_peft_model(model, config)
# Train on medical audio/transcript pairs
# Adapter size: ~20-50MB
# WER improvement: 20-40% on medical terms
```

### Synthetic Data Pipeline

No medical audio dataset? Generate one:
```
Step 1: LLM generates medical conversations (text)
Step 2: TTS (with medical LoRA!) speaks the text → audio files
Step 3: Pair audio + original text = training data for STT
Step 4: Train STT LoRA on the synthetic pairs
```

This creates a **self-reinforcing cycle**: better TTS → better STT training data →
better STT → better pipeline → better training data.

---

## The Full Pipeline Effect

When the medical skill activates:

```
User speaks: "What about metoprolol for blood pressure?"
                │
    STT + Medical LoRA: "metoprolol" (not "meto pro lol")
                │
    LLM + Medical LoRA: Knows metoprolol is a beta-blocker,
                        typical dosing, side effects, interactions
                │
    TTS + Medical LoRA: Pronounces "metoprolol" correctly,
                        speaks dosage clearly and calmly
                │
User hears: Accurate, well-pronounced medical guidance
```

Without skill adapters, this same interaction has errors at EVERY stage.

---

## Beyond Medical: Other High-Value Domains

| Domain | TTS Challenge | STT Challenge |
|---|---|---|
| **Medical** | Drug names, anatomy terms | Medical jargon recognition |
| **Legal** | Latin terms, case citations | Legal terminology, statute numbers |
| **Cooking** | Foreign cuisine/ingredient names | Ingredient names, cooking terms |
| **Music** | Composer names, musical terms | Song titles, artist names |
| **Science** | Chemical compounds, species names | Scientific terminology |
| **Names/Places** | Non-English proper nouns | Accented proper nouns |
| **Kids** | Slower, exaggerated speech | Child voice patterns |

Each domain gets its own skill package with TTS + STT + LLM adapters.

---

## Hardware Requirements for Training

| Adapter | Model | QLoRA VRAM | Time (5K examples) |
|---|---|---|---|
| TTS LoRA (Orpheus ~3B) | Orpheus | ~6-8 GB | 4-6 hours |
| STT LoRA (Whisper medium) | Whisper | ~4-6 GB | 2-4 hours |
| LLM LoRA (Atlas Core ~1B) | Atlas Core | ~4 GB | 2-3 hours |
| **Full skill package** | All three | **Sequential on 1 GPU** | **8-13 hours** |

A full skill package trains overnight on a single RTX 4060. Or split across two
nights. The sub-$1 electricity cost makes this practical for personal AI.

---

## Open Questions for Further Research

1. **Can a single training dataset serve all three models?** Medical text → generates
   TTS audio → generates STT training pairs. How much quality is lost in this chain?

2. **Adapter interference**: When multiple adapters are merged (medical + home), do
   they conflict? What's the quality impact of combining 3+ LoRA adapters?

3. **Orpheus-specific**: Orpheus uses emotion tags (`<laugh>`, `<sigh>`). Do LoRA
   adapters affect emotional expression? Can a medical adapter also encode
   "compassionate" emotional tone?

4. **Whisper-specific**: Whisper v3 has improved multilingual support. Does LoRA
   fine-tuning on English medical terms hurt multilingual capability? (May not matter
   for Atlas since we're English-only, but relevant for the open standard.)

5. **Continuous learning**: Can we incrementally update LoRA adapters without full
   retraining? (Adapter merging, progressive LoRA, etc.)

---

## References

- [LoRA for Pronunciation Assessment](https://arxiv.org/html/2509.02915v1)
- [Parameter-Efficient TTS via LoRA (Interspeech 2025)](https://www.isca-archive.org/interspeech_2025/kwon25_interspeech.pdf)
- [UtterTune: Phoneme/Prosody Control](https://github.com/shuheikatoinfo/UtterTune)
- [LoRA-Whisper: Multilingual ASR](https://leminhnguyen.github.io/post/speech-research/lora-whisper/)
- [Whisper LoRA Fine-Tuning (AWS)](https://aws.amazon.com/blogs/machine-learning/fine-tune-whisper-models-on-amazon-sagemaker-with-lora/)
- [Whisper LoRA Notebook (HuggingFace)](https://colab.research.google.com/github/Vaibhavs10/fast-whisper-finetuning/blob/main/Whisper_w_PEFT.ipynb)
- [Medical TTS Accuracy Benchmarks](https://www.listening.com/scientific-pronunciation)
- [Domain Adaptation with Synthetic Data](https://arxiv.org/html/2501.12501v1)
- [ElevenLabs Pronunciation Dictionaries](https://elevenlabs.io/docs/eleven-api/guides/cookbooks/text-to-speech/pronunciation-dictionaries)
