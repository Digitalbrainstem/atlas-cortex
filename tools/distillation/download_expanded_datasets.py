"""Download and filter expanded free datasets for all LoRA domains.
Maximizes free data to minimize teacher generation needs."""
from __future__ import annotations
import json, os, re
from pathlib import Path
from datasets import load_dataset, concatenate_datasets

OUT_DIR = Path('/workspace/atlas-distillation/data/lora_datasets_expanded')
OUT_DIR.mkdir(parents=True, exist_ok=True)

def save_jsonl(data: list[dict], path: Path):
    with open(path, 'w') as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(f'  Saved {len(data)} rows to {path.name}')

# ============================================================
# MEDICAL — Target: 200K+
# ============================================================
def download_medical():
    print('\n=== MEDICAL ===')
    rows = []
    
    # MedMCQA — 194K clinical MCQ with explanations
    print('Loading MedMCQA (194K)...')
    try:
        ds = load_dataset('openlifescienceai/medmcqa', split='train')
        for ex in ds:
            q = ex.get('question', '')
            opts = f"A) {ex.get('opa','')} B) {ex.get('opb','')} C) {ex.get('opc','')} D) {ex.get('opd','')}"
            cop = ex.get('cop', 0)
            labels = {0: 'A', 1: 'B', 2: 'C', 3: 'D'}
            answer = labels.get(cop, '?')
            exp = ex.get('exp', '') or ''
            if q and exp.strip():
                prompt = f'{q}\nOptions: {opts}'
                response = f'The correct answer is {answer}.\n\n{exp}'
                rows.append({'prompt': prompt, 'response': response})
        print(f'  MedMCQA with explanations: {len(rows)}')
    except Exception as e:
        print(f'  MedMCQA error: {e}')
    
    # MedQA — 12.7K USMLE board exam
    print('Loading MedQA...')
    try:
        ds = load_dataset('bigbio/med_qa', 'med_qa_en_4options_bigbio_qa', split='train', trust_remote_code=True)
        count = 0
        for ex in ds:
            q = ex.get('question', '')
            # bigbio format has choices and answer
            choices = ex.get('choices', [])
            answer = ex.get('answer', [''])[0] if ex.get('answer') else ''
            if q and answer:
                opts_str = ', '.join(choices) if choices else ''
                prompt = f'{q}' + (f'\nOptions: {opts_str}' if opts_str else '')
                rows.append({'prompt': prompt, 'response': answer})
                count += 1
        print(f'  MedQA: {count}')
    except Exception as e:
        print(f'  MedQA error: {e}')
    
    # Medical Meadow WikiDoc — 10K Q&A
    print('Loading medical_meadow_wikidoc...')
    try:
        ds = load_dataset('medalpaca/medical_meadow_wikidoc', split='train')
        count = 0
        for ex in ds:
            prompt = ex.get('input', '') or ex.get('instruction', '')
            response = ex.get('output', '')
            if prompt and response and len(response) > 50:
                rows.append({'prompt': prompt, 'response': response})
                count += 1
        print(f'  WikiDoc: {count}')
    except Exception as e:
        print(f'  WikiDoc error: {e}')
    
    # PubMedQA — biomedical research Q&A
    print('Loading PubMedQA...')
    try:
        ds = load_dataset('qiaojin/PubMedQA', 'pqa_artificial', split='train')
        count = 0
        for ex in ds:
            q = ex.get('question', '')
            answer = ex.get('long_answer', '')
            if q and answer and len(answer) > 50:
                rows.append({'prompt': q, 'response': answer})
                count += 1
                if count >= 20000:  # Cap at 20K — it's 211K total
                    break
        print(f'  PubMedQA: {count}')
    except Exception as e:
        print(f'  PubMedQA error: {e}')
    
    save_jsonl(rows, OUT_DIR / 'medical_expanded.jsonl')

