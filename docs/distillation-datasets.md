# Atlas Distillation: Training Datasets

This document catalogs all datasets used in Atlas model distillation,
including sources, licenses, sizes, and how they were used.

## Teacher-Generated Data

Generated using **Qwen3.5-122B-A10B-AWQ** (QuantTrio) on H100 GPU via vLLM.

| File | Domain | Rows | Description |
|------|--------|------|-------------|
| `teacher_general.jsonl` | General knowledge | 16,100 | College-grad level Q&A across all topics |
| `teacher_medicine.jsonl` | Medicine | 476 | Clinical reasoning, diagnostics |
| `teacher_coding.jsonl` | Coding | 500 | Software engineering, systems design |
| `teacher_math_reasoning.jsonl` | Math | 500 | Proofs, derivations, logic |
| `teacher_ai_ml.jsonl` | AI/ML | 700 | Neural nets, training, architectures |
| `teacher_physics_chemistry.jsonl` | Physics/Chem | 300 | Rigorous STEM explanations |
| `teacher_biology_biomed.jsonl` | Biology | 300 | Molecular bio, biomedical eng |
| `teacher_engineering.jsonl` | Engineering | 500 | Aero, mech, chem, EE, mechatronics |
| `teacher_earth_space.jsonl` | Earth/Space | 400 | Geology, astronomy, climate |
| `teacher_social_science.jsonl` | Social Science | 400 | Psychology, history |
| `teacher_creative_arts.jsonl` | Creative Arts | 400 | Film, music, game design, writing |
| `teacher_agriculture_animals.jsonl` | Agriculture v1 | 175 | Gardening, pets, livestock |
| `teacher_agriculture_animals_v2.jsonl` | Agriculture v2 | 399 | Expanded: 574 total covering vegetables, fruits, herbs, soil, pests, dog/cat food safety, pet health, chickens, aquariums, other pets |
| `action_tags.jsonl` | Action Tags | 461 | Layer 2.5 `[ACTION:...]` format training |
| `dpo_pairs.jsonl` | DPO Alignment | 138 | Chosen (Atlas personality) vs rejected pairs |

## Free / Open-Source Datasets

### Medical (Expert Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| medical-o1-SFT | HuggingFace | 19,704 | Apache 2.0 | `m-a-p/medical-o1-SFT` |
| MedMCQA | HuggingFace | ~190K (with explanations) | Research | `openlifescienceai/medmcqa` |
| MedQA (USMLE) | HuggingFace | ~12.7K | Research | `bigbio/med_qa` |
| medalpaca WikiDoc | HuggingFace | ~10K | MIT | `medalpaca/medical_meadow_wikidoc` |
| PubMedQA | HuggingFace | 20K (capped) | MIT | `qiaojin/PubMedQA` |
| SQuAD/TriviaQA (medical filter) | HuggingFace | ~32K | CC-BY-SA 4.0 / Apache | `rajpurkar/squad_v2`, `trivia_qa` |
| **Total** | | **~285K** | | |

### Coding (Expert Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| Magicoder-OSS-Instruct | HuggingFace | 5,000 | Apache 2.0 | `ise-uiuc/Magicoder-OSS-Instruct-75K` |
| Evol-Instruct-Code-80k | HuggingFace | ~80K | Apache 2.0 | `nickrosh/Evol-Instruct-Code-80k-v1` |
| CodeFeedback-Filtered | HuggingFace | ~157K | Apache 2.0 | `m-a-p/CodeFeedback-Filtered-Instruction` |
| dolphin-coder | HuggingFace | ~23K | Apache 2.0 | `QuixiAI/dolphin-coder` |
| **Total** | | **~265K** | | |

### Math/Reasoning (Expert Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| NuminaMath-CoT | HuggingFace | 5,000 | Apache 2.0 | `AI-MO/NuminaMath-CoT` |
| MATH (Hendrycks) | HuggingFace | ~12.5K | MIT | `qwedsacf/competition_math` |
| Orca-Math | HuggingFace | 20K (capped) | MIT | `microsoft/orca-math-word-problems-200k` |
| **Total** | | **~37K** | | |

