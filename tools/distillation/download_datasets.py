"""Download free datasets for LoRA training."""
import json, os
from datasets import load_dataset

OUT = '/workspace/atlas-distillation/data/lora_datasets'
os.makedirs(OUT, exist_ok=True)

def save_jsonl(data, path):
    with open(path, 'w') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    print(f'  Saved {len(data)} rows to {path}')

# 1. Medical O1 reasoning (English split)
print('Downloading medical-o1-reasoning-SFT...')
try:
    ds = load_dataset('FreedomIntelligence/medical-o1-reasoning-SFT', 'en', split='train')
    rows = [{'prompt': r['Question'], 'response': r['Response'], 'cot': r['Complex_CoT']} for r in ds]
    save_jsonl(rows, f'{OUT}/medical_o1.jsonl')
except Exception as e:
    print(f'  ERROR: {e}')

# 2. CAMEL-AI physics
print('Downloading camel-ai/physics...')
try:
    ds = load_dataset('camel-ai/physics', split='train')
    rows = [{'prompt': r['message_1'], 'response': r['message_2']} for r in ds if r.get('message_1')]
    save_jsonl(rows[:5000], f'{OUT}/camel_physics.jsonl')
except Exception as e:
    print(f'  ERROR: {e}')

# 3. CAMEL-AI chemistry
print('Downloading camel-ai/chemistry...')
try:
    ds = load_dataset('camel-ai/chemistry', split='train')
    rows = [{'prompt': r['message_1'], 'response': r['message_2']} for r in ds if r.get('message_1')]
    save_jsonl(rows[:5000], f'{OUT}/camel_chemistry.jsonl')
except Exception as e:
    print(f'  ERROR: {e}')

# 4. CAMEL-AI biology
print('Downloading camel-ai/biology...')
try:
    ds = load_dataset('camel-ai/biology', split='train')
    rows = [{'prompt': r['message_1'], 'response': r['message_2']} for r in ds if r.get('message_1')]
    save_jsonl(rows[:5000], f'{OUT}/camel_biology.jsonl')
except Exception as e:
    print(f'  ERROR: {e}')

# 5. NuminaMath-CoT (sample — full dataset is 800K+)
print('Downloading NuminaMath-CoT (sampling 5K)...')
try:
    ds = load_dataset('AI-MO/NuminaMath-CoT', split='train')
    rows = [{'prompt': r['problem'], 'response': r['solution']} for r in ds]
    # Take diverse sample
    import random
    random.seed(42)
    sample = random.sample(rows, min(5000, len(rows)))
    save_jsonl(sample, f'{OUT}/numina_math.jsonl')
except Exception as e:
    print(f'  ERROR: {e}')

# 6. Magicoder OSS Instruct
print('Downloading Magicoder-OSS-Instruct (sampling 5K)...')
try:
    ds = load_dataset('ise-uiuc/Magicoder-OSS-Instruct-75K', split='train')
    rows = [{'prompt': r['problem'], 'response': r['solution']} for r in ds]
    import random
    random.seed(42)
    sample = random.sample(rows, min(5000, len(rows)))
    save_jsonl(sample, f'{OUT}/magicoder_oss.jsonl')
except Exception as e:
    print(f'  ERROR: {e}')

print('\nAll downloads complete!')
import subprocess
result = subprocess.run(['wc', '-l'] + [f'{OUT}/{f}' for f in os.listdir(OUT) if f.endswith('.jsonl')], capture_output=True, text=True)
print(result.stdout)