# ============================================================
# AI/ML — Target: 5K+ (filter from large general datasets)
# ============================================================
def download_ai_ml():
    print('\n=== AI/ML ===')
    ai_keywords = [
        'machine learning', 'deep learning', 'neural network', 'artificial intelligence',
        'training data', 'model', 'transformer', 'attention mechanism', 'gradient descent',
        'backpropagation', 'convolutional', 'recurrent', 'LSTM', 'GAN', 'generative',
        'reinforcement learning', 'supervised learning', 'unsupervised', 'clustering',
        'classification', 'regression', 'overfitting', 'underfitting', 'hyperparameter',
        'fine-tuning', 'fine tuning', 'LoRA', 'quantization', 'distillation',
        'embedding', 'tokenizer', 'LLM', 'large language model', 'GPT', 'BERT',
        'natural language processing', 'NLP', 'computer vision', 'CNN',
        'data science', 'feature engineering', 'cross-validation', 'bias-variance',
        'random forest', 'decision tree', 'SVM', 'support vector', 'k-means',
        'dimensionality reduction', 'PCA', 'autoencoder', 'diffusion model',
        'pytorch', 'tensorflow', 'scikit-learn', 'hugging face', 'weights and biases',
    ]
    ai_pattern = re.compile('|'.join(re.escape(k) for k in ai_keywords), re.IGNORECASE)
    
    rows = []
    
    # Dolly-15K
    print('Loading Dolly-15K...')
    try:
        ds = load_dataset('databricks/databricks-dolly-15k', split='train')
        count = 0
        for ex in ds:
            text = (ex.get('instruction', '') + ' ' + ex.get('response', '')).lower()
            if ai_pattern.search(text):
                prompt = ex.get('instruction', '')
                ctx = ex.get('context', '')
                if ctx:
                    prompt = f'{prompt}\n\nContext: {ctx}'
                rows.append({'prompt': prompt, 'response': ex.get('response', '')})
                count += 1
        print(f'  Dolly AI/ML: {count}')
    except Exception as e:
        print(f'  Dolly error: {e}')
    
    # Alpaca
    print('Loading Alpaca...')
    try:
        ds = load_dataset('tatsu-lab/alpaca', split='train')
        count = 0
        for ex in ds:
            text = (ex.get('instruction', '') + ' ' + ex.get('output', '')).lower()
            if ai_pattern.search(text):
                prompt = ex.get('instruction', '')
                inp = ex.get('input', '')
                if inp:
                    prompt = f'{prompt}\n\nInput: {inp}'
                rows.append({'prompt': prompt, 'response': ex.get('output', '')})
                count += 1
        print(f'  Alpaca AI/ML: {count}')
    except Exception as e:
        print(f'  Alpaca error: {e}')
    
    # WizardLM Evol Instruct
    print('Loading WizardLM (70K)...')
    try:
        ds = load_dataset('WizardLMTeam/WizardLM_evol_instruct_70k', split='train')
        count = 0
        for ex in ds:
            text = (ex.get('instruction', '') + ' ' + ex.get('output', '')).lower()
            if ai_pattern.search(text):
                rows.append({'prompt': ex.get('instruction', ''), 'response': ex.get('output', '')})
                count += 1
        print(f'  WizardLM AI/ML: {count}')
    except Exception as e:
        print(f'  WizardLM error: {e}')
    
    # Self-Instruct
    print('Loading Self-Instruct...')
    try:
        ds = load_dataset('yizhongw/self_instruct', 'self_instruct', split='train')
        count = 0
        for ex in ds:
            prompt = ex.get('prompt', '')
            completion = ex.get('completion', '')
            if ai_pattern.search((prompt + ' ' + completion).lower()):
                rows.append({'prompt': prompt, 'response': completion})
                count += 1
        print(f'  Self-Instruct AI/ML: {count}')
    except Exception as e:
        print(f'  Self-Instruct error: {e}')
    
    # Deduplicate by prompt
    seen = set()
    unique = []
    for r in rows:
        key = r['prompt'][:200].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    rows = unique
    
    save_jsonl(rows, OUT_DIR / 'ai_ml_expanded.jsonl')