### AI/ML (Expert Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| Dolly-15K (AI/ML filter) | HuggingFace | ~500-1K | Apache 2.0 | `databricks/databricks-dolly-15k` |
| Alpaca (AI/ML filter) | HuggingFace | ~2-3K | CC-BY-NC 4.0 | `tatsu-lab/alpaca` |
| WizardLM 70K (AI/ML filter) | HuggingFace | ~3-5K | Apache 2.0 | `WizardLMTeam/WizardLM_evol_instruct_70k` |
| Self-Instruct (AI/ML filter) | HuggingFace | ~1-2K | Apache 2.0 | `yizhongw/self_instruct` |
| **Total** | | **~25K** | | |

### Physics/Chemistry (Advanced Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| CAMEL-AI Physics | HuggingFace | 5,000 | CC-BY-NC 4.0 | `camel-ai/physics` |
| CAMEL-AI Chemistry | HuggingFace | 5,000 | CC-BY-NC 4.0 | `camel-ai/chemistry` |
| SciQ (physics/chem filter) | HuggingFace | 4,580 | CC-BY-NC 3.0 | `allenai/sciq` |
| **Total** | | **~15K** | | |

### Biology/Biomedical (Advanced Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| CAMEL-AI Biology | HuggingFace | 5,000 | CC-BY-NC 4.0 | `camel-ai/biology` |
| SciQ (biology filter) | HuggingFace | 5,590 | CC-BY-NC 3.0 | `allenai/sciq` |
| **Total** | | **~11K** | | |

### Engineering (Advanced Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| STEM-AI Electrical Eng | HuggingFace | 1,130 | Open | `STEM-AI-mtl/Electrical-engineering` |
| Dolly/Alpaca (eng filter) | HuggingFace | ~4.8K | Apache 2.0 | filtered from general datasets |
| SQuAD/TriviaQA (eng filter) | HuggingFace | ~15K | CC-BY-SA 4.0 | `rajpurkar/squad_v2`, `trivia_qa` |
| stemdataset/STEM (engineering) | HuggingFace | ~19K (pending) | Open | `stemdataset/STEM` |
| **Total** | | **~40K+** | | |

### Earth/Space (Competent Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| SciQ (earth/space filter) | HuggingFace | ~2K | CC-BY-NC 3.0 | `allenai/sciq` |
| ARC (earth/space filter) | HuggingFace | ~1K | CC-BY-SA 4.0 | `allenai/ai2_arc` |
| Dolly/Alpaca (earth filter) | HuggingFace | ~1K | Apache 2.0 | filtered |
| SQuAD/TriviaQA (earth filter) | HuggingFace | ~9.4K | CC-BY-SA 4.0 | filtered |
| **Total** | | **~24K** | | |

### Social Science (Competent Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| SciQ (social filter) | HuggingFace | ~1K | CC-BY-NC 3.0 | `allenai/sciq` |
| Dolly/Alpaca (social filter) | HuggingFace | ~2K | Apache 2.0 | filtered |
| SQuAD/TriviaQA (social filter) | HuggingFace | ~18K | CC-BY-SA 4.0 | filtered |
| **Total** | | **~33K** | | |

### Creative Arts (Competent Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| creative_writing | HuggingFace | ~5K | Open | `lukehinds/creative_writing` |
| Dolly/Alpaca (creative filter) | HuggingFace | ~15K | Apache 2.0 | filtered |
| SQuAD/TriviaQA (creative filter) | HuggingFace | ~9K | CC-BY-SA 4.0 | filtered |
| **Total** | | **~30K** | | |

### Agriculture/Animals (Competent Tier)

| Dataset | Source | Rows Used | License | Link |
|---------|--------|-----------|---------|------|
| 45acp/agronomy | HuggingFace | 7,362 | MIT | `45acp/agronomy` |
| argilla/farming | HuggingFace | 1,695 | Open | `argilla/farming` |
| SQuAD/TriviaQA (ag filter) | HuggingFace | ~5.3K | CC-BY-SA 4.0 | filtered |
| Teacher-generated (v1+v2) | Custom | 574 | Custom | Covers pets, gardening, pests, aquariums |
| **Total** | | **~15K** | | |

**Note:** The CROP3 dataset (`AI4Agr`, 210K crop science Q&A, NeurIPS 2024) was identified
but could not be downloaded — it may require access request. If obtained, it would massively
boost agricultural knowledge.

## Dataset Resources Evaluated But Not Used

