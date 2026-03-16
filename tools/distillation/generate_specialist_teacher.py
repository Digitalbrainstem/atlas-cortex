"""Generate specialist teacher data using Qwen3.5-122B.
Optimized: shorter responses, reduced targets for domains with existing datasets."""
from __future__ import annotations
import argparse, asyncio, json, os, time, random
from pathlib import Path
import aiohttp

API_URL = 'http://localhost:8000/v1/chat/completions'
MODEL = 'QuantTrio/Qwen3.5-122B-A10B-AWQ'
OUT_DIR = Path('/workspace/atlas-distillation/data/specialist_teacher')
CONCURRENT = 16

SYSTEM_PROMPTS = {
    'medicine': 'You are an expert physician and medical researcher. Provide detailed, evidence-based answers. Be thorough but concise — target 400-600 words.',
    'coding': 'You are an expert software architect and systems engineer. Provide production-quality code with clear explanations. Be thorough but concise — target 400-600 words for explanations, plus code.',
    'math_reasoning': 'You are an expert mathematician. Show complete proofs and derivations. Be rigorous but concise — target 400-600 words.',
    'ai_ml': 'You are an expert ML researcher with deep knowledge of architectures, training techniques, and latest advances. Be precise and technical. Target 400-600 words.',
    'physics_chemistry': 'You are an expert physicist and chemist. Provide rigorous explanations with math where appropriate. Target 300-500 words.',
    'biology_biomed': 'You are an expert biologist and biomedical engineer. Explain mechanisms clearly with current understanding. Target 300-500 words.',
    'engineering': 'You are an expert engineer spanning aerospace, mechanical, chemical, electrical, and mechatronics. Provide practical technical answers. Target 300-500 words.',
    'earth_space': 'You are a knowledgeable earth scientist and astronomer. Explain phenomena clearly. Target 300-400 words.',
    'social_science': 'You are a knowledgeable psychologist and historian. Provide nuanced perspectives. Target 300-400 words.',
    'creative_arts': 'You are a knowledgeable expert in film, music, game design, and creative writing. Provide insightful analysis. Target 300-400 words.',
    'agriculture_animals': 'You are a knowledgeable agricultural scientist, master gardener, and veterinary-informed pet care expert. Provide practical, science-based advice. Target 300-400 words.',
}

# Reduced targets — domains with existing free datasets need fewer supplements
TARGETS = {
    'medicine': 500,         # has 19.7K from medical-o1-SFT
    'coding': 500,           # has Magicoder-OSS
    'math_reasoning': 500,   # has NuminaMath-CoT
    'ai_ml': 700,            # no existing dataset
    'physics_chemistry': 300, # has CAMEL-AI
    'biology_biomed': 300,    # has CAMEL-AI
    'engineering': 500,       # no existing dataset
    'earth_space': 400,
    'social_science': 400,
    'creative_arts': 400,
    'agriculture_animals': 400,
}

