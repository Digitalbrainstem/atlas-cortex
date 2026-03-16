"""Expanded agriculture & animals teacher data generator.
Covers: gardening, soil, pests, food preservation, pet safety/health/nutrition,
livestock, aquariums, mushrooms, and more."""
from __future__ import annotations
import asyncio, json, random
from pathlib import Path
import aiohttp

API_URL = 'http://localhost:8000/v1/chat/completions'
MODEL = 'QuantTrio/Qwen3.5-122B-A10B-AWQ'
OUT_PATH = Path('/workspace/atlas-distillation/data/specialist_teacher/teacher_agriculture_animals_v2.jsonl')
CONCURRENT = 16

SYSTEM = (
    'You are a knowledgeable agricultural scientist, master gardener, and veterinary-informed '
    'pet care expert. Provide practical, science-based advice. Be thorough but concise — '
    'target 300-500 words. Use specific numbers, varieties, and actionable steps when possible.'
)

def build_prompts():
    prompts = []

    # === VEGETABLES ===
    veggies = ['tomatoes','peppers','cucumbers','squash','zucchini','lettuce','spinach',
               'kale','carrots','radishes','beets','onions','garlic','potatoes',
               'sweet potatoes','beans','peas','corn','broccoli','cauliflower',
               'cabbage','Brussels sprouts','eggplant','okra','asparagus','celery']
    for v in veggies:
        prompts.append(f'How do I grow {v} from seed to harvest? Include soil, spacing, watering, and common problems.')
        prompts.append(f'What are the most common pests and diseases that affect {v}, and how do I treat them organically?')

    # === FRUITS ===
    fruits = ['strawberries','blueberries','raspberries','blackberries','grapes',
              'apple trees','peach trees','cherry trees','pear trees','fig trees',
              'citrus trees (lemon/orange)','watermelon','cantaloupe','avocado trees']
    for f in fruits:
        prompts.append(f'How do I grow {f} successfully? Cover planting, care, pruning, and harvesting.')
        prompts.append(f'What diseases and pests commonly affect {f} and what are organic solutions?')

    # === HERBS ===
    herbs = ['basil','cilantro','parsley','rosemary','thyme','oregano','sage',
             'mint','dill','chives','lavender','lemongrass','chamomile','echinacea']
    for h in herbs:
        prompts.append(f'How do I grow {h} indoors and outdoors? Best conditions, harvesting, and uses.')
    prompts.append('Design a kitchen herb garden for a sunny windowsill. What herbs grow best together indoors?')
    prompts.append('Which medicinal herbs can I grow at home and what are their traditional uses and evidence base?')

    # === SOIL SCIENCE ===
    prompts.extend([
        'How do I interpret a soil test report? What do the numbers for N, P, K, pH, and organic matter mean?',
        'Explain the difference between clay, sandy, loam, and silt soils. How do I improve each type?',
        'What is soil pH and why does it matter? How do I raise or lower pH for different plants?',
        'How do soil microorganisms (mycorrhizae, bacteria) benefit plants? How do I encourage them?',
        'What are cover crops and green manures? Which ones fix nitrogen and improve soil structure?',
        'How do I build soil from scratch for a new garden bed? Step by step from bare ground to planting.',
        'What is the difference between topsoil, garden soil, potting mix, and compost? When to use each?',
        'How do I manage soil drainage problems? Solutions for both waterlogged and overly dry soil.',
        'Explain cation exchange capacity (CEC) in simple terms. Why does it matter for fertilizing?',
        'How do I test soil at home without a lab kit? DIY methods for pH, drainage, and texture.',
    ])

    # === COMPOSTING ===
    prompts.extend([
        'Compare hot composting vs cold composting vs vermicomposting. Pros, cons, and setup for each.',
        'How do I start a worm bin for vermicomposting? What species, bedding, food scraps, and maintenance?',
        'My compost pile smells bad / is not heating up / has flies. Troubleshoot common composting problems.',
        'What can and cannot go in a home compost bin? Complete list with explanations.',
        'How do I make compost tea? Is it scientifically proven to benefit plants?',
        'How long does composting take and how do I speed it up? Carbon-to-nitrogen ratios explained.',
        'Bokashi composting: what is it, how does it work, and is it better than traditional composting?',
    ])

    # === PEST MANAGEMENT ===
    prompts.extend([
        'What is integrated pest management (IPM)? Explain the pyramid: cultural, mechanical, biological, chemical.',
        'Identify and treat aphids organically. What plants attract their natural predators?',
        'How do I deal with Japanese beetles, grubs, and other lawn/garden beetles without chemicals?',
        'Slugs and snails are destroying my garden. What are the most effective organic controls?',
        'How do I manage tomato hornworms, cabbage worms, and other caterpillar pests?',
        'What beneficial insects should I attract to my garden? How do I create habitat for them?',
        'How do I use neem oil, insecticidal soap, and diatomaceous earth safely and effectively?',
        'Squash bugs, squash vine borers, and cucumber beetles — identification and organic management.',
        'How do I deal with deer, rabbits, and squirrels eating my garden? Fencing and deterrent strategies.',
        'What are companion plants that naturally repel pests? The science behind companion planting.',
        'How do I identify and treat fungal diseases: powdery mildew, blight, black spot, rust?',
        'Spider mites on indoor and outdoor plants — identification, prevention, and treatment.',
        'How do I manage fire ants, carpenter ants, and ant colonies near garden beds?',
        'Gopher, mole, and vole damage in the garden. How to identify which one and manage them.',
    ])

    # === RAISED BEDS & STRUCTURES ===
    prompts.extend([
        'Design and build a raised bed garden. Materials, dimensions, soil mix, and drainage.',
        'What is the best soil mix for raised beds? The common recipes and which works best.',
        'How do I build a cold frame or low tunnel to extend the growing season?',
        'Greenhouse basics for beginners: types, heating, ventilation, and what to grow when.',
        'How do I build and use a hoop house for year-round growing?',
        'Vertical gardening: trellises, living walls, and tower gardens for small spaces.',
        'How do I set up a drip irrigation system for raised beds? Components and layout.',
    ])

    # === SEASONAL PLANNING ===
    prompts.extend([
        'Create a year-round vegetable planting calendar for USDA zone 7 (mid-Atlantic/Southeast US).',
        'What are first and last frost dates and how do I use them to plan my garden?',
        'Explain succession planting. How do I keep harvesting lettuce, beans, and radishes all season?',
        'What vegetables can I plant in fall for a winter harvest? Cold-hardy crops explained.',
        'How do I winterize my garden? Mulching, cover crops, tool care, and bed preparation.',
        'Spring garden preparation: when to start seeds indoors, soil prep, and transplant timing.',
        'How do I plan a four-season garden in a temperate climate?',
        'What grows well in hot, humid climates (zones 8-10)? Summer gardening strategies.',
    ])

    # === CONTAINER & INDOOR ===
    prompts.extend([
        'What vegetables grow well in containers on a balcony or patio? Best varieties and pot sizes.',
        'How do I grow tomatoes in containers successfully? Pot size, soil, support, and watering.',
        'Indoor plant care basics: light levels, watering, humidity, and common mistakes.',
        'What are the best low-light indoor plants? Care guide for apartments with limited sun.',
        'How do I grow microgreens at home? Setup, seeds, harvesting, and nutrition.',
        'Growing sprouts at home safely — equipment, seeds, food safety, and best varieties.',
        'How do I set up grow lights for indoor seed starting? LED vs fluorescent, height, timing.',
        'Houseplant pest management: fungus gnats, mealybugs, scale, and spider mites indoors.',
        'Best air-purifying indoor plants — what does the science actually say?',
    ])

    # === HYDROPONICS ===
    prompts.extend([
        'Explain hydroponics for beginners. DWC, NFT, ebb and flow — which system is simplest?',
        'How do I build a simple deep water culture (DWC) hydroponic system at home?',
        'Hydroponic nutrient solutions: what are the essential elements and how do I mix them?',
        'Kratky method hydroponics: the simplest no-pump system. Setup and best crops.',
        'Aquaponics basics: combining fish and plants. How does the nitrogen cycle work?',
    ])

    # === FOOD PRESERVATION ===
    prompts.extend([
        'How do I safely can vegetables and fruits at home? Water bath vs pressure canning explained.',
        'Fermenting vegetables at home: sauerkraut, kimchi, pickles. Safety and technique.',
        'How do I dehydrate fruits, vegetables, and herbs? Methods and storage.',
        'Freezing garden produce: blanching, packaging, and shelf life for common vegetables.',
        'How do I make and store tomato sauce, salsa, and other canned goods from garden produce?',
        'Root cellar storage: which vegetables store well and what conditions do they need?',
    ])

    # === LAWN CARE ===
    prompts.extend([
        'How do I establish a new lawn from seed? Soil prep, grass types by region, and care.',
        'Organic lawn care: fertilizing, overseeding, aerating, and weed control without chemicals.',
        'How do I identify and treat common lawn diseases: brown patch, dollar spot, fairy rings?',
        'Should I replace my lawn with native groundcovers or clover? Pros, cons, and how-to.',
        'How do I deal with crabgrass, dandelions, and other common lawn weeds organically?',
    ])

    # === TREES & LANDSCAPING ===
    prompts.extend([
        'How do I properly plant a tree? Hole size, depth, mulching, staking, and first-year care.',
        'When and how to prune fruit trees, shade trees, and ornamental trees.',
        'What are the best shade trees for residential yards in temperate climates?',
        'How do I design a native plant landscape? Benefits for wildlife, water, and maintenance.',
        'Xeriscaping: drought-tolerant landscaping design principles and plant choices.',
    ])

    # === POLLINATORS ===
    prompts.extend([
        'How do I create a pollinator garden? Best flowers for bees, butterflies, and hummingbirds.',
        'Native bees vs honeybees: what are the differences and how do I support native pollinators?',
        'How do I build and maintain a mason bee house?',
        'What flowers bloom in sequence to provide nectar from early spring through fall?',
        'How do I attract beneficial wildlife (birds, toads, ladybugs) to my garden?',
        'Monarch butterfly conservation: growing milkweed and creating waystation gardens.',
    ])

    # === CAN MY DOG EAT... ===
    dog_foods = [
        'chocolate','grapes','raisins','onions','garlic','avocado','macadamia nuts',
        'xylitol (artificial sweetener)','alcohol','caffeine/coffee','raw eggs',
        'raw meat/bones','mushrooms','walnuts','cherries','peaches (pits)',
        'tomatoes (green parts)','nutmeg','salt in large amounts','yeast dough',
        'apple seeds/cores','apricots','corn on the cob','cooked bones',
        'bananas','blueberries','watermelon','carrots','sweet potatoes','pumpkin',
        'rice','plain chicken','peanut butter (without xylitol)','green beans',
        'apples (without seeds)','salmon','eggs (cooked)','plain yogurt','oatmeal',
        'broccoli','celery','mango','pineapple','strawberries','oranges','cucumber',
        'coconut','honey','popcorn (plain)','shrimp (cooked)','turkey','cheese',
        'bread','peas','cranberries','spinach','quinoa',
    ]
    for food in dog_foods:
        prompts.append(f'Can my dog eat {food}? Is it safe, toxic, or should it be limited? Explain the risks or benefits.')

    # === CAN MY CAT EAT... ===
    cat_foods = [
        'tuna (canned)','milk/dairy','raw fish','onions','garlic','chocolate',
        'grapes/raisins','dog food','liver (too much)','raw eggs','alcohol',
        'caffeine','xylitol','avocado','raw dough',
        'cooked chicken','cooked fish','small amount of cheese','bananas',
        'blueberries','watermelon (seedless)','pumpkin','cooked eggs',
        'rice','cooked carrots','green beans','cantaloupe',
    ]
    for food in cat_foods:
        prompts.append(f'Can my cat eat {food}? Is it safe or harmful? What amount is okay or dangerous?')

    # === DOG HEALTH ===
    prompts.extend([
        'My dog is limping / favoring a leg. Common causes and when to see a vet.',
        'How do I know if my dog has allergies? Signs, types (food vs environmental), and management.',
        'Dog ear infections: causes, symptoms, home cleaning, and when antibiotics are needed.',
        'How to care for my dogs teeth. Brushing, dental chews, and signs of dental disease.',
        'My dog is vomiting / has diarrhea. When is it an emergency vs when can I manage at home?',
        'Hot spots on dogs: what causes them and how to treat them.',
        'How do I check my dog for ticks and what diseases do ticks transmit to dogs?',
        'Dog joint health and arthritis: supplements, exercise, weight management, and medications.',
        'Kennel cough in dogs: symptoms, treatment, prevention, and vaccination.',
        'How do I know if my dog is overweight? Body condition scoring and safe weight loss.',
        'Bloat (GDV) in dogs: what is it, which breeds are at risk, and warning signs.',
        'Anxiety in dogs: separation anxiety, noise phobia, and calming strategies.',
        'Puppy care basics: feeding schedule, house training, socialization timeline, and vaccines.',
        'Senior dog care: age-related changes, vet checkup frequency, diet adjustments.',
        'How do I safely introduce a new dog to my existing pets?',
        'Common skin conditions in dogs: mange, ringworm, yeast infections — identification and treatment.',
        'Heart disease in dogs: types, symptoms, breeds at risk, and management.',
        'Canine diabetes: signs, diagnosis, insulin management, and diet.',
        'My dog ate something it should not have. When to induce vomiting vs go to emergency vet.',
        'Flea and tick prevention: comparing oral, topical, and collar options. Pros and cons.',
    ])

    # === CAT HEALTH ===
    prompts.extend([
        'My cat is not eating / hiding / lethargic. Warning signs that need vet attention.',
        'Urinary problems in cats (FLUTD): symptoms, causes, emergency signs, and prevention.',
        'Hairballs in cats: are they normal? When to worry and how to reduce them.',
        'How do I keep my indoor cat healthy and mentally stimulated? Enrichment ideas.',
        'Cat dental disease: signs, professional cleaning, and home care.',
        'Upper respiratory infections in cats: symptoms, treatment, and prevention.',
        'Feline diabetes: signs, insulin management, diet, and potential remission.',
        'Kidney disease in cats: early signs, diet management, and prognosis.',
        'Hyperthyroidism in cats: symptoms, diagnosis, and treatment options.',
        'How do I introduce a new cat to my household? Step-by-step guide.',
        'Kitten care basics: feeding schedule, litter training, socialization, and first vet visit.',
        'Senior cat care: age-related changes, diet adjustments, and monitoring.',
        'Cat behavior problems: scratching furniture, litter box avoidance, aggression.',
        'How do I trim my cats nails and groom a long-haired cat?',
        'Common parasites in cats: worms, fleas, ear mites — prevention and treatment.',
    ])

    # === PET EMERGENCY & FIRST AID ===
    prompts.extend([
        'Pet first aid kit: what should be in it and how to use each item.',
        'How to perform CPR on a dog or cat. Step-by-step for different sizes.',
        'My pet is choking. How do I perform the Heimlich maneuver on a dog or cat?',
        'Heatstroke in dogs and cats: recognition, first aid, and prevention.',
        'My pet was bitten by a snake. What do I do? Venomous vs non-venomous bites.',
        'Bee stings and insect bites in pets: allergic reactions and treatment.',
        'How do I stop bleeding from a wound on my pet? First aid for cuts and lacerations.',
        'My pet ingested rat poison / antifreeze / cleaning chemicals. Emergency steps.',
        'Seizures in dogs and cats: what to do during and after, and when its an emergency.',
        'My pet was hit by a car. Immediate first aid and safe transport to the vet.',
        'Porcupine quills, foxtails, and other foreign body emergencies in pets.',
        'Signs of internal bleeding in pets: what to watch for after trauma.',
    ])

    # === PET NUTRITION ===
    prompts.extend([
        'How to read a pet food label. What do the ingredients, guaranteed analysis, and AAFCO statement mean?',
        'Raw diet vs kibble vs wet food for dogs: pros, cons, safety, and nutritional completeness.',
        'Raw diet vs commercial food for cats: is raw feeding safe and nutritionally complete?',
        'Grain-free dog food controversy: the FDA investigation and DCM heart disease link.',
        'How much should I feed my dog? Calculating portions by weight, age, and activity level.',
        'How much should I feed my cat? Indoor vs outdoor, age-based feeding guidelines.',
        'Homemade dog food recipes: how to ensure nutritional balance and what supplements are needed.',
        'Puppy nutrition: when to switch from puppy to adult food, large breed vs small breed differences.',
        'Kitten nutrition: feeding schedule, wet vs dry, and transitioning to adult food.',
        'Dog food allergies: common allergens, elimination diets, and hypoallergenic options.',
        'Best foods for senior dogs and cats: joint support, kidney-friendly, and calorie management.',
        'Supplements for dogs: fish oil, glucosamine, probiotics — which ones actually help?',
        'Supplements for cats: taurine, omega-3, lysine — evidence and dosing.',
        'Treats and training rewards: healthy options and what percentage of daily calories they should be.',
    ])

    # === PET VACCINES ===
    prompts.extend([
        'Core vaccines for dogs: rabies, DHPP — what they protect against and recommended schedule.',
        'Non-core vaccines for dogs: Bordetella, Lyme, leptospirosis — who needs them and why.',
        'Core vaccines for cats: FVRCP, rabies — what they protect against and schedule.',
        'Non-core vaccines for cats: FeLV, FIV — which cats need them?',
        'Puppy and kitten vaccination schedules: timing, boosters, and when its safe to go outside.',
        'Are annual boosters necessary? Current veterinary guidelines on vaccination frequency.',
        'Vaccine reactions in pets: normal side effects vs allergic reactions. What to watch for.',
    ])

    # === CHICKENS & POULTRY ===
    prompts.extend([
        'Getting started with backyard chickens: breeds, coop requirements, local laws, and costs.',
        'Chicken coop design: size, ventilation, nesting boxes, roosting bars, and predator protection.',
        'What do chickens eat? Layer feed, treats, grit, calcium, and foods to avoid.',
        'Common chicken diseases: Marek, coccidiosis, respiratory infections — prevention and treatment.',
        'Egg production: why have my chickens stopped laying? Causes and solutions.',
        'Raising chicks from day one: brooder setup, temperature, feeding, and integration with flock.',
        'Chicken predator protection: hawks, raccoons, foxes, snakes — how to secure your flock.',
        'Do I need a rooster? Pros and cons, noise, and fertilized vs unfertilized eggs.',
        'Molting in chickens: what is it, how long does it last, and how to support them through it.',
        'Winter chicken care: cold-hardy breeds, coop heating debate, frostbite prevention, water.',
    ])

    # === LIVESTOCK & HOMESTEADING ===
    prompts.extend([
        'Getting started with backyard goats: breeds, fencing, shelter, and basic care.',
        'Goat health basics: common diseases, hoof trimming, deworming, and vaccination.',
        'Raising meat rabbits: breeds, housing, feeding, and humane processing basics.',
        'Beekeeping for beginners: equipment, hive types, seasonal management, and honey harvesting.',
        'Common bee diseases and pests: varroa mites, American foulbrood, hive beetles.',
        'Raising ducks: breeds, housing differences from chickens, and egg production.',
        'How to raise quail for eggs: space-efficient protein production for small properties.',
        'Basic sheep care: breeds for small flocks, fencing, feeding, and shearing.',
        'Pig keeping basics: breeds for homesteads, housing, feeding, and rotational pasture.',
    ])

    # === AQUARIUMS & FISH ===
    prompts.extend([
        'Setting up a freshwater aquarium for beginners: tank size, equipment, and cycling explained.',
        'The nitrogen cycle in aquariums: ammonia, nitrite, nitrate — how to cycle a new tank.',
        'Best freshwater fish for beginners: hardy species, compatibility, and stocking levels.',
        'Common freshwater fish diseases: ich, fin rot, dropsy — identification and treatment.',
        'How do I maintain a healthy aquarium? Water changes, testing, filter maintenance schedule.',
        'Live plants in aquariums: benefits, easy species, lighting, and substrate choices.',
        'Betta fish care: tank size myths, water parameters, feeding, and common health issues.',
        'Goldfish care: proper tank size (not a bowl!), filtration, feeding, and lifespan.',
        'Shrimp keeping: cherry shrimp, amano shrimp — setup, water parameters, and breeding.',
        'Dealing with algae in aquariums: types, causes, and natural vs chemical solutions.',
        'How to set up a planted aquarium: CO2, fertilizers, lighting, and aquascaping basics.',
        'Quarantine procedures for new fish: why its important and how to set up a QT tank.',
    ])

    # === OTHER PETS ===
    prompts.extend([
        'Hamster care: cage size, bedding, diet, exercise, and common health issues.',
        'Guinea pig care: housing, diet (vitamin C!), social needs, and health.',
        'Rabbit care as indoor pets: housing, diet, litter training, and health.',
        'Reptile basics: leopard gecko care — tank setup, heating, feeding, and handling.',
        'Ball python care: enclosure, heating, humidity, feeding frozen/thawed, and handling.',
        'Bearded dragon care: lighting (UVB!), diet, temperature gradients, and common issues.',
        'Parakeet/budgie care: cage size, diet, socialization, and common health problems.',
        'Cockatiel care: diet, cage requirements, and building trust with your bird.',
        'Turtle and tortoise basics: species differences, housing, UVB, diet, and lifespan.',
        'Ferret care: cage setup, diet, ferret-proofing your home, and common health issues.',
    ])

    # === MUSHROOM GROWING ===
    prompts.extend([
        'Growing oyster mushrooms at home: substrate, containers, and fruiting conditions.',
        'Shiitake mushroom cultivation on logs: inoculation, stacking, and harvesting timeline.',
        'Indoor mushroom growing kits vs DIY: what works best for beginners?',
        'Lions mane, reishi, and other medicinal mushrooms: can I grow them at home?',
    ])

    # === FLOWERS & ORNAMENTALS ===
    prompts.extend([
        'How do I grow roses? Pruning, feeding, disease prevention, and best varieties for beginners.',
        'Annual vs perennial flowers: what is the difference and how do I plan beds with both?',
        'Growing sunflowers: varieties, planting, and harvesting seeds for eating.',
        'Dahlia care: planting tubers, staking, feeding, and winter storage.',
        'How do I grow a cut flower garden? Best varieties for bouquets and continuous blooms.',
        'Bulb planting guide: tulips, daffodils, crocus, allium — when and how to plant.',
        'How do I divide and propagate perennials like hostas, daylilies, and irises?',
        'Growing peonies: planting depth, support, and why they might not bloom.',
        'Wildflower meadow creation: seed selection, soil prep, and maintenance.',
    ])

    # === GENERAL / CROSS-CUTTING ===
    prompts.extend([
        'How do I start a vegetable garden from scratch with zero experience? Step-by-step first year plan.',
        'What is permaculture? Core principles and how to apply them to a suburban yard.',
        'How do I garden on a budget? Free seeds, DIY supplies, and maximizing value.',
        'Best gardening books and resources for beginners and intermediate growers.',
        'How do I deal with poor soil, heavy clay, or rocky ground? Realistic strategies.',
        'Square foot gardening explained: does it really work and how do I set it up?',
        'How do I save seeds from my garden? Techniques for tomatoes, peppers, beans, and flowers.',
        'Edible landscaping: replacing ornamental plants with food-producing alternatives.',
        'How do I calculate how much garden space I need to supplement my familys produce?',
        'Common gardening myths debunked: eggshells for calcium, coffee grounds for acid, etc.',
        'Gardening with kids: age-appropriate projects and plants that excite children.',
        'How do pet-safe gardens work? Plants toxic to dogs/cats and safe alternatives.',
        'Planning a garden for someone with mobility issues: accessible raised beds and tools.',
        'What is biointensive gardening? Yield per square foot and the Jeavons method.',
        'How do I attract and support fireflies in my yard?',
    ])

    return prompts