These were evaluated and found unsuitable for text instruction tuning (image/tabular only):

| Resource | Why Not Used |
|----------|-------------|
| VetDataHub (GitHub) | Image datasets for veterinary radiology |
| PlantDisease (Kaggle) | Plant leaf image classification |
| DeepWeeds (GitHub) | Weed species image classification |
| digital-agriculture-datasets (GitHub) | All image/segmentation datasets |
| Lacuna Fund agriculture | Geospatial crop boundary polygons |
| UCI ML Repository agriculture | Tabular/numerical sensor data |
| OpenML agriculture | Tabular classification datasets |
| Kaggle Animal Veterinary Health | Tabular clinical records |
| AAHA vet AI radiology | Article about imaging tools |

## Useful Reference Lists

- [mlabonne/llm-datasets](https://github.com/mlabonne/llm-datasets) — Comprehensive curated list of instruction tuning datasets
- [RenzeLou/Datasets-for-Question-Answering](https://github.com/RenzeLou/Datasets-for-Question-Answering) — QA dataset index
- [raunak-agarwal/instruction-datasets](https://github.com/raunak-agarwal/instruction-datasets) — Instruction dataset collection
- [Awesome-Medical-Large-Language-Models](https://github.com/burglarhobbit/Awesome-Medical-Large-Language-Models) — Medical LLM resources
- [Open Medical-LLM Leaderboard](https://huggingface.co/spaces/openlifescienceai/open_medical_llm_leaderboard) — Medical model benchmarks

## Grand Total

| Category | Total Rows |
|----------|-----------|
| Teacher-generated | ~5,900 |
| Free datasets | ~700K+ |
| **Combined** | **~706K+** |

## Additional Datasets Found (Round 2)

### Pet Health (Agriculture/Animals LoRA)

| Dataset | Source | Rows | License | Link |
|---------|--------|------|---------|------|
| Pet Health Symptoms | HuggingFace | 2,000 | Open | `karenwky/pet-health-symptoms-dataset` |

Covers: skin irritations, digestive issues, parasites, ear infections, mobility problems.
Includes both owner observations ("My dog has bald patches") and clinical notes.
Species covered: dogs, cats, ferrets, rabbits, guinea pigs, cockatiels.

### Medical (Additional)

| Dataset | Source | Rows | License | Link |
|---------|--------|------|---------|------|
| MedQuAD | HuggingFace | 16,405 | CC-BY 4.0 | `keivalya/MedQuad-MedicalQnADataset` |

NIH-sourced medical Q&A from 12 official sources (cancer.gov, GARD, MedlinePlus, etc.).
Original: 47,457 total (some answers removed for copyright).

### Resources Evaluated But Not Usable

| Resource | Type | Why Not Used |
|----------|------|-------------|
| Stanford AIMI shared datasets | Medical imaging (X-rays, CT, MRI) | Image data, not text Q&A |
| awesome-healthcare-datasets (GitHub) | EHR, imaging, genomics | Clinical records/images, not instruction Q&A |
| MSKCC AI datasets guide | Links to imaging/clinical databases | All image/EHR data |
| Kaggle biomedical-ai | Tabular biomedical data | Not text Q&A format |
| UF IC3 datasets | Clinical research datasets | Registration required, EHR format |
| NEJM AI article | Research paper | Not a dataset |
| PMC synthetic clinical notes | Research paper about synthetic data | Describes method, not downloadable Q&A |
| Oxford VGG Pets | Pet breed image classification | Image data |
| dog-cat-full-dataset | Image classification | Image data |
| TensorFlow cats_vs_dogs | Image classification | Image data |
| fish-datasets | Fish detection images | Image data |
| aquarium.sea-lion.ai | Aquatic species detection | Image data |
| awesome-ocean-ai-data | Ocean imagery datasets | Image data |
| VetHeal-app | Mobile app source code | Not a dataset |
| Roboflow lawn mower | Object detection images | Image data |
| awesome-forests | Forest remote sensing imagery | Image data |
| BioASQ participants area | Biomedical Q&A | Requires registration for full data |
| TELUS biology text | Commercial dataset | Paid, not open source |
| Kenhub anatomy | Learning platform | Not a dataset |
| macgence chatbot training | Blog article | Not a dataset |
| PetCare hackathon | Hackathon project | Not a dataset |
