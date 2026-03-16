"""Generate DPO preference pairs for Atlas personality alignment.
Creates chosen (warm, concise, Atlas-style) vs rejected (generic, verbose) pairs."""
from __future__ import annotations
import asyncio, json, random
from pathlib import Path
import aiohttp

API_URL = 'http://localhost:8000/v1/chat/completions'
MODEL = 'QuantTrio/Qwen3.5-122B-A10B-AWQ'
OUT_PATH = Path('/workspace/atlas-distillation/data/dpo_pairs.jsonl')
CONCURRENT = 8

CHOSEN_SYSTEM = (
    'You are Atlas, a warm and helpful personal AI assistant. Your personality traits:\n'
    '- Friendly, approachable, and conversational (not robotic or corporate)\n'
    '- Concise and direct — respect the users time, aim for 50-150 words unless detail is needed\n'
    '- Proactive — anticipate follow-up needs and offer relevant extras\n'
    '- Honest about uncertainty — say "I am not sure" rather than hallucinate\n'
    '- Use natural speech patterns, light humor when appropriate\n'
    '- Never start with "Certainly!", "Of course!", "Great question!", or similar filler\n'
    '- Address the user directly and warmly\n'
    '- For technical topics, be precise but accessible\n'
    '- For personal/emotional topics, be empathetic and supportive'
)

REJECTED_SYSTEM = (
    'You are a generic AI assistant. Respond in a formal, verbose manner. '
    'Start with phrases like "Certainly!" or "Great question!". '
    'Be thorough to the point of being unnecessarily long. '
    'Use corporate language and hedge everything. '
    'Do not show personality. Structure everything with excessive headers and bullet points.'
)

PROMPTS = [
    # Casual conversation
    "Hey, how's it going?",
    "Good morning!",
    "I'm bored, any ideas?",
    "Tell me something interesting",
    "What should I have for dinner tonight?",
    "I had a rough day at work",
    "I'm feeling stressed out lately",
    "Can you tell me a joke?",
    "What's a fun fact I can share at a party?",
    "I can't sleep, any tips?",

    # Knowledge questions (college-grad level)
    "Why is the sky blue?",
    "How does WiFi actually work?",
    "What causes thunder and lightning?",
    "How do vaccines work?",
    "Why do we dream?",
    "How does GPS know where I am?",
    "What's the difference between a virus and bacteria?",
    "How do airplanes stay in the air?",
    "Why do leaves change color in fall?",
    "How does a refrigerator work?",
    "What is quantum computing in simple terms?",
    "Why is the ocean salty?",
    "How do magnets work?",
    "What causes earthquakes?",
    "How does the stock market work?",
    "Why do we have different blood types?",
    "How does memory work in the brain?",
    "What's the difference between weather and climate?",
    "How do solar panels convert sunlight to electricity?",
    "Why do we yawn?",

    # Practical help
    "How do I remove a red wine stain from a white shirt?",
    "What's the best way to organize my closet?",
    "How do I change a flat tire?",
    "Tips for meal prepping on Sunday?",
    "How do I fix a running toilet?",
    "Best way to get rid of fruit flies?",
    "How do I improve my credit score?",
    "Tips for negotiating a raise?",
    "How do I start a budget?",
    "What should I look for when buying a used car?",
    "How do I unclog a drain without chemicals?",
    "Best way to remove ice from a windshield?",
    "How do I hang a heavy picture frame?",
    "Tips for reducing my electric bill?",
    "How do I write a good resume?",

    # Home assistant style
    "Set a timer for 15 minutes",
    "What's the weather going to be like today?",
    "Remind me to take out the trash tonight",
    "Add milk and eggs to my shopping list",
    "What time does Target close?",
    "Turn off the living room lights",
    "Play some relaxing music",
    "What's on my calendar tomorrow?",
    "Set the thermostat to 72",
    "Is the front door locked?",

    # Cooking
    "How do I make scrambled eggs?",
    "What can I make with chicken, rice, and broccoli?",
    "How long do I cook a turkey per pound?",
    "What's a simple pasta sauce from scratch?",
    "How do I know when steak is medium rare?",
    "Substitute for buttermilk in a recipe?",
    "How do I make bread from scratch?",
    "What's the difference between baking soda and baking powder?",
    "How do I properly season a cast iron skillet?",
    "Quick weeknight dinner ideas for a family of four?",

    # Health and wellness
    "How much water should I drink per day?",
    "Is cracking my knuckles bad for me?",
    "What are the benefits of stretching?",
    "How do I start running if I'm out of shape?",
    "What should I eat before a workout?",
    "How many hours of sleep do I need?",
    "Is it okay to exercise with a cold?",
    "What are some good stress-relief techniques?",
    "How do I improve my posture?",
    "What causes headaches and how can I prevent them?",

    # Technology
    "My phone battery drains fast, what can I do?",
    "How do I set up a VPN?",
    "What's the difference between 2.4GHz and 5GHz WiFi?",
    "How do I back up my photos?",
    "Is it safe to use public WiFi?",
    "How do I free up storage on my computer?",
    "What's a good password strategy?",
    "How do I connect my Bluetooth speaker?",
    "My internet is slow, what should I check?",
    "How do I transfer data to a new phone?",

    # Parenting and family
    "My toddler won't eat vegetables, any tricks?",
    "How do I explain death to a young child?",
    "Tips for potty training?",
    "Good bedtime routine for a 5 year old?",
    "How do I help my kid with math homework?",

    # Pets
    "My dog is scratching a lot, what could it be?",
    "How often should I take my cat to the vet?",
    "Can dogs eat peanut butter?",
    "My cat keeps knocking things off tables, why?",
    "How do I introduce a new puppy to my older dog?",

    # Edge cases — honesty and boundaries
    "What will the stock market do tomorrow?",
    "Should I take this medication my friend recommended?",
    "Can you diagnose this rash for me?",
    "Who's going to win the election?",
    "Is this mole on my arm cancerous?",

    # Opinions and recommendations
    "What's a good book to read?",
    "Best Netflix shows right now?",
    "Should I get a cat or a dog?",
    "What's a good beginner hobby?",
    "Best indoor plants that are hard to kill?",

    # Multi-turn style
    "Actually, can you explain that in simpler terms?",
    "Wait, go back — what did you mean by that?",
    "Can you give me an example?",
    "That's helpful, but what about the cost?",
    "Okay, so what should I do first?",

    # Emotional intelligence
    "I just got promoted!",
    "My dog passed away yesterday",
    "I'm nervous about a job interview tomorrow",
    "I think I messed up at work",
    "I'm thinking about going back to school",
    "My friend is going through a tough time, how can I help?",
    "I accomplished something I've been working on for months",
    "I'm having trouble making friends in a new city",

    # Quirky/fun
    "If you could have a superpower, what would it be?",
    "What's the most useless fact you know?",
    "Explain the internet to someone from the 1800s",
    "If aliens visited Earth, what would surprise them most?",
    "What would you name a pet rock?",

    # Home/garden
    "When should I plant tomatoes?",
    "How do I get rid of weeds without chemicals?",
    "My houseplant leaves are turning yellow, help!",
    "What flowers attract butterflies?",
    "How do I compost in a small apartment?",

    # Safety and emergency
    "What do I do if someone is choking?",
    "How do I turn off the water main in my house?",
    "What should be in a home emergency kit?",
    "My power went out, what should I check first?",
    "I smell gas in my house, what do I do?",

    # Travel
    "Tips for packing light for a week trip?",
    "How do I deal with jet lag?",
    "What should I know before traveling internationally?",
    "Best way to find cheap flights?",
    "How do I stay safe while traveling alone?",
]


