"""Generate action-tag training data for Layer 2.5.
Teaches the model to emit [ACTION:plugin:entity:command:value] tags."""
from __future__ import annotations
import asyncio, json, os, random
from pathlib import Path
import aiohttp

API_URL = 'http://localhost:8000/v1/chat/completions'
MODEL = 'QuantTrio/Qwen3.5-122B-A10B-AWQ'
OUT = Path('/workspace/atlas-distillation/data/action_tags.jsonl')
CONCURRENT = 16

SYSTEM = '''You are Atlas, a personal AI assistant. When the user asks you to control devices, set reminders, manage lists, or perform actions, you MUST include action tags in your response using this exact format:

[ACTION:plugin_name:entity:command:value]

Available plugins and their formats:
- Home control: [ACTION:hass:entity_id:command:value] (e.g., [ACTION:hass:light.living_room:turn_on:brightness=80])
- Lists: [ACTION:lists:list_name:add:item] or [ACTION:lists:list_name:remove:item]
- Timers: [ACTION:timer:name:set:duration] (e.g., [ACTION:timer:pasta:set:10m])
- Reminders: [ACTION:reminder:name:set:time and message]
- Weather: [ACTION:weather:location:forecast:days]
- Music: [ACTION:music:source:play:query] or [ACTION:music:source:pause:]

Always respond naturally AND include the action tag. The tag can appear anywhere in your response.'''

# Diverse user prompts that should trigger action tags
PROMPTS = [
    # Home control
    "Turn on the living room lights",
    "Set the bedroom lights to 50%",
    "Turn off all the lights downstairs",
    "Make the kitchen lights warm white",
    "Is the garage door open?",
    "Lock the front door",
    "Set the thermostat to 72 degrees",
    "Turn on the fan in the bedroom",
    "Close the blinds in the office",
    "Turn on the porch light",
    "Dim the dining room lights to 30%",
    "Switch the living room to movie mode",
    # Lists
    "Add milk to the shopping list",
    "Put eggs and bread on the grocery list",
    "Remove bananas from the shopping list",
    "What's on my todo list?",
    "Add call the dentist to my tasks",
    "Add laundry detergent to the shopping list",
    # Timers
    "Set a timer for 15 minutes",
    "Start a 10 minute pasta timer",
    "Set a 30 second timer",
    "Timer for 2 hours for the roast",
    # Reminders
    "Remind me to take the trash out at 8pm",
    "Set a reminder for my meeting at 3pm tomorrow",
    "Remind me to call mom on Sunday",
    # Weather
    "What's the weather going to be like tomorrow?",
    "Will it rain this weekend?",
    "What's the forecast for the next 5 days?",
    # Music
    "Play some jazz music",
    "Put on Bohemian Rhapsody",
    "Play my workout playlist",
    "Pause the music",
    "Play something relaxing",
    # Multi-action
    "Good night, turn off all lights and lock the doors",
    "I'm leaving, lock up and set the thermostat to 65",
    "Movie time! Dim the lights and close the blinds",
    "I'm cooking dinner, set a timer for 20 minutes and add chicken to the shopping list",
    # Conversational with action
    "It's getting cold in here",
    "I can't see anything in here",
    "The baby is sleeping, can you keep it quiet?",
    "I need to wake up early tomorrow",
    # Edge cases - should NOT have action tags
    "What is photosynthesis?",
    "Tell me a joke",
    "How do I make pasta from scratch?",
    "What year did World War 2 end?",
    "Can you explain quantum physics simply?",
    "What's the meaning of life?",
]

# Rephrasings to multiply data
REPHRASE_TEMPLATES = [
    "Hey Atlas, {}",
    "Could you {}",
    "Please {}",
    "{} please",
    "Can you {}",
    "I need you to {}",
    "Go ahead and {}",
    "{} for me",
    "Atlas, {}",
    "{} when you get a chance",
]

def expand_prompts(base_prompts):
    expanded = list(base_prompts)
    for p in base_prompts:
        for tmpl in REPHRASE_TEMPLATES:
            try:
                expanded.append(tmpl.format(p.lower()))
            except:
                pass
    random.seed(42)
    random.shuffle(expanded)
    return expanded[:2000]

async def generate_one(session, prompt, sem):
    async with sem:
        payload = {
            'model': MODEL,
            'messages': [
                {'role': 'system', 'content': SYSTEM},
                {'role': 'user', 'content': prompt}
            ],
            'max_tokens': 512,
            'temperature': 0.7,
        }
        for attempt in range(3):
            try:
                async with session.post(API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        response = data['choices'][0]['message']['content']
                        has_action = '[ACTION:' in response
                        return {'prompt': prompt, 'response': response, 'has_action': has_action}
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                await asyncio.sleep(2 ** attempt)
    return None

async def main():
    prompts = expand_prompts(PROMPTS)
    print(f'Generating {len(prompts)} action-tag training examples...')
    
    sem = asyncio.Semaphore(CONCURRENT)
    completed = 0
    async with aiohttp.ClientSession() as session:
        tasks = [generate_one(session, p, sem) for p in prompts]
        with open(OUT, 'w') as f:
            for coro in asyncio.as_completed(tasks):
                result = await coro
                if result:
                    f.write(json.dumps(result, ensure_ascii=False) + '\n')
                    completed += 1
                    if completed % 100 == 0:
                        print(f'  {completed}/{len(prompts)}')
    
    # Stats
    with open(OUT) as f:
        rows = [json.loads(l) for l in f]
    with_action = sum(1 for r in rows if r['has_action'])
    print(f'\nDone! {len(rows)} total, {with_action} with actions, {len(rows)-with_action} without')

if __name__ == '__main__':
    asyncio.run(main())
