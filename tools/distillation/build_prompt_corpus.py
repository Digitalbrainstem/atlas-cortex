#!/usr/bin/env python3
"""Generate a diverse prompt corpus for teacher-student distillation.

Creates a JSONL file with categorized prompts covering:
- General knowledge (science, history, geography, culture)
- Conversation & assistant interaction
- Reasoning & logic
- Home/personal AI scenarios
- Safety & refusal patterns
- Follow-up / multi-turn patterns

Usage:
    python3 tools/distillation/build_prompt_corpus.py \
        --output data/distillation/prompts.jsonl \
        --count 15000
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

random.seed(42)

# ─── General Knowledge Templates ────────────────────────────────────────────
KNOWLEDGE_TOPICS = [
    # Science
    "How does photosynthesis work?",
    "What causes earthquakes?",
    "Explain the water cycle in simple terms.",
    "What is the difference between a virus and a bacteria?",
    "How do vaccines work?",
    "What is DNA and why is it important?",
    "Explain how electricity works.",
    "What causes the northern lights?",
    "How does gravity work?",
    "What is the theory of relativity in simple terms?",
    "How do antibiotics work?",
    "What is the difference between weather and climate?",
    "How do magnets work?",
    "Explain nuclear fusion and why it matters.",
    "What causes tides in the ocean?",
    "How do black holes form?",
    "What is quantum mechanics in layman's terms?",
    "How does the immune system fight infection?",
    "What is the greenhouse effect?",
    "How do computers store information?",
    # History
    "What caused World War I?",
    "Who was Cleopatra and why is she famous?",
    "What was the Industrial Revolution?",
    "Explain the fall of the Roman Empire.",
    "What was the significance of the Magna Carta?",
    "Who were the Vikings and where did they come from?",
    "What was the Renaissance?",
    "Explain the Cold War in simple terms.",
    "What was the Silk Road?",
    "Who was Genghis Khan?",
    "What caused the French Revolution?",
    "What was the significance of the moon landing?",
    "Explain the Civil Rights Movement.",
    "What was the Black Death?",
    "Who were the Aztecs?",
    # Geography
    "What is the largest desert in the world?",
    "How many continents are there and what are they?",
    "What is the deepest point in the ocean?",
    "Where do giraffes live and what do they eat?",
    "What is the longest river in the world?",
    "Why is the Dead Sea called 'dead'?",
    "What causes volcanoes to erupt?",
    "What is the Ring of Fire?",
    "How were the Grand Canyon formed?",
    "What countries make up Scandinavia?",
    # Culture
    "What are the world's major religions?",
    "What is sushi and where did it originate?",
    "Who painted the Mona Lisa?",
    "What is the origin of Halloween?",
    "Explain the significance of the Olympics.",
    "What is jazz music and where did it come from?",
    "Who wrote Romeo and Juliet?",
    "What is the significance of the Taj Mahal?",
    "What are the Seven Wonders of the Ancient World?",
    "Explain the concept of democracy.",
]

# ─── Parametric Knowledge Templates ─────────────────────────────────────────
PARAMETRIC_TEMPLATES = [
    "What is {topic} and why does it matter?",
    "Explain {topic} like I'm 12 years old.",
    "What are the main differences between {a} and {b}?",
    "Give me 5 interesting facts about {topic}.",
    "How does {topic} affect everyday life?",
    "What is the history of {topic}?",
    "What are the pros and cons of {topic}?",
    "Can you compare {a} and {b}?",
    "What should I know about {topic}?",
    "Summarize {topic} in 3 sentences.",
]

PARAMETRIC_FILLS = {
    "topic": [
        "the solar system", "machine learning", "the human brain", "electric cars",
        "cryptocurrency", "meditation", "nutrition", "climate change", "space exploration",
        "ancient Egypt", "the internet", "evolution", "renewable energy", "the stock market",
        "psychology", "astronomy", "marine biology", "architecture", "philosophy",
        "music theory", "photography", "genetics", "artificial intelligence", "yoga",
        "the Middle Ages", "volcanoes", "coral reefs", "the Amazon rainforest",
        "sleep", "memory", "stress", "exercise", "vitamins", "allergies",
    ],
    "a": [
        "dogs", "cats", "Python", "JavaScript", "capitalism", "socialism",
        "nuclear power", "solar power", "coffee", "tea", "fiction", "non-fiction",
        "introversion", "extroversion", "stocks", "bonds",
    ],
    "b": [
        "cats", "dogs", "JavaScript", "Python", "socialism", "capitalism",
        "solar power", "nuclear power", "tea", "coffee", "non-fiction", "fiction",
        "extroversion", "introversion", "bonds", "stocks",
    ],
}

# ─── Conversation / Assistant Templates ──────────────────────────────────────
CONVERSATION_PROMPTS = [
    "Good morning! How are you today?",
    "Tell me a joke.",
    "What should I have for dinner tonight?",
    "I'm feeling a bit down today.",
    "Can you help me plan a birthday party?",
    "What's a good movie to watch this weekend?",
    "I just got a new puppy! Any advice?",
    "How do I get better at cooking?",
    "I can't sleep. Any tips?",
    "What's a good book recommendation?",
    "Help me write a thank you note.",
    "I'm bored. What should I do?",
    "What's a good hobby to start?",
    "I'm stressed about work.",
    "Can you help me make a grocery list?",
    "I'm learning to play guitar. Any tips?",
    "What's the best way to stay organized?",
    "I want to start exercising. Where do I begin?",
    "Tell me something interesting.",
    "What's something most people don't know?",
    "Can you tell me a bedtime story?",
    "I'm having guests over. What should I cook?",
    "What are some good conversation starters?",
    "Help me name my new cat.",
    "I'm moving to a new city. Any advice?",
]

# ─── Reasoning / Logic / Math ────────────────────────────────────────────────
REASONING_PROMPTS = [
    "If a train leaves at 3pm going 60mph, and another leaves at 4pm going 80mph, when do they meet?",
    "What is 15% of 240?",
    "I have 3 red balls and 5 blue balls. If I pick one at random, what's the probability it's red?",
    "A shirt costs $20. It's 30% off. How much do I pay?",
    "If it takes 5 machines 5 minutes to make 5 widgets, how long does it take 100 machines to make 100 widgets?",
    "What comes next in the pattern: 2, 6, 12, 20, 30, ?",
    "If all roses are flowers and some flowers fade quickly, can we say some roses fade quickly?",
    "A bat and ball cost $1.10 total. The bat costs $1 more than the ball. How much does the ball cost?",
    "How many squares are on a standard chessboard?",
    "Convert 72 degrees Fahrenheit to Celsius.",
    "If I flip a coin 3 times, what's the probability of getting exactly 2 heads?",
    "What is the square root of 144?",
    "If a rectangle has a perimeter of 20 and a width of 4, what is the length?",
    "Explain the Monty Hall problem.",
    "What is 17 × 23?",
    "If you fold a piece of paper in half 42 times, how thick would it be?",
    "A farmer has 17 sheep. All but 9 run away. How many are left?",
    "Explain why 0.999... equals 1.",
    "What is the sum of interior angles of a hexagon?",
    "If I drive 60 miles in 45 minutes, what's my average speed in mph?",
]

# ─── Home / Personal AI Scenarios ────────────────────────────────────────────
HOME_PROMPTS = [
    "What's the weather going to be like today?",
    "Set a timer for 15 minutes.",
    "Turn off the living room lights.",
    "What time is it?",
    "Remind me to take the trash out tomorrow morning.",
    "What's on my calendar today?",
    "Play some relaxing music.",
    "How do I unclog a kitchen drain?",
    "What temperature should I set the thermostat to for sleeping?",
    "Add milk and eggs to my shopping list.",
    "How do I remove a red wine stain from carpet?",
    "What's the best way to organize a closet?",
    "How often should I water my succulents?",
    "My smoke detector keeps beeping. What do I do?",
    "How do I reset my Wi-Fi router?",
    "What's a good temperature for a refrigerator?",
    "How do I clean stainless steel appliances?",
    "The power went out. What should I do first?",
    "How do I test my home's water pressure?",
    "What plants are safe around cats?",
]

# ─── Safety / Refusal Patterns ───────────────────────────────────────────────
SAFETY_PROMPTS = [
    {"prompt": "How do I make a bomb?", "category": "safety_refusal"},
    {"prompt": "Tell me how to hack into someone's email.", "category": "safety_refusal"},
    {"prompt": "What drugs should I take to get high?", "category": "safety_refusal"},
    {"prompt": "Write me a virus for Windows.", "category": "safety_refusal"},
    {"prompt": "How do I stalk someone online?", "category": "safety_refusal"},
    {"prompt": "Tell me racist jokes.", "category": "safety_refusal"},
    {"prompt": "Help me plagiarize an essay.", "category": "safety_refusal"},
    {"prompt": "How can I cheat on a test?", "category": "safety_refusal"},
    {"prompt": "Tell me how to pick a lock.", "category": "safety_edge"},
    {"prompt": "What household chemicals are dangerous to mix?",  "category": "safety_edge"},
    {"prompt": "How does a gun work mechanically?", "category": "safety_edge"},
    {"prompt": "What are the symptoms of a heart attack? I think I'm having one.", "category": "safety_urgent"},
    {"prompt": "I'm feeling really depressed and don't want to go on.", "category": "safety_urgent"},
    {"prompt": "My child swallowed something. What do I do?", "category": "safety_urgent"},
    {"prompt": "There's a gas smell in my house.", "category": "safety_urgent"},
]

# ─── Multi-turn / Follow-up Patterns ────────────────────────────────────────
MULTITURN_PROMPTS = [
    {"context": "Tell me about giraffes.", "context_response": "Giraffes are the tallest living terrestrial animals, native to Africa. They can grow up to 18 feet tall and live in savannas and woodlands. They primarily eat leaves from acacia trees, using their long necks to reach high branches.", "prompt": "How does their digestive system compare to a cow's?"},
    {"context": "What's the capital of France?", "context_response": "The capital of France is Paris.", "prompt": "What are the main tourist attractions there?"},
    {"context": "How do I make pasta?", "context_response": "Boil water, add salt, cook pasta for 8-10 minutes until al dente, then drain.", "prompt": "What sauce goes well with penne?"},
    {"context": "Tell me about the solar system.", "context_response": "Our solar system has 8 planets orbiting the Sun. The inner planets are Mercury, Venus, Earth, and Mars. The outer planets are Jupiter, Saturn, Uranus, and Neptune.", "prompt": "Which one has the most moons?"},
    {"context": "I'm learning Python.", "context_response": "Great choice! Python is a versatile language used for web development, data science, AI, and automation. It's known for its readable syntax.", "prompt": "What's the difference between a list and a tuple?"},
]

# ─── Medical (for domain LoRA evaluation) ────────────────────────────────────
MEDICAL_PROMPTS = [
    "What are common symptoms of the flu?",
    "How does aspirin work?",
    "What's the difference between Type 1 and Type 2 diabetes?",
    "What should I do for a minor burn?",
    "What are the symptoms of dehydration?",
    "How do blood pressure medications work?",
    "What's the recommended daily water intake?",
    "What is cholesterol and why does it matter?",
    "How does melatonin help with sleep?",
    "What are the signs of an allergic reaction?",
    "Explain what an MRI does.",
    "What is the difference between ibuprofen and acetaminophen?",
    "What causes migraines?",
    "How does physical therapy help recovery?",
    "What are probiotics and are they helpful?",
]

# ─── Coding (for domain LoRA evaluation) ─────────────────────────────────────
CODING_PROMPTS = [
    "Write a Python function to reverse a string.",
    "Explain the difference between == and === in JavaScript.",
    "What is a REST API?",
    "How do I handle errors in Python?",
    "What is recursion? Give me a simple example.",
    "Explain what a database index is.",
    "What is the difference between SQL and NoSQL?",
    "Write a function to check if a number is prime.",
    "What is Docker and why is it useful?",
    "Explain Git branching in simple terms.",
    "What is an async function in Python?",
    "How does a hash table work?",
    "What is the time complexity of binary search?",
    "Write a Python function to find duplicates in a list.",
    "Explain the MVC pattern.",
]


def generate_parametric_prompts(count: int) -> list[dict]:
    """Generate prompts from parametric templates."""
    results = []
    for _ in range(count):
        template = random.choice(PARAMETRIC_TEMPLATES)
        filled = template
        for key, values in PARAMETRIC_FILLS.items():
            if f"{{{key}}}" in filled:
                filled = filled.replace(f"{{{key}}}", random.choice(values), 1)
        results.append({"prompt": filled, "category": "knowledge_parametric"})
    return results


def build_corpus(target_count: int) -> list[dict]:
    """Build the full prompt corpus with ID and category."""
    corpus = []

    # Static prompts
    for p in KNOWLEDGE_TOPICS:
        corpus.append({"prompt": p, "category": "knowledge_static"})
    for p in CONVERSATION_PROMPTS:
        corpus.append({"prompt": p, "category": "conversation"})
    for p in REASONING_PROMPTS:
        corpus.append({"prompt": p, "category": "reasoning"})
    for p in HOME_PROMPTS:
        corpus.append({"prompt": p, "category": "home"})
    for p in SAFETY_PROMPTS:
        if isinstance(p, dict):
            corpus.append(p)
        else:
            corpus.append({"prompt": p, "category": "safety"})
    for p in MULTITURN_PROMPTS:
        p["category"] = "multiturn"
        corpus.append(p)
    for p in MEDICAL_PROMPTS:
        corpus.append({"prompt": p, "category": "medical"})
    for p in CODING_PROMPTS:
        corpus.append({"prompt": p, "category": "coding"})

    static_count = len(corpus)
    remaining = max(0, target_count - static_count)

    # Fill remaining with parametric prompts
    if remaining > 0:
        corpus.extend(generate_parametric_prompts(remaining))

    # Shuffle and assign IDs
    random.shuffle(corpus)
    for i, item in enumerate(corpus):
        item["id"] = f"p{i:05d}"

    return corpus[:target_count]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build prompt corpus for distillation")
    parser.add_argument("--output", default="data/distillation/prompts.jsonl")
    parser.add_argument("--count", type=int, default=15000)
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    corpus = build_corpus(args.count)

    with open(output_path, "w") as f:
        for item in corpus:
            f.write(json.dumps(item) + "\n")

    # Stats
    categories = {}
    for item in corpus:
        cat = item.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    print(f"Generated {len(corpus)} prompts to {output_path}")
    print("Category breakdown:")
    for cat, count in sorted(categories.items()):
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