async def generate_response(session, prompt, system, sem):
    async with sem:
        payload = {
            'model': MODEL,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': prompt}
            ],
            'max_tokens': 1024,
            'temperature': 0.7,
            'top_p': 0.9,
            'chat_template_kwargs': {'enable_thinking': False}
        }
        for attempt in range(3):
            try:
                async with session.post(API_URL, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=180)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data['choices'][0]['message'].get('content') or ''
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                print(f'  Error: {e}')
                await asyncio.sleep(2 ** attempt)
    return None


async def generate_pair(session, prompt, sem):
    chosen = await generate_response(session, prompt, CHOSEN_SYSTEM, sem)
    rejected = await generate_response(session, prompt, REJECTED_SYSTEM, sem)
    if chosen and rejected and chosen.strip() and rejected.strip():
        return {
            'prompt': prompt,
            'chosen': chosen.strip(),
            'rejected': rejected.strip()
        }
    return None


async def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    done = set()
    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            for line in f:
                done.add(json.loads(line)['prompt'])

    remaining = [p for p in PROMPTS if p not in done]
    print(f'Total: {len(PROMPTS)}, done: {len(done)}, remaining: {len(remaining)}')

    if not remaining:
        print('All done!')
        return

    sem = asyncio.Semaphore(CONCURRENT)
    async with aiohttp.ClientSession() as session:
        tasks = [generate_pair(session, p, sem) for p in remaining]
        completed = 0
        with open(OUT_PATH, 'a') as f:
            for coro in asyncio.as_completed(tasks):
                result = await coro
                if result:
                    f.write(json.dumps(result, ensure_ascii=False) + '\n')
                    f.flush()
                    completed += 1
                    if completed % 25 == 0:
                        print(f'  Progress: {completed}/{len(remaining)}')
    print(f'DONE: {completed} pairs generated')


if __name__ == '__main__':
    asyncio.run(main())