# ============================================================
# ENGINEERING — Target: 2K+
# ============================================================
def download_engineering():
    print('\n=== ENGINEERING ===')
    rows = []
    
    # STEM-AI Electrical Engineering
    print('Loading STEM-AI Electrical Engineering...')
    try:
        ds = load_dataset('STEM-AI-mtl/Electrical-engineering', split='train')
        count = 0
        for ex in ds:
            prompt = ex.get('question', '') or ex.get('instruction', '') or ex.get('input', '')
            response = ex.get('answer', '') or ex.get('output', '') or ex.get('response', '')
            if prompt and response:
                rows.append({'prompt': prompt, 'response': response})
                count += 1
        print(f'  EE dataset: {count}')
    except Exception as e:
        print(f'  EE error: {e}')
    
    # Filter engineering from Alpaca + Dolly + WizardLM
    eng_keywords = [
        'engineer', 'circuit', 'resistor', 'capacitor', 'voltage', 'current',
        'thermodynamics', 'fluid mechanics', 'structural', 'beam', 'stress', 'strain',
        'aerodynamic', 'turbine', 'combustion', 'heat transfer', 'conduction',
        'convection', 'radiation', 'hydraulic', 'pneumatic', 'gear', 'bearing',
        'weld', 'rivet', 'bolt', 'torque', 'power plant', 'renewable energy',
        'solar panel', 'wind turbine', 'motor', 'generator', 'transformer',
        'microcontroller', 'Arduino', 'Raspberry Pi', 'PLC', 'SCADA',
        'robotics', 'actuator', 'sensor', 'feedback control', 'PID',
        'CAD', 'manufacturing', 'CNC', '3D printing', 'material science',
        'alloy', 'polymer', 'composite', 'fatigue', 'fracture mechanics',
        'bridge', 'foundation', 'concrete', 'steel structure', 'civil engineer',
        'HVAC', 'piping', 'plumbing', 'water treatment', 'sewage',
        'telecommunications', 'signal processing', 'antenna', 'amplifier',
    ]
    eng_pattern = re.compile('|'.join(re.escape(k) for k in eng_keywords), re.IGNORECASE)
    
    print('Filtering engineering from Dolly, Alpaca, WizardLM...')
    for ds_name, inst_key, resp_key in [
        ('databricks/databricks-dolly-15k', 'instruction', 'response'),
        ('tatsu-lab/alpaca', 'instruction', 'output'),
    ]:
        try:
            ds = load_dataset(ds_name, split='train')
            count = 0
            for ex in ds:
                text = (ex.get(inst_key, '') + ' ' + ex.get(resp_key, '')).lower()
                if eng_pattern.search(text):
                    rows.append({'prompt': ex.get(inst_key, ''), 'response': ex.get(resp_key, '')})
                    count += 1
            print(f'  {ds_name} engineering: {count}')
        except Exception as e:
            print(f'  {ds_name} error: {e}')
    
    # Deduplicate
    seen = set()
    unique = []
    for r in rows:
        key = r['prompt'][:200].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    
    save_jsonl(unique, OUT_DIR / 'engineering_expanded.jsonl')

