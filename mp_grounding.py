#!/usr/bin/env python3
"""Memory Palace — Grounding Effect Deep Dive

Hypothesis: Pre-computed KV caches stabilize thinking-mode reasoning,
preventing the model from second-guessing correct initial answers.

Design:
1. 100 MCQ questions across 5 subjects (20 each) the model likely knows
2. Run baseline (no think) — establish which questions model gets RIGHT
3. Run thinking mode — show which correct answers flip to WRONG
4. Run Palace+Think — show which flipped answers are RESCUED
5. Run Palace (no think) — isolate Palace's effect without thinking

This gives us:
- "Overthink rate" = % of correct baseline answers that thinking flips wrong
- "Grounding rescue rate" = % of overthought answers Palace+Think recovers
- Statistical significance via McNemar's test on paired binary outcomes
"""
from __future__ import annotations
import os, json, time, gc, re
from pathlib import Path
from datetime import datetime

os.environ["HSA_OVERRIDE_GFX_VERSION"] = "11.0.0"

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, DynamicCache

RESULTS_DIR = Path("/mnt/fastpool/atlas-distillation/results/memory_palace/grounding")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
MODEL_ID = "Qwen/Qwen3-4B"

# ── Utilities ─────────────────────────────────────────────────────────

def clone_cache(cache):
    c = DynamicCache()
    if hasattr(cache, "layers"):
        for L in cache.layers:
            c.update(L.keys.clone(), L.values.clone(), len(c.layers))
    elif hasattr(cache, "key_cache"):
        for i in range(len(cache.key_cache)):
            if cache.key_cache[i] is not None:
                c.update(cache.key_cache[i].clone(), cache.value_cache[i].clone(), i)
    return c

def build_cache(text, tokenizer, model):
    msgs = [{"role": "system", "content": text}]
    prefix = tokenizer.apply_chat_template(msgs, tokenize=False, enable_thinking=False)
    ids = tokenizer(prefix, return_tensors="pt").input_ids.to("cuda")
    prefix_len = ids.shape[1]
    with torch.no_grad():
        out = model(ids, use_cache=True)
    return out.past_key_values, prefix_len

def query_palace(q, cache, prefix_len, tok, model, max_new=2048, thinking=False):
    c = clone_cache(cache)
    msgs = [{"role": "user", "content": q}]
    qt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=thinking)
    qids = tok(qt, return_tensors="pt").input_ids.to("cuda")
    ql = qids.shape[1]
    pos = torch.arange(prefix_len, prefix_len + ql, device="cuda").unsqueeze(0)
    mask = torch.ones(1, prefix_len + ql, device="cuda", dtype=torch.long)
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(qids, past_key_values=c, position_ids=pos,
                             attention_mask=mask, max_new_tokens=max_new, do_sample=False)
    lat = time.time() - t0
    resp = tok.decode(out[0][ql:], skip_special_tokens=not thinking)
    return resp, lat

def query_baseline(q, tok, model, max_new=2048, thinking=False):
    msgs = [{"role": "user", "content": q}]
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=thinking)
    ids = tok(text, return_tensors="pt").input_ids.to("cuda")
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=max_new, do_sample=False)
    lat = time.time() - t0
    resp = tok.decode(out[0][ids.shape[1]:], skip_special_tokens=not thinking)
    return resp, lat

def extract_mcq(response):
    text = response
    if "</think>" in text:
        text = text.split("</think>")[-1]
    text = text.strip()
    for pat in [r"(?:answer is|answer:)\s*\(?([A-D])\)?",
                r"^\s*\(?([A-D])\)[\.\):]",
                r"\*\*([A-D])\*\*",
                r"^([A-D])$"]:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m: return m.group(1).upper()
    m = re.search(r"\b([A-D])\b", text)
    return m.group(1).upper() if m else "?"

# ── 100 Questions (well-known facts, model should get these right) ───