async def generate_one(session, prompt, sem):
    async with sem:
        payload = {
            'model': MODEL,
            'messages': [
                {'role': 'system', 'content': SYSTEM},
                {'role': 'user', 'content': prompt}
            ],
            'max_tokens': 2048, 'temperature': 0.7, 'top_p': 0.9,
            'presence_penalty': 1.2,
            'chat_template_kwargs': {'enable_thinking': False}
        }
        for attempt in range(3):
            try:
                async with session.post(API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data['choices'][0]['message'].get('content') or ''
                        if not content.strip():
                            return None
                        return {'prompt': prompt, 'response': content, 'thinking': ''}
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                print(f'  Error: {e}')
                await asyncio.sleep(2 ** attempt)
    return None

async def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_prompts = build_prompts()
    random.seed(42)
    random.shuffle(all_prompts)
    done = set()
    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            for line in f:
                done.add(json.loads(line)['prompt'])
    remaining = [p for p in all_prompts if p not in done]
    print(f'Total prompts: {len(all_prompts)}, already done: {len(done)}, remaining: {len(remaining)}')
    sem = asyncio.Semaphore(CONCURRENT)
    async with aiohttp.ClientSession() as session:
        tasks = [generate_one(session, p, sem) for p in remaining]
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
    print(f'DONE: {completed} generated, total file: {len(done) + completed} rows')

if __name__ == '__main__':
    asyncio.run(main())