# ============================================================
# EARTH/SPACE — SciQ + ARC + filters
# ============================================================
def download_earth_space():
    print('\n=== EARTH/SPACE ===')
    rows = []
    earth_keywords = [
        'geology', 'tectonic', 'earthquake', 'volcano', 'mineral', 'rock',
        'sediment', 'erosion', 'weathering', 'fossil', 'stratigraphy',
        'atmosphere', 'climate', 'weather', 'precipitation', 'hurricane',
        'tornado', 'ocean', 'tide', 'current', 'glacier', 'ice age',
        'water cycle', 'aquifer', 'groundwater', 'watershed',
        'astronomy', 'planet', 'star', 'galaxy', 'solar system', 'nebula',
        'black hole', 'supernova', 'constellation', 'telescope', 'orbit',
        'moon', 'comet', 'asteroid', 'meteor', 'light year', 'parsec',
        'cosmology', 'big bang', 'dark matter', 'dark energy', 'red shift',
        'satellite', 'space station', 'NASA', 'Mars', 'Jupiter', 'Venus',
        'ecosystem', 'biome', 'habitat', 'biodiversity', 'conservation',
        'deforestation', 'greenhouse', 'carbon', 'ozone', 'pollution',
        'renewable', 'sustainability', 'environmental',
        'soil', 'lithosphere', 'hydrosphere', 'magnetosphere',
    ]
    earth_pattern = re.compile('|'.join(re.escape(k) for k in earth_keywords), re.IGNORECASE)
    
    # SciQ — 13.7K science Q&A
    print('Loading SciQ...')
    try:
        ds = load_dataset('allenai/sciq', split='train')
        count = 0
        for ex in ds:
            q = ex.get('question', '')
            answer = ex.get('correct_answer', '')
            support = ex.get('support', '')
            text = (q + ' ' + answer + ' ' + support).lower()
            if earth_pattern.search(text):
                response = answer
                if support:
                    response = f'{answer}\n\n{support}'
                rows.append({'prompt': q, 'response': response})
                count += 1
        print(f'  SciQ earth/space: {count}')
    except Exception as e:
        print(f'  SciQ error: {e}')
    
    # ARC — Science reasoning
    print('Loading ARC...')
    try:
        for subset in ['ARC-Challenge', 'ARC-Easy']:
            ds = load_dataset('allenai/ai2_arc', subset, split='train')
            count = 0
            for ex in ds:
                q = ex.get('question', '')
                choices = ex.get('choices', {})
                labels = choices.get('label', [])
                texts = choices.get('text', [])
                answer_key = ex.get('answerKey', '')
                text_full = q.lower()
                if earth_pattern.search(text_full):
                    answer_idx = labels.index(answer_key) if answer_key in labels else -1
                    answer = texts[answer_idx] if answer_idx >= 0 else answer_key
                    rows.append({'prompt': q, 'response': answer})
                    count += 1
            print(f'  ARC {subset} earth/space: {count}')
    except Exception as e:
        print(f'  ARC error: {e}')
    
    # Filter from Alpaca/Dolly
    for ds_name, inst_key, resp_key in [
        ('databricks/databricks-dolly-15k', 'instruction', 'response'),
        ('tatsu-lab/alpaca', 'instruction', 'output'),
    ]:
        try:
            ds = load_dataset(ds_name, split='train')
            count = 0
            for ex in ds:
                text = (ex.get(inst_key, '') + ' ' + ex.get(resp_key, '')).lower()
                if earth_pattern.search(text):
                    rows.append({'prompt': ex.get(inst_key, ''), 'response': ex.get(resp_key, '')})
                    count += 1
            print(f'  {ds_name} earth/space: {count}')
        except Exception as e:
            print(f'  {ds_name} error: {e}')
    
    seen = set()
    unique = []
    for r in rows:
        key = r['prompt'][:200].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    save_jsonl(unique, OUT_DIR / 'earth_space_expanded.jsonl')