QUESTIONS = {
    "biology": [
        {"q": "What is the powerhouse of the cell?\nA) Nucleus\nB) Ribosome\nC) Mitochondria\nD) Golgi apparatus", "a": "C",
         "knowledge": "Mitochondria are the powerhouse of the cell, producing ATP through oxidative phosphorylation. The inner membrane contains the electron transport chain and ATP synthase."},
        {"q": "DNA replication is described as semi-conservative because:\nA) Only one strand is copied\nB) Each new DNA molecule has one old and one new strand\nC) Both strands are completely new\nD) Only coding regions are replicated", "a": "B",
         "knowledge": "Semi-conservative replication means each daughter DNA molecule contains one original (parent) strand and one newly synthesized strand. This was proven by the Meselson-Stahl experiment using nitrogen isotopes."},
        {"q": "Which molecule carries amino acids to the ribosome during translation?\nA) mRNA\nB) rRNA\nC) tRNA\nD) snRNA", "a": "C",
         "knowledge": "Transfer RNA (tRNA) carries specific amino acids to the ribosome during translation. Each tRNA has an anticodon that base-pairs with the corresponding mRNA codon, ensuring correct amino acid placement."},
        {"q": "The process by which plants convert light energy to chemical energy is:\nA) Cellular respiration\nB) Fermentation\nC) Photosynthesis\nD) Chemosynthesis", "a": "C",
         "knowledge": "Photosynthesis converts light energy into chemical energy (glucose) using CO2 and H2O. It occurs in chloroplasts, with light reactions in thylakoid membranes and the Calvin cycle in the stroma."},
        {"q": "Which type of bond holds the two strands of DNA together?\nA) Covalent bonds\nB) Ionic bonds\nC) Hydrogen bonds\nD) Peptide bonds", "a": "C",
         "knowledge": "Hydrogen bonds between complementary base pairs (A-T has 2, G-C has 3) hold the two DNA strands together. These are relatively weak individually but collectively stabilize the double helix."},
        {"q": "The enzyme that unwinds DNA during replication is:\nA) DNA polymerase\nB) Helicase\nC) Ligase\nD) Topoisomerase", "a": "B",
         "knowledge": "Helicase unwinds the DNA double helix by breaking hydrogen bonds between base pairs, creating a replication fork. It uses ATP hydrolysis to separate the strands."},
        {"q": "Which organelle is responsible for protein synthesis?\nA) Mitochondria\nB) Ribosome\nC) Lysosome\nD) Peroxisome", "a": "B",
         "knowledge": "Ribosomes are the site of protein synthesis (translation). They read mRNA codons and assemble amino acids into polypeptide chains. Found free in cytoplasm or attached to rough ER."},
        {"q": "What is the function of the cell membrane?\nA) Energy production\nB) Protein synthesis\nC) Selective permeability and cell boundary\nD) DNA storage", "a": "C",
         "knowledge": "The cell membrane (plasma membrane) is a selectively permeable phospholipid bilayer that controls what enters and exits the cell. It maintains homeostasis and cell integrity."},
        {"q": "Meiosis produces:\nA) 2 identical diploid cells\nB) 4 genetically unique haploid cells\nC) 2 haploid cells\nD) 4 identical diploid cells", "a": "B",
         "knowledge": "Meiosis is a two-division process (meiosis I and II) that produces 4 genetically unique haploid cells from one diploid cell. Crossing over and independent assortment create genetic diversity."},
        {"q": "Which blood cells are responsible for carrying oxygen?\nA) White blood cells\nB) Platelets\nC) Red blood cells\nD) Plasma cells", "a": "C",
         "knowledge": "Red blood cells (erythrocytes) carry oxygen bound to hemoglobin. Each hemoglobin molecule can bind 4 oxygen molecules. RBCs lack a nucleus to maximize hemoglobin capacity."},
        {"q": "The genetic code is:\nA) Overlapping and ambiguous\nB) Non-overlapping and degenerate\nC) Overlapping and degenerate\nD) Non-overlapping and unambiguous only", "a": "B",
         "knowledge": "The genetic code is non-overlapping (each nucleotide is part of only one codon), degenerate/redundant (multiple codons can code for the same amino acid), and universal (same code in almost all organisms)."},
        {"q": "Which vitamin is produced when skin is exposed to sunlight?\nA) Vitamin A\nB) Vitamin C\nC) Vitamin D\nD) Vitamin K", "a": "C",
         "knowledge": "Vitamin D is synthesized in the skin when UVB radiation converts 7-dehydrocholesterol to cholecalciferol (vitamin D3). It's essential for calcium absorption and bone health."},
        {"q": "The largest organ in the human body is:\nA) Liver\nB) Brain\nC) Skin\nD) Lungs", "a": "C",
         "knowledge": "The skin is the largest organ, covering approximately 1.5-2 square meters in adults. It serves as a protective barrier, regulates temperature, and contains sensory receptors."},
        {"q": "Which part of the brain controls balance and coordination?\nA) Cerebrum\nB) Cerebellum\nC) Medulla oblongata\nD) Hypothalamus", "a": "B",
         "knowledge": "The cerebellum coordinates voluntary movements, balance, and motor learning. It receives input from sensory systems and other brain regions to fine-tune motor output."},
        {"q": "Antibodies are produced by:\nA) T cells\nB) B cells (plasma cells)\nC) Red blood cells\nD) Macrophages", "a": "B",
         "knowledge": "B lymphocytes differentiate into plasma cells that produce and secrete antibodies (immunoglobulins). Each B cell produces antibodies specific to one antigen."},
        {"q": "The function of the large intestine is primarily:\nA) Nutrient absorption\nB) Protein digestion\nC) Water absorption and feces formation\nD) Fat emulsification", "a": "C",
         "knowledge": "The large intestine primarily absorbs water and electrolytes from indigestible food matter, forming and storing feces. It also houses beneficial gut bacteria that produce certain vitamins."},
        {"q": "Which hormone regulates blood sugar by lowering glucose levels?\nA) Glucagon\nB) Insulin\nC) Cortisol\nD) Epinephrine", "a": "B",
         "knowledge": "Insulin, produced by beta cells of the pancreatic islets of Langerhans, lowers blood glucose by promoting cellular uptake and glycogen synthesis. Glucagon has the opposite effect."},
        {"q": "The process of cell death programmed by the cell itself is called:\nA) Necrosis\nB) Apoptosis\nC) Lysis\nD) Autophagy", "a": "B",
         "knowledge": "Apoptosis is programmed cell death — an orderly, energy-dependent process where the cell dismantles itself. Unlike necrosis (uncontrolled death), apoptosis doesn't cause inflammation."},
        {"q": "Which macromolecule serves as the primary long-term energy storage in animals?\nA) Proteins\nB) Carbohydrates\nC) Lipids (fats)\nD) Nucleic acids", "a": "C",
         "knowledge": "Lipids (fats/triglycerides) are the primary long-term energy storage in animals, providing 9 kcal/gram compared to 4 kcal/gram for carbohydrates. Glycogen is short-term storage."},
        {"q": "The Hardy-Weinberg equilibrium assumes:\nA) Natural selection is occurring\nB) No mutation, migration, selection, drift, or non-random mating\nC) A small population size\nD) Genetic drift is the main evolutionary force", "a": "B",
         "knowledge": "Hardy-Weinberg equilibrium requires: no mutation, no migration, no selection, random mating, and infinite population size. Any violation causes allele frequency changes (evolution)."},
    ],
    "general_science": [
        {"q": "Water boils at what temperature at standard atmospheric pressure?\nA) 90°C\nB) 100°C\nC) 110°C\nD) 120°C", "a": "B",
         "knowledge": "Water boils at 100°C (212°F) at standard atmospheric pressure (1 atm or 101.325 kPa). Boiling point changes with pressure — lower at high altitude, higher in a pressure cooker."},
        {"q": "The chemical formula for table salt is:\nA) NaOH\nB) NaCl\nC) KCl\nD) CaCl2", "a": "B",
         "knowledge": "Table salt is sodium chloride (NaCl), an ionic compound formed from sodium (Na+) and chloride (Cl-) ions in a crystal lattice structure."},
        {"q": "Which planet is closest to the Sun?\nA) Venus\nB) Earth\nC) Mercury\nD) Mars", "a": "C",
         "knowledge": "Mercury is the closest planet to the Sun at an average distance of 57.9 million km. Despite this, Venus is hotter due to its thick CO2 atmosphere creating a greenhouse effect."},
        {"q": "The speed of light in a vacuum is approximately:\nA) 3 × 10^6 m/s\nB) 3 × 10^8 m/s\nC) 3 × 10^10 m/s\nD) 3 × 10^12 m/s", "a": "B",
         "knowledge": "The speed of light in vacuum (c) is approximately 299,792,458 m/s, or about 3 × 10^8 m/s. It is a fundamental constant and the maximum speed at which information can travel."},
        {"q": "Newton's second law of motion states that:\nA) Every action has an equal and opposite reaction\nB) F = ma (force equals mass times acceleration)\nC) An object at rest stays at rest\nD) Energy is conserved", "a": "B",
         "knowledge": "Newton's second law: F = ma. The net force on an object equals its mass multiplied by its acceleration. This is the fundamental equation connecting force, mass, and motion."},
        {"q": "The pH of pure water at 25°C is:\nA) 0\nB) 5\nC) 7\nD) 14", "a": "C",
         "knowledge": "Pure water has a pH of 7 at 25°C, which is neutral. pH = -log[H+]. In pure water, [H+] = [OH-] = 10^-7 M. pH < 7 is acidic, pH > 7 is basic."},
        {"q": "Which element has the atomic number 1?\nA) Helium\nB) Hydrogen\nC) Lithium\nD) Carbon", "a": "B",
         "knowledge": "Hydrogen has atomic number 1, meaning it has 1 proton. It's the lightest and most abundant element in the universe, making up about 75% of normal matter by mass."},
        {"q": "Sound travels fastest through:\nA) Air\nB) Water\nC) Vacuum\nD) Steel", "a": "D",
         "knowledge": "Sound travels fastest through solids (steel ~5,960 m/s), then liquids (water ~1,480 m/s), then gases (air ~343 m/s). Sound cannot travel through a vacuum — it requires a medium."},
        {"q": "The law of conservation of energy states that:\nA) Energy always decreases over time\nB) Energy can be created from nothing\nC) Energy cannot be created or destroyed, only transformed\nD) Potential energy always equals kinetic energy", "a": "C",
         "knowledge": "The law of conservation of energy: energy cannot be created or destroyed, only converted from one form to another. The total energy of an isolated system remains constant."},
        {"q": "What force keeps the Moon in orbit around Earth?\nA) Electromagnetic force\nB) Strong nuclear force\nC) Gravitational force\nD) Centrifugal force", "a": "C",
         "knowledge": "Gravity keeps the Moon in orbit around Earth. The gravitational force provides the centripetal acceleration needed for orbital motion. F = GMm/r² (Newton's law of gravitation)."},
        {"q": "Photosynthesis produces oxygen as a byproduct from:\nA) Carbon dioxide\nB) Glucose\nC) Water\nD) Sunlight", "a": "C",
         "knowledge": "The oxygen released in photosynthesis comes from water (H2O) molecules, not CO2. Light reactions split water (photolysis): 2H2O → 4H+ + 4e- + O2."},
        {"q": "The number of chromosomes in a normal human somatic cell is:\nA) 23\nB) 44\nC) 46\nD) 48", "a": "C",
         "knowledge": "Human somatic cells have 46 chromosomes (23 pairs — 22 autosomal pairs + 1 pair of sex chromosomes). Gametes (sperm/egg) have 23 chromosomes (haploid)."},
        {"q": "Diamond and graphite are both made of:\nA) Silicon\nB) Carbon\nC) Iron\nD) Aluminum", "a": "B",
         "knowledge": "Diamond and graphite are allotropes of carbon — same element, different crystal structures. Diamond has tetrahedral sp3 bonding (hard). Graphite has planar sp2 layers (soft, conducts electricity)."},
        {"q": "What is the most abundant gas in Earth's atmosphere?\nA) Oxygen\nB) Carbon dioxide\nC) Nitrogen\nD) Argon", "a": "C",
         "knowledge": "Nitrogen (N2) makes up approximately 78% of Earth's atmosphere. Oxygen is second at ~21%. Argon is ~0.93%. CO2 is only ~0.04%."},
        {"q": "An acid has a pH:\nA) Greater than 7\nB) Equal to 7\nC) Less than 7\nD) Equal to 14", "a": "C",
         "knowledge": "Acids have pH less than 7. The lower the pH, the stronger the acid. pH 0-6 is acidic, pH 7 is neutral, pH 8-14 is basic/alkaline."},
        {"q": "The SI unit of electric current is:\nA) Volt\nB) Watt\nC) Ampere\nD) Ohm", "a": "C",
         "knowledge": "The ampere (A) is the SI base unit of electric current. It measures the rate of flow of electric charge. 1 ampere = 1 coulomb per second."},
        {"q": "Which subatomic particle has a negative charge?\nA) Proton\nB) Neutron\nC) Electron\nD) Photon", "a": "C",
         "knowledge": "Electrons have a negative charge of -1.6 × 10^-19 coulombs. Protons are positive, neutrons are neutral, and photons have no charge."},
        {"q": "Rust is formed by the reaction of iron with:\nA) Nitrogen\nB) Oxygen and water\nC) Carbon dioxide\nD) Hydrogen", "a": "B",
         "knowledge": "Rust (iron(III) oxide-hydroxide, Fe2O3·nH2O) forms when iron reacts with oxygen and water in a process called corrosion. Both oxygen and moisture are required."},
        {"q": "The half-life of a radioactive substance is the time it takes for:\nA) All atoms to decay\nB) Half of the atoms to decay\nC) The substance to become stable\nD) The radiation to halve in intensity only", "a": "B",
         "knowledge": "Half-life is the time required for half of the radioactive atoms in a sample to decay. After one half-life, 50% remains; after two half-lives, 25%; after three, 12.5%."},
        {"q": "Which type of electromagnetic radiation has the highest frequency?\nA) Radio waves\nB) Infrared\nC) Visible light\nD) Gamma rays", "a": "D",
         "knowledge": "Gamma rays have the highest frequency (and energy) in the EM spectrum. Order from lowest to highest frequency: radio < microwave < infrared < visible < ultraviolet < X-ray < gamma."},
    ],
    "history_geography": [
        {"q": "Which continent is the Sahara Desert located on?\nA) Asia\nB) South America\nC) Africa\nD) Australia", "a": "C",
         "knowledge": "The Sahara Desert is in Africa, spanning approximately 9.2 million km². It is the world's largest hot desert, covering most of North Africa across 11 countries."},
        {"q": "World War II ended in which year?\nA) 1943\nB) 1944\nC) 1945\nD) 1946", "a": "C",
         "knowledge": "World War II ended in 1945. Germany surrendered on May 8 (V-E Day) and Japan surrendered on September 2 (V-J Day) after the atomic bombings of Hiroshima and Nagasaki."},
        {"q": "The capital of Japan is:\nA) Osaka\nB) Kyoto\nC) Tokyo\nD) Yokohama", "a": "C",
         "knowledge": "Tokyo is the capital of Japan and its most populous city with over 13 million people in the city proper. It became the capital in 1868 when the Emperor moved from Kyoto."},
        {"q": "The longest river in the world is:\nA) Amazon\nB) Mississippi\nC) Yangtze\nD) Nile", "a": "D",
         "knowledge": "The Nile River is traditionally considered the longest river at approximately 6,650 km, flowing through 11 African countries from Burundi to Egypt's Mediterranean coast."},
        {"q": "Who was the first President of the United States?\nA) Thomas Jefferson\nB) John Adams\nC) George Washington\nD) Benjamin Franklin", "a": "C",
         "knowledge": "George Washington served as the first President of the United States from 1789 to 1797. He was unanimously elected by the Electoral College for both terms."},
        {"q": "The Great Wall of China was primarily built to:\nA) Facilitate trade routes\nB) Serve as a road system\nC) Defend against northern invasions\nD) Mark provincial boundaries", "a": "C",
         "knowledge": "The Great Wall was built to protect Chinese states and empires against invasions from northern nomadic groups. Construction spanned many centuries, with the most well-known sections built during the Ming Dynasty."},
        {"q": "Which ocean is the largest by area?\nA) Atlantic\nB) Indian\nC) Arctic\nD) Pacific", "a": "D",
         "knowledge": "The Pacific Ocean is the largest ocean, covering approximately 165.25 million km² — more than all land area combined. It spans from the Americas to Asia and Australia."},
        {"q": "The French Revolution began in which year?\nA) 1776\nB) 1789\nC) 1799\nD) 1804", "a": "B",
         "knowledge": "The French Revolution began in 1789 with the storming of the Bastille on July 14. It overthrew the monarchy and established republican ideals of liberty, equality, fraternity."},
        {"q": "Mount Everest is located in which mountain range?\nA) Alps\nB) Andes\nC) Himalayas\nD) Rocky Mountains", "a": "C",
         "knowledge": "Mount Everest (8,849 m) is in the Himalayas, on the border between Nepal and Tibet (China). It is the highest peak above sea level on Earth."},
        {"q": "The Industrial Revolution began in which country?\nA) France\nB) Germany\nC) United States\nD) Great Britain", "a": "D",
         "knowledge": "The Industrial Revolution began in Great Britain in the mid-18th century (1760s), driven by innovations in textile manufacturing, steam power, and iron production."},
        {"q": "Which country has the largest population in the world?\nA) United States\nB) India\nC) China\nD) Indonesia", "a": "B",
         "knowledge": "As of 2023-2024, India has surpassed China as the world's most populous country with approximately 1.44 billion people. China is second at approximately 1.43 billion."},
        {"q": "The Amazon Rainforest is primarily located in:\nA) Africa\nB) Southeast Asia\nC) South America\nD) Central America", "a": "C",
         "knowledge": "The Amazon Rainforest covers approximately 5.5 million km² in South America, with about 60% in Brazil. It's the world's largest tropical rainforest and produces about 6% of the world's oxygen."},
        {"q": "The Berlin Wall fell in which year?\nA) 1987\nB) 1989\nC) 1991\nD) 1993", "a": "B",
         "knowledge": "The Berlin Wall fell on November 9, 1989, marking the symbolic end of the Cold War. Germany was officially reunified on October 3, 1990."},
        {"q": "Which ancient civilization built the pyramids at Giza?\nA) Roman\nB) Greek\nC) Egyptian\nD) Mesopotamian", "a": "C",
         "knowledge": "The ancient Egyptians built the Giza pyramids around 2560 BCE during the Fourth Dynasty. The Great Pyramid (Khufu/Cheops) was the tallest man-made structure for over 3,800 years."},
        {"q": "The equator passes through how many continents?\nA) 2\nB) 3\nC) 4\nD) 5", "a": "B",
         "knowledge": "The equator passes through 3 continents: South America (Brazil, Ecuador, Colombia), Africa (multiple countries), and Asia (Indonesia). It does not pass through North America, Europe, Australia, or Antarctica."},
        {"q": "Who wrote the Declaration of Independence?\nA) Benjamin Franklin\nB) John Adams\nC) Thomas Jefferson\nD) James Madison", "a": "C",
         "knowledge": "Thomas Jefferson was the principal author of the Declaration of Independence, adopted on July 4, 1776. He wrote it over about 17 days with input from a committee including Franklin and Adams."},
        {"q": "The Strait of Gibraltar connects the Atlantic Ocean to:\nA) Pacific Ocean\nB) Indian Ocean\nC) Mediterranean Sea\nD) Red Sea", "a": "C",
         "knowledge": "The Strait of Gibraltar connects the Atlantic Ocean to the Mediterranean Sea, separating Spain (Europe) from Morocco (Africa). It is approximately 14 km wide at its narrowest point."},
        {"q": "The Renaissance began in which country?\nA) France\nB) England\nC) Spain\nD) Italy", "a": "D",
         "knowledge": "The Renaissance began in Italy in the 14th century, particularly in Florence. It was a cultural movement emphasizing art, science, and classical learning, spreading throughout Europe."},
        {"q": "Which desert is the largest cold desert in the world?\nA) Gobi\nB) Sahara\nC) Antarctic\nD) Patagonian", "a": "C",
         "knowledge": "Antarctica is the world's largest desert (cold desert) at 14.2 million km², receiving less than 200mm of precipitation annually. The Sahara is the largest hot desert."},
        {"q": "The Magna Carta was signed in which year?\nA) 1066\nB) 1215\nC) 1492\nD) 1776", "a": "B",
         "knowledge": "The Magna Carta was sealed in 1215 by King John of England at Runnymede. It established that everyone, including the king, was subject to the law — a foundation of constitutional governance."},
    ],
    "math_logic": [
        {"q": "What is the value of π (pi) to two decimal places?\nA) 3.12\nB) 3.14\nC) 3.16\nD) 3.18", "a": "B",
         "knowledge": "Pi (π) ≈ 3.14159265... It is the ratio of a circle's circumference to its diameter. It is an irrational and transcendental number."},
        {"q": "What is 15% of 200?\nA) 15\nB) 20\nC) 30\nD) 35", "a": "C",
         "knowledge": "15% of 200 = 0.15 × 200 = 30. To calculate percentages: convert percent to decimal (divide by 100), then multiply."},
        {"q": "If x + 5 = 12, what is x?\nA) 5\nB) 7\nC) 12\nD) 17", "a": "B",
         "knowledge": "x + 5 = 12. Subtract 5 from both sides: x = 12 - 5 = 7. This is a basic linear equation solved by isolating the variable."},
        {"q": "The square root of 144 is:\nA) 11\nB) 12\nC) 13\nD) 14", "a": "B",
         "knowledge": "√144 = 12, because 12 × 12 = 144. 144 is a perfect square. The square root function finds the number that when multiplied by itself gives the input."},
        {"q": "What is the area of a circle with radius 5?\nA) 10π\nB) 25π\nC) 50π\nD) 5π", "a": "B",
         "knowledge": "Area of a circle = πr². With r = 5: A = π(5)² = 25π ≈ 78.54. The formula comes from integrating the circumference (2πr) from 0 to r."},
        {"q": "In a right triangle, if the two legs are 3 and 4, the hypotenuse is:\nA) 5\nB) 6\nC) 7\nD) 12", "a": "A",
         "knowledge": "By the Pythagorean theorem: c² = a² + b² = 9 + 16 = 25, so c = 5. The 3-4-5 triangle is the most common Pythagorean triple."},
        {"q": "What is 2^10?\nA) 512\nB) 1024\nC) 2048\nD) 4096", "a": "B",
         "knowledge": "2^10 = 1024. Powers of 2: 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024. This is important in computing — 1 KB = 1024 bytes."},
        {"q": "The sum of angles in a triangle is:\nA) 90°\nB) 180°\nC) 270°\nD) 360°", "a": "B",
         "knowledge": "The sum of interior angles in any triangle is always 180° (in Euclidean geometry). This is a fundamental theorem proved by drawing a line parallel to one side."},
        {"q": "What is the derivative of x²?\nA) x\nB) 2x\nC) x³\nD) 2", "a": "B",
         "knowledge": "The derivative of x² is 2x, using the power rule: d/dx[x^n] = nx^(n-1). So d/dx[x²] = 2x^1 = 2x."},
        {"q": "If a coin is flipped twice, what is the probability of getting two heads?\nA) 1/2\nB) 1/3\nC) 1/4\nD) 1/8", "a": "C",
         "knowledge": "P(HH) = P(H) × P(H) = 1/2 × 1/2 = 1/4. With two flips, there are 4 equally likely outcomes: HH, HT, TH, TT. Only one is two heads."},
        {"q": "The factorial of 5 (5!) equals:\nA) 25\nB) 60\nC) 120\nD) 720", "a": "C",
         "knowledge": "5! = 5 × 4 × 3 × 2 × 1 = 120. Factorial is the product of all positive integers up to n. It's used in permutations and combinations."},
        {"q": "What is the slope of a horizontal line?\nA) 1\nB) -1\nC) 0\nD) Undefined", "a": "C",
         "knowledge": "A horizontal line has slope 0 because there is no change in y (rise = 0). Slope = rise/run = 0/run = 0. A vertical line has undefined slope (run = 0)."},
        {"q": "log₁₀(1000) equals:\nA) 2\nB) 3\nC) 4\nD) 10", "a": "B",
         "knowledge": "log₁₀(1000) = 3 because 10³ = 1000. Logarithm answers: 'to what power must the base be raised to get this number?' log₁₀(10) = 1, log₁₀(100) = 2, log₁₀(1000) = 3."},
        {"q": "The Fibonacci sequence starts 1, 1, 2, 3, 5, 8. What comes next?\nA) 11\nB) 12\nC) 13\nD) 14", "a": "C",
         "knowledge": "The next Fibonacci number is 5 + 8 = 13. Each Fibonacci number is the sum of the two preceding numbers: F(n) = F(n-1) + F(n-2)."},
        {"q": "How many degrees are in a full circle?\nA) 180°\nB) 270°\nC) 360°\nD) 720°", "a": "C",
         "knowledge": "A full circle contains 360 degrees. This convention dates to ancient Babylonian mathematics (base-60 number system). In radians, a full circle is 2π."},
        {"q": "What is the least common multiple (LCM) of 4 and 6?\nA) 2\nB) 10\nC) 12\nD) 24", "a": "C",
         "knowledge": "LCM(4, 6) = 12. The LCM is the smallest number divisible by both. Multiples of 4: 4, 8, 12... Multiples of 6: 6, 12... First common: 12."},
        {"q": "If f(x) = 3x + 2, what is f(4)?\nA) 12\nB) 14\nC) 16\nD) 18", "a": "B",
         "knowledge": "f(4) = 3(4) + 2 = 12 + 2 = 14. Function evaluation: substitute the input value for x and compute."},
        {"q": "What is the median of the set {3, 7, 9, 1, 5}?\nA) 3\nB) 5\nC) 7\nD) 9", "a": "B",
         "knowledge": "Sort the set: {1, 3, 5, 7, 9}. The median is the middle value = 5. For an odd number of values, it's the central element. For even, it's the average of the two central elements."},
        {"q": "A right angle measures:\nA) 45°\nB) 60°\nC) 90°\nD) 180°", "a": "C",
         "knowledge": "A right angle measures exactly 90°. It's formed when two perpendicular lines meet. Acute angles are <90°, obtuse angles are >90° and <180°, straight angles are 180°."},
        {"q": "What is the greatest common divisor (GCD) of 24 and 36?\nA) 6\nB) 8\nC) 12\nD) 18", "a": "C",
         "knowledge": "GCD(24, 36) = 12. Factors of 24: 1,2,3,4,6,8,12,24. Factors of 36: 1,2,3,4,6,9,12,18,36. Largest common: 12. Can also use Euclidean algorithm: 36 = 1×24 + 12, 24 = 2×12 + 0."},
    ],
    "everyday_knowledge": [
        {"q": "How many days are in a leap year?\nA) 364\nB) 365\nC) 366\nD) 367", "a": "C",
         "knowledge": "A leap year has 366 days (the extra day is February 29). Leap years occur every 4 years, except centuries unless divisible by 400."},
        {"q": "Which metal is liquid at room temperature?\nA) Iron\nB) Mercury\nC) Aluminum\nD) Copper", "a": "B",
         "knowledge": "Mercury (Hg) is the only metal that is liquid at standard room temperature (25°C). Its melting point is -38.83°C. Gallium melts at 29.76°C (just above room temp)."},
        {"q": "How many continents are there on Earth?\nA) 5\nB) 6\nC) 7\nD) 8", "a": "C",
         "knowledge": "There are 7 continents: Africa, Antarctica, Asia, Australia/Oceania, Europe, North America, and South America. Asia is the largest by both area and population."},
        {"q": "The currency of the United Kingdom is:\nA) Euro\nB) Dollar\nC) Pound Sterling\nD) Franc", "a": "C",
         "knowledge": "The United Kingdom uses the Pound Sterling (GBP, £). Despite being in Europe, the UK never adopted the Euro. The pound is one of the oldest currencies still in use."},
        {"q": "What is the chemical symbol for gold?\nA) Go\nB) Gd\nC) Au\nD) Ag", "a": "C",
         "knowledge": "Gold's chemical symbol is Au, from the Latin 'aurum' meaning 'shining dawn.' Silver is Ag (from 'argentum'). Gold has atomic number 79."},
        {"q": "How many teeth does a typical adult human have?\nA) 28\nB) 30\nC) 32\nD) 34", "a": "C",
         "knowledge": "Adult humans typically have 32 teeth: 8 incisors, 4 canines, 8 premolars, and 12 molars (including 4 wisdom teeth). Some people have 28 if wisdom teeth are removed or absent."},
        {"q": "The Eiffel Tower is located in which city?\nA) London\nB) Berlin\nC) Rome\nD) Paris", "a": "D",
         "knowledge": "The Eiffel Tower is in Paris, France. Built in 1889 for the World's Fair, it stands 330 meters tall and was the world's tallest structure until 1930."},
        {"q": "Which vitamin is also known as ascorbic acid?\nA) Vitamin A\nB) Vitamin B12\nC) Vitamin C\nD) Vitamin E", "a": "C",
         "knowledge": "Vitamin C is ascorbic acid. It's essential for collagen synthesis, immune function, and acts as an antioxidant. Deficiency causes scurvy. Found in citrus fruits and vegetables."},
        {"q": "The human body is approximately what percentage water?\nA) 30%\nB) 45%\nC) 60%\nD) 80%", "a": "C",
         "knowledge": "The adult human body is approximately 60% water by weight. This varies with age, sex, and body composition. The brain and heart are about 73% water, lungs about 83%."},
        {"q": "What is the tallest mammal?\nA) Elephant\nB) Giraffe\nC) Blue whale\nD) Brown bear", "a": "B",
         "knowledge": "The giraffe is the tallest living mammal, reaching heights of 5-6 meters (16-19 feet). Their long necks contain only 7 vertebrae (same as humans), but each can be over 25 cm long."},
        {"q": "The boiling point of water in Fahrenheit is:\nA) 100°F\nB) 180°F\nC) 212°F\nD) 250°F", "a": "C",
         "knowledge": "Water boils at 212°F (100°C) at standard atmospheric pressure. The Fahrenheit scale sets water's freezing at 32°F and boiling at 212°F, a span of 180 degrees."},
        {"q": "Which country gifted the Statue of Liberty to the United States?\nA) England\nB) Germany\nC) France\nD) Spain", "a": "C",
         "knowledge": "France gifted the Statue of Liberty to the United States in 1886 as a symbol of friendship and shared values of freedom. It was designed by Frédéric Auguste Bartholdi."},
        {"q": "How many strings does a standard guitar have?\nA) 4\nB) 5\nC) 6\nD) 8", "a": "C",
         "knowledge": "A standard guitar has 6 strings, tuned E-A-D-G-B-E from lowest to highest. Bass guitars typically have 4 strings. 12-string guitars double each string."},
        {"q": "The freezing point of water in Celsius is:\nA) -10°C\nB) 0°C\nC) 10°C\nD) 32°C", "a": "B",
         "knowledge": "Water freezes at 0°C (32°F) at standard atmospheric pressure. This is one of the defining points of the Celsius scale (the other being 100°C for boiling)."},
        {"q": "What is the largest organ inside the human body?\nA) Heart\nB) Brain\nC) Liver\nD) Stomach", "a": "C",
         "knowledge": "The liver is the largest internal organ, weighing about 1.5 kg in adults. It performs over 500 functions including detoxification, bile production, protein synthesis, and glycogen storage."},
        {"q": "Which gas do plants primarily absorb from the atmosphere?\nA) Oxygen\nB) Nitrogen\nC) Carbon dioxide\nD) Hydrogen", "a": "C",
         "knowledge": "Plants absorb carbon dioxide (CO2) from the atmosphere through stomata in their leaves. CO2 is used in photosynthesis along with water and light to produce glucose and oxygen."},
        {"q": "A decade is how many years?\nA) 5\nB) 10\nC) 50\nD) 100", "a": "B",
         "knowledge": "A decade is 10 years. A century is 100 years. A millennium is 1,000 years. A score is 20 years (as in Lincoln's 'four score and seven years ago' = 87 years)."},
        {"q": "The hardest natural substance is:\nA) Gold\nB) Iron\nC) Diamond\nD) Quartz", "a": "C",
         "knowledge": "Diamond is the hardest natural substance, rated 10 on the Mohs hardness scale. It's made of carbon atoms in a tetrahedral crystal lattice. Only synthetic materials like aggregated diamond nanorods are harder."},
        {"q": "How many sides does a hexagon have?\nA) 5\nB) 6\nC) 7\nD) 8", "a": "B",
         "knowledge": "A hexagon has 6 sides. The prefix 'hex' means six. Pentagon = 5, hexagon = 6, heptagon = 7, octagon = 8. Regular hexagons tessellate perfectly (honeycombs)."},
        {"q": "Which planet in our solar system is known for its rings?\nA) Jupiter\nB) Mars\nC) Saturn\nD) Neptune", "a": "C",
         "knowledge": "Saturn is famous for its prominent ring system, made primarily of ice particles and rocky debris. While Jupiter, Uranus, and Neptune also have rings, Saturn's are the most visible and extensive."},
    ],
}

# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("GROUNDING EFFECT DEEP DIVE")
    print(f"Model: {MODEL_ID}")
    print(f"Questions: {sum(len(v) for v in QUESTIONS.values())} across {len(QUESTIONS)} subjects")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 70)

    print("\nLoading model...")
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.float16).to("cuda")
    model.eval()
    print(f"Model loaded. VRAM: {torch.cuda.memory_allocated()/1e9:.1f}GB")
    _ = query_baseline("Hello", tok, model, max_new=5)
    print("Warmup done.\n")

    all_results = {"model": MODEL_ID, "timestamp": datetime.now().isoformat(), "subjects": {}}
    grand = {"base": 0, "think": 0, "palace_think": 0, "palace_nothink": 0, "total": 0}
    grand["overthink_flips"] = 0  # correct→wrong with thinking
    grand["grounding_rescues"] = 0  # flipped by thinking, rescued by palace+think

    for subj, questions in QUESTIONS.items():
        print(f"\n{'='*60}")
        print(f"  {subj.upper()} ({len(questions)} questions)")
        print(f"{'='*60}")

        stats = {"base": 0, "think": 0, "palace_think": 0, "palace_nothink": 0,
                 "total": len(questions), "overthink_flips": 0, "grounding_rescues": 0}
        details = []

        for i, q in enumerate(questions):
            qtext = q["q"]
            correct = q["a"]
            print(f"\n  Q{i+1}: {qtext[:55]}... [correct={correct}]", end="", flush=True)

            # Condition 1: Baseline (no think)
            r1, l1 = query_baseline(qtext, tok, model, max_new=50, thinking=False)
            a1 = extract_mcq(r1)
            p1 = a1 == correct
            stats["base"] += int(p1)

            # Condition 2: Thinking (no cache)
            r2, l2 = query_baseline(qtext, tok, model, max_new=2048, thinking=True)
            a2 = extract_mcq(r2)
            p2 = a2 == correct
            stats["think"] += int(p2)

            # Track overthinking
            flipped = p1 and not p2  # was right, now wrong
            if flipped:
                stats["overthink_flips"] += 1

            # Condition 3: Palace + Think
            cache, pl = build_cache(q["knowledge"], tok, model)
            r3, l3 = query_palace(qtext, cache, pl, tok, model, max_new=2048, thinking=True)
            a3 = extract_mcq(r3)
            p3 = a3 == correct
            stats["palace_think"] += int(p3)

            # Track grounding rescue
            rescued = flipped and p3  # was flipped by thinking, rescued by palace
            if rescued:
                stats["grounding_rescues"] += 1

            # Condition 4: Palace (no think) — isolate palace effect
            r4, l4 = query_palace(qtext, cache, pl, tok, model, max_new=50, thinking=False)
            a4 = extract_mcq(r4)
            p4 = a4 == correct
            stats["palace_nothink"] += int(p4)

            del cache; gc.collect(); torch.cuda.empty_cache()

            sym = lambda x: "✓" if x else "✗"
            flag = " ← OVERTHINK" if flipped else ""
            flag += " ← RESCUED" if rescued else ""
            print(f"\n    Base:{a1}{sym(p1)} Think:{a2}{sym(p2)} P+T:{a3}{sym(p3)} P:{a4}{sym(p4)}{flag}")

            details.append({
                "question": qtext[:80], "correct": correct,
                "base": {"answer": a1, "correct": p1, "latency": l1},
                "think": {"answer": a2, "correct": p2, "latency": l2},
                "palace_think": {"answer": a3, "correct": p3, "latency": l3},
                "palace_nothink": {"answer": a4, "correct": p4, "latency": l4},
                "overthink_flip": flipped, "grounding_rescue": rescued,
            })

        n = stats["total"]
        print(f"\n  --- {subj.upper()} SUMMARY ---")
        print(f"  Base: {stats['base']}/{n} ({stats['base']/n*100:.0f}%)")
        print(f"  Think: {stats['think']}/{n} ({stats['think']/n*100:.0f}%)")
        print(f"  Palace+Think: {stats['palace_think']}/{n} ({stats['palace_think']/n*100:.0f}%)")
        print(f"  Palace (no think): {stats['palace_nothink']}/{n} ({stats['palace_nothink']/n*100:.0f}%)")
        print(f"  Overthink flips: {stats['overthink_flips']}/{n}")
        print(f"  Grounding rescues: {stats['grounding_rescues']}/{stats['overthink_flips'] if stats['overthink_flips'] else 1}")

        all_results["subjects"][subj] = {**stats, "details": details}
        for k in grand:
            if k in stats:
                grand[k] += stats[k]

    # Grand summary
    n = grand["total"]
    print(f"\n{'='*60}")
    print(f"  GRAND SUMMARY ({n} questions)")
    print(f"{'='*60}")
    print(f"  Baseline:       {grand['base']}/{n} ({grand['base']/n*100:.1f}%)")
    print(f"  Thinking:       {grand['think']}/{n} ({grand['think']/n*100:.1f}%)")
    print(f"  Palace+Think:   {grand['palace_think']}/{n} ({grand['palace_think']/n*100:.1f}%)")
    print(f"  Palace (no-think): {grand['palace_nothink']}/{n} ({grand['palace_nothink']/n*100:.1f}%)")
    print(f"\n  Overthink rate: {grand['overthink_flips']}/{grand['base']} ({grand['overthink_flips']/max(grand['base'],1)*100:.1f}% of correct answers flipped)")
    print(f"  Rescue rate:    {grand['grounding_rescues']}/{grand['overthink_flips']} ({grand['grounding_rescues']/max(grand['overthink_flips'],1)*100:.1f}% of flipped answers rescued)")

    all_results["grand_summary"] = grand
    all_results["completed"] = datetime.now().isoformat()

    out = RESULTS_DIR / "grounding_effect_100q.json"
    with open(out, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved: {out}")
    print(f"Finished: {datetime.now().isoformat()}")