async def generate_one(session: aiohttp.ClientSession, prompt: str, system: str, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        payload = {
            'model': MODEL,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': prompt}
            ],
            'max_tokens': 2048,
            'temperature': 0.7,
            'top_p': 0.9,
            'presence_penalty': 1.2,
            'chat_template_kwargs': {'enable_thinking': False}
        }
        for attempt in range(3):
            try:
                async with session.post(API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        msg = data['choices'][0]['message']
                        content = msg.get('content') or ''
                        reasoning = msg.get('reasoning') or ''
                        response = content if content else reasoning
                        if not response.strip():
                            return None
                        return {'prompt': prompt, 'response': response, 'thinking': ''}
                    else:
                        text = await resp.text()
                        print(f'  HTTP {resp.status}: {text[:100]}')
                        await asyncio.sleep(2 ** attempt)
            except Exception as e:
                print(f'  Error: {e}')
                await asyncio.sleep(2 ** attempt)
    return None

async def generate_domain(domain: str, prompts: list[str], system: str):
    out_path = OUT_DIR / f'teacher_{domain}.jsonl'
    done = set()
    if out_path.exists():
        with open(out_path) as f:
            for line in f:
                done.add(json.loads(line)['prompt'])
    remaining = [p for p in prompts if p not in done]
    if not remaining:
        print(f'  {domain}: already complete ({len(prompts)} done)')
        return
    print(f'  {domain}: {len(remaining)} remaining (of {len(prompts)})')
    
    sem = asyncio.Semaphore(CONCURRENT)
    async with aiohttp.ClientSession() as session:
        tasks = [generate_one(session, p, system, sem) for p in remaining]
        completed = 0
        with open(out_path, 'a') as f:
            for coro in asyncio.as_completed(tasks):
                result = await coro
                if result:
                    f.write(json.dumps(result, ensure_ascii=False) + '\n')
                    f.flush()
                    completed += 1
                    if completed % 25 == 0:
                        print(f'    {domain}: {completed}/{len(remaining)}')
    print(f'  {domain}: DONE ({completed} generated)')

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--domains', nargs='+', help='Domains to generate')
    args = parser.parse_args()
    
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    
    PROMPT_DIR = Path('/workspace/atlas-distillation/data/specialist')
    
    DOMAIN_FILES = {
        'medicine': ['prompts_medicine.jsonl', 'prompts_medical.jsonl'],
        'coding': ['prompts_computer_science.jsonl'],
        'math_reasoning': ['prompts_math.jsonl', 'prompts_reasoning_strategy.jsonl'],
        'ai_ml': ['prompts_ai_ml.jsonl'],
        'physics_chemistry': ['prompts_physics.jsonl', 'prompts_chemistry.jsonl'],
        'biology_biomed': ['prompts_biology.jsonl', 'prompts_biomedical_eng.jsonl'],
        'engineering': ['prompts_aerospace.jsonl', 'prompts_mechanical.jsonl', 'prompts_chemical_eng.jsonl', 'prompts_electrical_eng.jsonl', 'prompts_mechatronics.jsonl'],
        'earth_space': ['prompts_earth_environmental.jsonl', 'prompts_astronomy.jsonl'],
        'social_science': ['prompts_psychology.jsonl', 'prompts_history.jsonl'],
        'creative_arts': ['prompts_film.jsonl', 'prompts_music.jsonl', 'prompts_game_design.jsonl', 'prompts_language_arts.jsonl'],
        'agriculture_animals': [],
    }
    
    domains_to_run = args.domains or list(DOMAIN_FILES.keys())
    
    for domain in domains_to_run:
        if domain not in DOMAIN_FILES:
            print(f'Unknown domain: {domain}')
            continue
        
        target = TARGETS.get(domain, 400)
        system = SYSTEM_PROMPTS[domain]
        
        all_prompts = []
        for fname in DOMAIN_FILES[domain]:
            fpath = PROMPT_DIR / fname
            if fpath.exists():
                with open(fpath) as f:
                    for line in f:
                        row = json.loads(line)
                        all_prompts.append(row['prompt'])
        
        if domain == 'agriculture_animals' and not all_prompts:
            topics = [
                'companion planting strategies for vegetable gardens',
                'soil pH management and amendment techniques',
                'integrated pest management for home gardens',
                'composting methods: hot vs cold vs vermicomposting',
                'raised bed garden design and soil mix ratios',
                'seasonal planting calendars by USDA hardiness zone',
                'fruit tree pruning techniques and timing',
                'water conservation and drip irrigation design',
                'organic fertilizer types and application rates',
                'seed starting indoors: lighting, timing, hardening off',
                'herb garden planning: culinary and medicinal herbs',
                'plant disease identification and organic treatment',
                'pollinator garden design and native plant selection',
                'container gardening for balconies and small spaces',
                'foods toxic to dogs: chocolate, grapes, onions, xylitol, garlic',
                'foods toxic to cats: lilies, onions, chocolate, essential oils',
                'common dog health issues: allergies, joint pain, dental care, ear infections',
                'common cat health issues: urinary problems, hairballs, dental disease',
                'pet vaccination schedules for dogs and cats',
                'recognizing pet emergencies: bloat, poisoning, heatstroke, seizures',
                'basic pet first aid and when to see a vet',
                'pet nutrition: reading labels, raw vs kibble, age-appropriate feeding',
                'backyard chicken keeping: coops, feed, egg production, health',
                'basic livestock care: goats, rabbits, bees for small homesteads',
                'aquarium and fish care: freshwater setup, cycling, common diseases',
            ]
            templates = [
                'Explain {topic} in detail. What are the key principles, common mistakes, and best practices?',
                'I am a beginner. Walk me through {topic} step by step.',
                'Compare different approaches to {topic}. What works best in different climates?',
                'What does the latest research say about {topic}? Any new techniques?',
                'Troubleshoot common problems with {topic}. Signs of issues and how to fix them?',
                'Design a practical plan for {topic} on a suburban quarter-acre lot.',
                'Explain the science behind {topic}. Cover the biology and chemistry involved.',
            ]
            for topic in topics:
                for tmpl in templates:
                    all_prompts.append(tmpl.format(topic=topic))
        
        random.seed(42)
        if len(all_prompts) > target:
            prompts = random.sample(all_prompts, target)
        else:
            prompts = all_prompts[:target]
        
        print(f'\n=== {domain} ({len(prompts)} prompts, target {target}) ===')
        await generate_domain(domain, prompts, system)

if __name__ == '__main__':
    asyncio.run(main())