# ============================================================
# SOCIAL SCIENCE — psychology, history, sociology
# ============================================================
def download_social_science():
    print('\n=== SOCIAL SCIENCE ===')
    rows = []
    social_keywords = [
        'psychology', 'cognitive', 'behavioral', 'therapy', 'mental health',
        'depression', 'anxiety', 'personality', 'motivation', 'emotion',
        'perception', 'memory', 'learning theory', 'conditioning',
        'Freud', 'Piaget', 'Maslow', 'Skinner', 'Jung',
        'sociology', 'social', 'culture', 'society', 'institution',
        'history', 'historical', 'ancient', 'medieval', 'renaissance',
        'revolution', 'war', 'empire', 'dynasty', 'civilization',
        'democracy', 'monarchy', 'republic', 'constitution', 'amendment',
        'economics', 'inflation', 'GDP', 'fiscal', 'monetary',
        'supply and demand', 'market', 'trade', 'capitalism', 'socialism',
        'philosophy', 'ethics', 'morality', 'epistemology', 'metaphysics',
        'political science', 'government', 'legislation', 'policy',
        'anthropology', 'archaeology', 'ethnography',
    ]
    social_pattern = re.compile('|'.join(re.escape(k) for k in social_keywords), re.IGNORECASE)
    
    # SciQ for social science overlap
    print('Loading SciQ for social science...')
    try:
        ds = load_dataset('allenai/sciq', split='train')
        count = 0
        for ex in ds:
            q = ex.get('question', '')
            answer = ex.get('correct_answer', '')
            support = ex.get('support', '')
            text = (q + ' ' + answer + ' ' + support).lower()
            if social_pattern.search(text):
                response = answer + ('\n\n' + support if support else '')
                rows.append({'prompt': q, 'response': response})
                count += 1
        print(f'  SciQ social: {count}')
    except Exception as e:
        print(f'  SciQ error: {e}')
    
    # Filter from Alpaca/Dolly
    for ds_name, inst_key, resp_key in [
        ('databricks/databricks-dolly-15k', 'instruction', 'response'),
        ('tatsu-lab/alpaca', 'instruction', 'output'),
    ]:
        try:
            ds = load_dataset(ds_name, split='train')
            count = 0
            for ex in ds:
                text = (ex.get(inst_key, '') + ' ' + ex.get(resp_key, '')).lower()
                if social_pattern.search(text):
                    rows.append({'prompt': ex.get(inst_key, ''), 'response': ex.get(resp_key, '')})
                    count += 1
            print(f'  {ds_name} social: {count}')
        except Exception as e:
            print(f'  {ds_name} error: {e}')
    
    seen = set()
    unique = []
    for r in rows:
        key = r['prompt'][:200].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    save_jsonl(unique, OUT_DIR / 'social_science_expanded.jsonl')

# ============================================================
# CREATIVE ARTS — writing, film, music
# ============================================================
def download_creative_arts():
    print('\n=== CREATIVE ARTS ===')
    rows = []
    
    # Creative Writing Q&A dataset
    print('Loading creative_writing...')
    try:
        ds = load_dataset('lukehinds/creative_writing', split='train')
        count = 0
        for ex in ds:
            # Check format
            if 'conversations' in ex:
                convs = ex['conversations']
                for i in range(0, len(convs)-1, 2):
                    if convs[i].get('from') == 'human' and convs[i+1].get('from') in ('gpt', 'assistant'):
                        rows.append({'prompt': convs[i]['value'], 'response': convs[i+1]['value']})
                        count += 1
            elif 'instruction' in ex:
                rows.append({'prompt': ex['instruction'], 'response': ex.get('output', '')})
                count += 1
        print(f'  creative_writing: {count}')
    except Exception as e:
        print(f'  creative_writing error: {e}')
    
    # Filter creative from Alpaca/Dolly
    creative_keywords = [
        'story', 'poem', 'creative writing', 'fiction', 'novel', 'screenplay',
        'screenwriting', 'dialogue', 'character', 'plot', 'narrative',
        'film', 'movie', 'director', 'cinematography', 'editing',
        'music', 'melody', 'harmony', 'chord', 'rhythm', 'tempo',
        'composition', 'song', 'genre', 'orchestra', 'instrument',
        'game design', 'game mechanic', 'level design', 'game development',
        'art', 'painting', 'sculpture', 'photography', 'animation',
        'theater', 'drama', 'comedy', 'tragedy', 'acting',
        'literary', 'metaphor', 'symbolism', 'irony', 'allegory',
        'haiku', 'sonnet', 'rhyme', 'verse', 'stanza',
    ]
    creative_pattern = re.compile('|'.join(re.escape(k) for k in creative_keywords), re.IGNORECASE)
    
    for ds_name, inst_key, resp_key in [
        ('databricks/databricks-dolly-15k', 'instruction', 'response'),
        ('tatsu-lab/alpaca', 'instruction', 'output'),
    ]:
        try:
            ds = load_dataset(ds_name, split='train')
            count = 0
            for ex in ds:
                text = (ex.get(inst_key, '') + ' ' + ex.get(resp_key, '')).lower()
                if creative_pattern.search(text):
                    rows.append({'prompt': ex.get(inst_key, ''), 'response': ex.get(resp_key, '')})
                    count += 1
            print(f'  {ds_name} creative: {count}')
        except Exception as e:
            print(f'  {ds_name} error: {e}')
    
    seen = set()
    unique = []
    for r in rows:
        key = r['prompt'][:200].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    save_jsonl(unique, OUT_DIR / 'creative_arts_expanded.jsonl')

# ============================================================
# ALSO BOOST: coding, math, biology, physics/chem
# ============================================================
def download_extra_stem():
    print('\n=== EXTRA STEM (coding, math boost) ===')
    
    # SciQ for biology/physics/chem boost
    print('Loading SciQ for bio/phys/chem boost...')
    bio_rows, phys_rows = [], []
    bio_kw = re.compile(r'cell|DNA|RNA|protein|gene|enzyme|organism|species|evolution|ecology|photosynthesis|mitosis|meiosis|chromosome|mutation|bacteria|virus|immune|anatomy|organ|tissue|plant|animal|fungi|microb', re.IGNORECASE)
    phys_kw = re.compile(r'force|energy|momentum|velocity|acceleration|gravity|electric|magnetic|wave|frequency|light|optic|thermodynamic|entropy|quantum|atom|molecule|ion|bond|reaction|acid|base|pH|oxidation|reduction|electron|proton|neutron|nuclear', re.IGNORECASE)
    
    try:
        ds = load_dataset('allenai/sciq', split='train')
        for ex in ds:
            q = ex.get('question', '')
            answer = ex.get('correct_answer', '')
            support = ex.get('support', '')
            text = q + ' ' + answer + ' ' + support
            response = answer + ('\n\n' + support if support else '')
            row = {'prompt': q, 'response': response}
            if bio_kw.search(text):
                bio_rows.append(row)
            elif phys_kw.search(text):
                phys_rows.append(row)
        print(f'  SciQ biology: {len(bio_rows)}, physics/chem: {len(phys_rows)}')
    except Exception as e:
        print(f'  SciQ error: {e}')
    
    if bio_rows:
        save_jsonl(bio_rows, OUT_DIR / 'biology_sciq_boost.jsonl')
    if phys_rows:
        save_jsonl(phys_rows, OUT_DIR / 'physics_chem_sciq_boost.jsonl')

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    import sys
    targets = sys.argv[1:] if len(sys.argv) > 1 else ['medical', 'ai_ml', 'engineering', 'earth_space', 'social_science', 'creative_arts', 'stem']
    
    if 'medical' in targets:
        download_medical()
    if 'ai_ml' in targets:
        download_ai_ml()
    if 'engineering' in targets:
        download_engineering()
    if 'earth_space' in targets:
        download_earth_space()
    if 'social_science' in targets:
        download_social_science()
    if 'creative_arts' in targets:
        download_creative_arts()
    if 'stem' in targets:
        download_extra_stem()
    
    print('\n=== ALL DOWNLOADS COMPLETE ===')
    import subprocess
    result = subprocess.run(['wc', '-l'] + [str(p) for p in sorted(OUT_DIR.glob('*.jsonl'))], capture_output=True, text=True)
    print(result.stdout)
