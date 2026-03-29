#!/usr/bin/env python3
"""Memory Palace Batch 2 — Novel Reasoning + Industry Benchmarks"""
from __future__ import annotations
import os, json, time, gc, re, sys
from pathlib import Path
from datetime import datetime

os.environ["HSA_OVERRIDE_GFX_VERSION"] = "11.0.0"

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, DynamicCache

RESULTS_DIR = Path("/mnt/fastpool/atlas-distillation/results/memory_palace/batch2")
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

def query_palace(q, cache, prefix_len, tok, model, max_new=300, thinking=False):
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
    if thinking:
        resp = tok.decode(out[0][ql:], skip_special_tokens=False)
    else:
        resp = tok.decode(out[0][ql:], skip_special_tokens=True)
    return resp, lat

def query_baseline(q, tok, model, max_new=300, thinking=False):
    msgs = [{"role": "user", "content": q}]
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=thinking)
    ids = tok(text, return_tensors="pt").input_ids.to("cuda")
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=max_new, do_sample=False)
    lat = time.time() - t0
    if thinking:
        resp = tok.decode(out[0][ids.shape[1]:], skip_special_tokens=False)
    else:
        resp = tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True)
    return resp, lat

def query_rag(q, knowledge, tok, model, max_new=300, thinking=False):
    msgs = [{"role": "system", "content": knowledge}, {"role": "user", "content": q}]
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=thinking)
    ids = tok(text, return_tensors="pt").input_ids.to("cuda")
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=max_new, do_sample=False)
    lat = time.time() - t0
    if thinking:
        resp = tok.decode(out[0][ids.shape[1]:], skip_special_tokens=False)
    else:
        resp = tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True)
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

def kw_score(response, keywords):
    r = response.lower()
    found = [k for k in keywords if k.lower() in r]
    return len(found), found

# ── Track A: Novel Reasoning ─────────────────────────────────────────

REASONING_SCENARIOS = [
    {
        "id": "drug-interaction",
        "title": "Warfarin + NeoCardiol Interaction",
        "knowledge": """NeoCardiol (NCD-4417) Prescribing Information — Drug Interactions:
NeoCardiol is metabolized primarily by CYP3A4 (60%) and CYP2D6 (30%). It is a moderate inhibitor of CYP2C9.
Warfarin: NeoCardiol increases warfarin exposure by 45% via CYP2C9 inhibition. INR monitoring must increase
from monthly to weekly for the first 8 weeks. Warfarin dose reduction of 25-35% is typically required.
Onset of interaction: 3-5 days after NeoCardiol initiation. Peak effect: 10-14 days.
Risk category: HIGH. Cases of INR >5 reported in 8% of co-administered patients in post-marketing surveillance.
Bleeding events requiring hospitalization: 2.1% vs 0.4% with warfarin alone.
Recommendation: Reduce warfarin dose by 25% at NeoCardiol start, check INR at day 3, 7, 14, then weekly × 6 weeks.""",
        "question": "A 72-year-old patient stable on warfarin (INR 2.3) is starting NeoCardiol for heart failure. What specific dose adjustments and monitoring changes are needed? Include timeline.",
        "scoring_keywords": ["25%", "CYP2C9", "weekly", "INR", "3", "7", "14", "8 weeks", "reduce", "45%"],
        "min_keywords": 5,
    },
    {
        "id": "dosing-calculation",
        "title": "Pediatric FloraRestore Dosing",
        "knowledge": """FloraRestore (FR-8821) Pediatric Dosing Guide:
Weight-based dosing for rCDI:
- <15 kg: 1 capsule daily × 3 days (half adult dose)
- 15-30 kg: 2 capsules daily × 3 days
- 30-50 kg: 3 capsules daily × 3 days
- >50 kg: 4 capsules daily × 3 days (adult dose)
Administration: Each capsule contains 1.2 × 10^10 CFU. Take on empty stomach, 2 hours after last antibiotic.
Must complete full vancomycin taper (125mg QID × 10d, then 125mg BID × 7d, then 125mg daily × 7d) before starting.
Contraindications: Immunocompromised (ANC <500), active GI bleeding, bowel perforation.
Monitoring: Stool C. diff toxin at week 4 and week 8. If positive at week 8, may repeat course once.
Pediatric trial (n=127): Recurrence 11% vs 34% placebo in children 2-17 years.""",
        "question": "A 25 kg child with recurrent C. difficile needs FloraRestore. Calculate the dose, explain the vancomycin taper schedule, and outline the monitoring plan.",
        "scoring_keywords": ["2 capsules", "3 days", "125mg", "vancomycin", "taper", "week 4", "week 8", "toxin", "15-30", "empty stomach"],
        "min_keywords": 5,
    },
    {
        "id": "contraindication",
        "title": "Hepatic Impairment + NeoCardiol",
        "knowledge": """NeoCardiol (NCD-4417) — Hepatic Impairment Guidance:
Hepatotoxicity incidence by liver function:
- Normal liver function: 2% (ALT >3× ULN)
- Mild impairment (Child-Pugh A): 7% — dose reduce to 100mg BID, monitor ALT biweekly
- Moderate impairment (Child-Pugh B): 18% — CONTRAINDICATED per FDA label
- Severe impairment (Child-Pugh C): NOT STUDIED — Absolutely contraindicated
Mechanism: NeoCardiol's active metabolite (NCD-4417-M2) is hepatically cleared. Impaired clearance leads to
M2 accumulation → mitochondrial toxicity → hepatocyte necrosis.
Time to onset: typically 2-6 weeks. Monitoring ALT/AST every 2 weeks for first 3 months.
Alternatives for HFrEF in hepatic impairment: sacubitril/valsartan (no hepatic concerns),
empagliflozin (renal clearance, safe), or ivabradine (mild hepatic metabolism, dose adjust in Child-Pugh A-B).""",
        "question": "A patient with Child-Pugh C cirrhosis and HFrEF (LVEF 28%) is referred for NeoCardiol. Is it safe? What alternatives would you recommend and why?",
        "scoring_keywords": ["contraindicated", "Child-Pugh C", "not studied", "sacubitril", "empagliflozin", "hepatic", "M2", "mitochondrial", "renal clearance"],
        "min_keywords": 4,
    },
    {
        "id": "multi-drug",
        "title": "NeuroSyncX + Polypharmacy",
        "knowledge": """NeuroSyncX DBS Implant — Drug and Device Interactions:
MRI Safety: MRI-conditional at 1.5T ONLY. SAR ≤0.1 W/kg, head coil only. 3T MRI CONTRAINDICATED (risk of
lead heating, tissue damage, device malfunction).
Drug interactions with stimulation parameters:
- Levodopa: NeuroSyncX adaptive algorithm accounts for levodopa on/off states. No dose change needed at implant.
  However, levodopa dose can typically be reduced by 30-40% at 6 months post-implant.
- MAO-B inhibitors (selegiline, rasagiline): SAFE with NeuroSyncX. No interaction.
- Antipsychotics (haloperidol, quetiapine): May reduce DBS efficacy by 20-40% via dopamine blockade.
  If antipsychotic required, use quetiapine (least D2 blockade). Avoid haloperidol.
- Diathermy: CONTRAINDICATED — can cause tissue damage around leads and permanent neurological injury.
- Lithotripsy: CONTRAINDICATED if within 15cm of implant.
- Cardiac pacemakers: Possible electromagnetic interference. Requires cardiology/neurology co-programming.
  Minimum 20cm separation between devices. Monitor for sensing artifacts.""",
        "question": "A NeuroSyncX patient needs an MRI of the lumbar spine and is also being started on haloperidol for agitation. What are the specific risks and what alternatives would you recommend?",
        "scoring_keywords": ["1.5T", "3T", "contraindicated", "head coil", "SAR", "haloperidol", "dopamine", "quetiapine", "20-40%", "D2"],
        "min_keywords": 5,
    },
    {
        "id": "treatment-sequencing",
        "title": "FloraRestore Responder Subgroups",
        "knowledge": """FloraRestore (FR-8821) — Subgroup Analysis from RESTORE-CDI Trial:
Response predictors (8-week recurrence rates):
- Prior recurrences = 2: FloraRestore 5% vs placebo 28% (ARR 23%, NNT 4.3)
- Prior recurrences = 3: FloraRestore 9% vs placebo 35% (ARR 26%, NNT 3.8)
- Prior recurrences ≥4: FloraRestore 18% vs placebo 41% (ARR 23%, NNT 4.3)
- Age <65: FloraRestore 6% vs placebo 29% (ARR 23%)
- Age ≥65: FloraRestore 12% vs placebo 34% (ARR 22%)
- Immunocompromised: FloraRestore 22% vs placebo 48% (ARR 26%, NNT 3.8)
- Strain NAP1/BI/027: FloraRestore 14% vs placebo 42% (ARR 28%, NNT 3.6) — BEST responders
- Prior FMT failure: FloraRestore 16% vs placebo 38% (ARR 22%, NNT 4.5)
Shannon diversity at baseline:
- <1.5 (severely depleted): 11% recurrence (strong engraftment)
- 1.5-2.5 (moderately depleted): 7% recurrence (best outcomes)
- >2.5 (mildly depleted): 14% recurrence (less engraftment room)
Donor strain engraftment correlated with response: 94% in responders vs 61% in non-responders.""",
        "question": "A 70-year-old immunocompromised patient with 4 prior CDI recurrences and a failed FMT is being considered for FloraRestore. Based on the subgroup data, what is the predicted recurrence rate and what factors suggest she may or may not respond?",
        "scoring_keywords": ["18%", "22%", "immunocompromised", "≥4", "FMT", "16%", "engraftment", "Shannon", "NAP1", "age ≥65", "12%"],
        "min_keywords": 5,
    },
]

# ── Track B: Industry Benchmarks ─────────────────────────────────────

BENCHMARK_QUESTIONS = {
    "biology": [
        {"q": "Which protein complex is responsible for the final step of oxidative phosphorylation, directly synthesizing ATP?\nA) Complex I (NADH dehydrogenase)\nB) Complex III (cytochrome bc1)\nC) Complex IV (cytochrome c oxidase)\nD) Complex V (ATP synthase)", "a": "D",
         "knowledge": "In oxidative phosphorylation, the electron transport chain (Complexes I-IV) creates a proton gradient. Complex V (ATP synthase/F1F0-ATPase) uses this gradient to synthesize ATP from ADP + Pi via rotary catalysis. It is the only complex that directly produces ATP."},
        {"q": "In the JAK-STAT signaling pathway, what is the immediate consequence of cytokine binding to its receptor?\nA) STAT proteins are phosphorylated\nB) Receptor-associated JAK kinases are activated via transphosphorylation\nC) STAT dimers translocate to the nucleus\nD) Gene transcription is initiated", "a": "B",
         "knowledge": "When a cytokine binds its receptor, the receptor dimerizes, bringing associated JAK (Janus kinase) proteins into proximity. JAKs then transphosphorylate each other (activate). Only after JAK activation do they phosphorylate the receptor and subsequently STAT proteins."},
        {"q": "CRISPR-Cas9 requires a PAM (protospacer adjacent motif) sequence. For SpCas9, this PAM is:\nA) 5'-NAG-3'\nB) 5'-NGG-3'\nC) 5'-TTN-3'\nD) 5'-TTTN-3'", "a": "B",
         "knowledge": "SpCas9 (from Streptococcus pyogenes) recognizes the PAM sequence 5'-NGG-3' on the non-target strand. Without a PAM, Cas9 cannot bind or cleave DNA. Different Cas proteins use different PAMs — Cas12a uses 5'-TTTN-3'."},
        {"q": "Which epigenetic modification is most strongly associated with transcriptional silencing?\nA) H3K4me3 (histone 3 lysine 4 trimethylation)\nB) H3K27ac (histone 3 lysine 27 acetylation)\nC) H3K27me3 (histone 3 lysine 27 trimethylation)\nD) H3K36me3 (histone 3 lysine 36 trimethylation)", "a": "C",
         "knowledge": "H3K27me3 is catalyzed by PRC2 (Polycomb Repressive Complex 2) and is the hallmark of transcriptional silencing. H3K4me3 marks active promoters. H3K27ac marks active enhancers. H3K36me3 marks actively transcribed gene bodies."},
        {"q": "During V(D)J recombination in B cells, which enzyme introduces the diversity at coding joints through random nucleotide addition?\nA) RAG1/RAG2 recombinase\nB) Terminal deoxynucleotidyl transferase (TdT)\nC) Activation-induced cytidine deaminase (AID)\nD) DNA ligase IV", "a": "B",
         "knowledge": "TdT (terminal deoxynucleotidyl transferase) adds random non-templated nucleotides (N-nucleotides) at the coding joints during V(D)J recombination. RAG1/RAG2 initiates the recombination. AID is involved in somatic hypermutation and class switch recombination, not V(D)J."},
        {"q": "The Warburg effect in cancer cells refers to:\nA) Increased oxidative phosphorylation under hypoxic conditions\nB) Preferential use of aerobic glycolysis even in the presence of oxygen\nC) Enhanced fatty acid oxidation for energy production\nD) Upregulation of the pentose phosphate pathway exclusively", "a": "B",
         "knowledge": "The Warburg effect describes cancer cells' preference for glycolysis over oxidative phosphorylation even when oxygen is abundant (aerobic glycolysis). This produces lactate and provides biosynthetic intermediates for rapid cell division despite being less ATP-efficient."},
        {"q": "Which phase of the cell cycle is targeted by cyclin-dependent kinase inhibitors like p21 and p27?\nA) G1/S transition\nB) S phase progression\nC) G2/M transition\nD) M phase exit", "a": "A",
         "knowledge": "p21 (CDKN1A) and p27 (CDKN1B) are CKIs that primarily inhibit cyclin E-CDK2 and cyclin D-CDK4/6 complexes, blocking the G1/S transition. p21 is a downstream effector of p53 in the DNA damage response."},
        {"q": "In the complement system, the membrane attack complex (MAC) is formed by which components?\nA) C1q, C1r, C1s\nB) C3a, C4a, C5a\nC) C5b, C6, C7, C8, C9\nD) Factor B, Factor D, Properdin", "a": "C",
         "knowledge": "The MAC is formed by C5b initiating assembly of C6, C7, C8, and multiple copies of C9. C9 polymerizes to form a pore in the target cell membrane. C3a/C4a/C5a are anaphylatoxins. C1q/r/s initiate the classical pathway."},
        {"q": "Prions cause disease by:\nA) Encoding a pathogenic RNA that disrupts cellular function\nB) Inducing conformational change in normal PrP^C to misfolded PrP^Sc\nC) Integrating into host DNA and producing toxic proteins\nD) Activating an autoimmune response against neural tissue", "a": "B",
         "knowledge": "Prions are misfolded proteins (PrP^Sc) that template the conformational conversion of normal cellular prion protein (PrP^C) into the pathogenic form. No nucleic acid is involved. The misfolded form is resistant to proteases and accumulates in neural tissue."},
        {"q": "The enzyme telomerase is a:\nA) DNA-dependent DNA polymerase\nB) RNA-dependent DNA polymerase (reverse transcriptase)\nC) DNA-dependent RNA polymerase\nD) RNA-dependent RNA polymerase", "a": "B",
         "knowledge": "Telomerase is a reverse transcriptase — it uses its integral RNA component (TERC) as a template to synthesize telomeric DNA repeats (TTAGGG in humans). It extends the 3' end of chromosomes, counteracting the end-replication problem."},
    ],
    "chemistry": [
        {"q": "In an SN2 reaction, what is the stereochemical outcome at the carbon center?\nA) Retention of configuration\nB) Inversion of configuration (Walden inversion)\nC) Racemization\nD) No change in stereochemistry", "a": "B",
         "knowledge": "SN2 reactions proceed via backside attack, where the nucleophile attacks from the opposite side of the leaving group. This results in complete inversion of configuration (Walden inversion). SN1 reactions, by contrast, go through a planar carbocation and give racemization."},
        {"q": "Which reagent would selectively reduce a ketone in the presence of an ester?\nA) LiAlH4\nB) NaBH4\nC) H2/Pd\nD) Zn(Hg)/HCl", "a": "B",
         "knowledge": "NaBH4 is a mild reducing agent that selectively reduces ketones and aldehydes but does NOT reduce esters, amides, or carboxylic acids. LiAlH4 is a strong reducing agent that reduces all of these. H2/Pd is for catalytic hydrogenation of alkenes."},
        {"q": "The Diels-Alder reaction requires:\nA) A dienophile with electron-donating groups and a diene with electron-withdrawing groups\nB) A diene in the s-cis conformation and an electron-poor dienophile\nC) Both reactants to be in the s-trans conformation\nD) A radical initiator and UV light", "a": "B",
         "knowledge": "The Diels-Alder reaction is a [4+2] cycloaddition requiring: (1) a conjugated diene in the s-cis conformation, and (2) a dienophile, typically with electron-withdrawing groups (EWG). The normal electron demand has electron-rich diene + electron-poor dienophile."},
        {"q": "What is the hybridization of carbon in a carbene (:CH2)?\nA) sp3\nB) sp2\nC) sp\nD) It depends on whether the carbene is singlet or triplet", "a": "D",
         "knowledge": "Carbene hybridization depends on its spin state. Singlet carbenes have sp2 hybridization (paired electrons in one orbital, empty p orbital). Triplet carbenes have sp hybridization (unpaired electrons in two orbitals). Fischer carbenes are singlet; Schrock carbenes can be triplet."},
        {"q": "In NMR spectroscopy, which of these nuclei has spin quantum number I = 0 and is therefore NMR-inactive?\nA) ¹H\nB) ¹²C\nC) ¹³C\nD) ³¹P", "a": "B",
         "knowledge": "¹²C has an even number of protons (6) and neutrons (6), giving it nuclear spin I = 0, making it NMR-inactive. ¹H (I=1/2), ¹³C (I=1/2), and ³¹P (I=1/2) are all NMR-active. Only nuclei with I ≠ 0 can be observed by NMR."},
        {"q": "The Henderson-Hasselbalch equation is pH = pKa + log([A⁻]/[HA]). At what pH does a buffer have maximum capacity?\nA) pH = 7.0\nB) pH = pKa\nC) pH = pKa + 1\nD) pH = pKa - 1", "a": "B",
         "knowledge": "Buffer capacity is maximum when pH = pKa, because at this point [A⁻] = [HA] (equal concentrations of conjugate base and acid). The buffer can equally resist addition of acid or base. Buffer is effective within ±1 pH unit of pKa."},
        {"q": "Which thermodynamic quantity determines spontaneity at constant temperature and pressure?\nA) Enthalpy (ΔH)\nB) Entropy (ΔS)\nC) Gibbs free energy (ΔG)\nD) Internal energy (ΔU)", "a": "C",
         "knowledge": "Gibbs free energy (ΔG = ΔH - TΔS) determines spontaneity at constant T and P. If ΔG < 0, the process is spontaneous. ΔH alone doesn't determine spontaneity (endothermic reactions can be spontaneous if TΔS > ΔH)."},
        {"q": "In crystallography, the Bragg equation nλ = 2d sin(θ) relates:\nA) Crystal symmetry to diffraction intensity\nB) X-ray wavelength to interplanar spacing and diffraction angle\nC) Electron density to structure factor\nD) Unit cell dimensions to space group", "a": "B",
         "knowledge": "Bragg's Law (nλ = 2d sinθ) relates the wavelength (λ) of incident X-rays to the interplanar spacing (d) and the angle of diffraction (θ). Constructive interference occurs when the path difference equals an integer number of wavelengths."},
        {"q": "What is the major product when 2-butene undergoes ozonolysis followed by reductive workup (Zn/AcOH)?\nA) Two equivalents of acetaldehyde\nB) One equivalent of butanal\nC) Two equivalents of formaldehyde\nD) Butanedial", "a": "A",
         "knowledge": "Ozonolysis cleaves C=C double bonds. 2-Butene (CH3-CH=CH-CH3) is symmetric. Cleavage gives two equivalents of acetaldehyde (CH3CHO). Reductive workup (Zn/AcOH or DMS) gives aldehydes; oxidative workup (H2O2) gives carboxylic acids."},
        {"q": "The chelate effect explains why:\nA) Monodentate ligands form more stable complexes than polydentate ligands\nB) Polydentate ligands form more stable complexes than monodentate ligands\nC) Transition metals prefer octahedral geometry\nD) d-d transitions are Laporte forbidden", "a": "B",
         "knowledge": "The chelate effect is the enhanced stability of complexes with polydentate (chelating) ligands vs monodentate ligands. It's driven by entropy — replacing multiple monodentate ligands with one polydentate ligand releases more molecules into solution, increasing entropy."},
    ],
    "medicine": [
        {"q": "A patient presents with sudden-onset severe headache, neck stiffness, and photophobia. CT head is normal. What is the next best step?\nA) Discharge with analgesics\nB) Lumbar puncture\nC) MRI brain with contrast\nD) Start empiric antibiotics immediately", "a": "B",
         "knowledge": "In suspected subarachnoid hemorrhage (SAH), CT is ~95% sensitive within 6 hours but sensitivity drops with time. If CT is negative, lumbar puncture is mandatory to rule out SAH — looking for xanthochromia and elevated RBC count. A negative CT does NOT rule out SAH."},
        {"q": "Which anti-hypertensive class is CONTRAINDICATED in bilateral renal artery stenosis?\nA) Calcium channel blockers\nB) ACE inhibitors\nC) Beta-blockers\nD) Thiazide diuretics", "a": "B",
         "knowledge": "ACE inhibitors (and ARBs) are contraindicated in bilateral renal artery stenosis because they reduce efferent arteriolar tone, which is the kidney's compensatory mechanism to maintain GFR. This can precipitate acute renal failure. Unilateral stenosis with normal contralateral kidney is relatively safe."},
        {"q": "A 45-year-old woman presents with fatigue, weight gain, constipation, and cold intolerance. TSH is elevated, free T4 is low. What is the most likely diagnosis?\nA) Graves' disease\nB) Hashimoto's thyroiditis\nC) Thyroid storm\nD) De Quervain's thyroiditis", "a": "B",
         "knowledge": "Elevated TSH + low free T4 = primary hypothyroidism. In a middle-aged woman, the most common cause is Hashimoto's thyroiditis (chronic autoimmune thyroiditis). Anti-TPO antibodies confirm the diagnosis. Graves' disease causes hyperthyroidism (low TSH, high T4)."},
        {"q": "In acute myocardial infarction, which biomarker rises earliest?\nA) Troponin I\nB) CK-MB\nC) Myoglobin\nD) LDH", "a": "C",
         "knowledge": "Myoglobin rises within 1-3 hours of MI (earliest), but is not cardiac-specific. Troponin I rises at 3-6 hours (most specific, gold standard). CK-MB rises at 4-8 hours. LDH rises at 12-24 hours. Myoglobin's early rise makes it useful for early rule-out."},
        {"q": "What is the mechanism of action of metformin in type 2 diabetes?\nA) Stimulates insulin secretion from pancreatic beta cells\nB) Activates AMPK, reducing hepatic glucose production\nC) Inhibits DPP-4 enzyme\nD) Blocks SGLT2 in the proximal tubule", "a": "B",
         "knowledge": "Metformin primarily activates AMP-activated protein kinase (AMPK) in the liver, which reduces hepatic gluconeogenesis (glucose production). It also increases insulin sensitivity in peripheral tissues. It does NOT stimulate insulin secretion (that's sulfonylureas)."},
        {"q": "A patient on warfarin has an INR of 8.5 with no active bleeding. What is the appropriate management?\nA) Administer IV vitamin K 10mg + fresh frozen plasma\nB) Hold warfarin and give oral vitamin K 2.5-5mg\nC) Continue warfarin at reduced dose\nD) Administer prothrombin complex concentrate", "a": "B",
         "knowledge": "For INR >4.5-10 without bleeding: hold warfarin and give low-dose oral vitamin K (2.5-5mg). IV vitamin K and FFP/PCC are reserved for active bleeding. Oral vitamin K reduces INR within 24-48 hours. Very high dose IV vitamin K can cause warfarin resistance."},
        {"q": "Which mutation is most commonly associated with hereditary hemochromatosis?\nA) C282Y in the HFE gene\nB) Factor V Leiden\nC) BRCA1\nD) JAK2 V617F", "a": "A",
         "knowledge": "C282Y homozygosity in the HFE gene (chromosome 6) accounts for >80% of hereditary hemochromatosis cases. It disrupts hepcidin regulation, leading to excessive intestinal iron absorption. Factor V Leiden causes thrombophilia. JAK2 V617F is associated with myeloproliferative neoplasms."},
        {"q": "The 'triple assessment' for a breast lump consists of:\nA) Mammography, CT, PET scan\nB) Clinical examination, imaging (mammography/ultrasound), tissue sampling (FNA/core biopsy)\nC) Blood tests, MRI, surgical excision\nD) Self-examination, genetic testing, sentinel node biopsy", "a": "B",
         "knowledge": "Triple assessment for breast lumps: (1) Clinical examination, (2) Imaging — mammography (>40) or ultrasound (<40), (3) Tissue sampling — FNA (fine needle aspiration) or core biopsy. All three should be concordant before a benign diagnosis is accepted."},
        {"q": "Addison's disease is characterized by which electrolyte pattern?\nA) Hypernatremia and hypokalemia\nB) Hyponatremia and hyperkalemia\nC) Hypernatremia and hyperkalemia\nD) Hyponatremia and hypokalemia", "a": "B",
         "knowledge": "Addison's disease (primary adrenal insufficiency) causes aldosterone deficiency → sodium wasting (hyponatremia) and potassium retention (hyperkalemia). Also cortisol deficiency → hypotension, hypoglycemia, hyperpigmentation (from elevated ACTH)."},
        {"q": "Which scoring system is used to assess severity in acute pancreatitis at 48 hours?\nA) APACHE II\nB) Glasgow (Imrie) score\nC) CURB-65\nD) Child-Pugh score", "a": "B",
         "knowledge": "The Glasgow (Imrie) score uses 8 parameters measured at 48 hours to assess acute pancreatitis severity: PaO2, Age, Neutrophils, Calcium, Renal (urea), Enzymes (LDH), Albumin, Sugar (glucose). ≥3 indicates severe. APACHE II can also be used. CURB-65 is for pneumonia. Child-Pugh is for liver cirrhosis."},
    ],
    "physics": [
        {"q": "In the photoelectric effect, increasing the intensity of light above the threshold frequency:\nA) Increases the kinetic energy of emitted electrons\nB) Increases the number of emitted electrons\nC) Decreases the work function\nD) Changes the threshold frequency", "a": "B",
         "knowledge": "Increasing light intensity increases the number of photons, hence more electrons are emitted (more current). Kinetic energy of each electron depends on frequency, not intensity (KE = hf - φ). Work function and threshold frequency are material properties, unchanged by intensity."},
        {"q": "A particle in a one-dimensional infinite potential well of width L has energy levels given by:\nA) E_n = n²h²/(8mL²)\nB) E_n = nhf\nC) E_n = -13.6/n² eV\nD) E_n = (n+1/2)ℏω", "a": "A",
         "knowledge": "For a particle in a 1D infinite square well of width L: E_n = n²π²ℏ²/(2mL²) = n²h²/(8mL²), where n = 1, 2, 3... The energy is quantized and proportional to n². E_n = -13.6/n² eV is for hydrogen atom. E_n = (n+1/2)ℏω is the quantum harmonic oscillator."},
        {"q": "Maxwell's equations predict that the speed of electromagnetic waves in vacuum is:\nA) c = 1/√(μ₀ε₀)\nB) c = μ₀ε₀\nC) c = √(μ₀/ε₀)\nD) c = ε₀/μ₀", "a": "A",
         "knowledge": "From Maxwell's equations, the wave equation gives c = 1/√(μ₀ε₀), where μ₀ is the permeability of free space and ε₀ is the permittivity. This was one of the great unifications of physics — showing light is an electromagnetic wave."},
        {"q": "In special relativity, the Lorentz factor γ equals 2 when v equals:\nA) c/2\nB) c√3/2\nC) c/√2\nD) 3c/4", "a": "B",
         "knowledge": "γ = 1/√(1 - v²/c²). Setting γ = 2: 4 = 1/(1 - v²/c²), so 1 - v²/c² = 1/4, v²/c² = 3/4, v = c√3/2 ≈ 0.866c. At v = c/2, γ ≈ 1.155. At v = c/√2, γ = √2 ≈ 1.414."},
        {"q": "The Carnot efficiency of a heat engine operating between temperatures T_H and T_C is:\nA) η = 1 - T_H/T_C\nB) η = 1 - T_C/T_H\nC) η = T_C/T_H\nD) η = (T_H - T_C)/(T_H + T_C)", "a": "B",
         "knowledge": "Carnot efficiency η = 1 - T_C/T_H (temperatures in Kelvin). This is the maximum possible efficiency for any heat engine between those temperatures. No real engine can exceed Carnot efficiency (2nd law of thermodynamics)."},
        {"q": "Heisenberg's uncertainty principle states that ΔxΔp ≥:\nA) h\nB) ℏ/2\nC) ℏ\nD) h/(4π)", "a": "B",
         "knowledge": "The Heisenberg uncertainty principle: ΔxΔp ≥ ℏ/2 (where ℏ = h/2π). Note that ℏ/2 = h/(4π), so both B and D are equivalent. The standard form uses ℏ/2. This is a fundamental limit, not a measurement limitation."},
        {"q": "In quantum mechanics, the expectation value of an observable A for a state |ψ⟩ is:\nA) ⟨ψ|A|ψ⟩\nB) |⟨ψ|A|ψ⟩|²\nC) ⟨A|ψ|A⟩\nD) Tr(A|ψ⟩⟨ψ|) only", "a": "A",
         "knowledge": "The expectation value of observable A in state |ψ⟩ is ⟨A⟩ = ⟨ψ|A|ψ⟩ (for pure states). This equals Tr(Aρ) where ρ = |ψ⟩⟨ψ| is the density matrix. |⟨ψ|A|ψ⟩|² gives a probability, not an expectation value."},
        {"q": "A uniform electric field E exists between two parallel plates separated by distance d. The potential difference between the plates is:\nA) V = Ed\nB) V = E/d\nC) V = Ed²\nD) V = E²d", "a": "A",
         "knowledge": "For a uniform electric field between parallel plates: V = Ed (voltage = field × distance). The electric field is E = V/d, uniform between the plates. This is the basis of parallel plate capacitors where C = ε₀A/d."},
        {"q": "Which of these is NOT a consequence of time dilation in special relativity?\nA) Moving clocks run slower\nB) Muons created in the upper atmosphere reach the Earth's surface\nC) The speed of light varies with the observer's velocity\nD) GPS satellites require relativistic corrections", "a": "C",
         "knowledge": "A fundamental postulate of special relativity is that the speed of light is constant (c) for all inertial observers — it does NOT vary with observer velocity. Time dilation explains muon reach, GPS corrections, and the twin paradox."},
        {"q": "The de Broglie wavelength of a particle with momentum p is:\nA) λ = hp\nB) λ = h/p\nC) λ = p/h\nD) λ = h²/p", "a": "B",
         "knowledge": "de Broglie's hypothesis: every particle has a wave-like nature with wavelength λ = h/p, where h is Planck's constant and p is momentum. For an electron with velocity v: λ = h/(mv). This wave-particle duality is fundamental to quantum mechanics."},
    ],
    "math": [
        {"q": "What is the derivative of f(x) = x^x for x > 0?\nA) x^x · ln(x)\nB) x · x^(x-1)\nC) x^x(ln(x) + 1)\nD) x^x · x", "a": "C",
         "knowledge": "For f(x) = x^x, use logarithmic differentiation: ln(f) = x·ln(x), so f'/f = ln(x) + 1, giving f'(x) = x^x(ln(x) + 1). The key insight is that both base and exponent depend on x, so neither the power rule nor exponential rule alone applies."},
        {"q": "The eigenvalues of the matrix [[0, 1], [1, 0]] are:\nA) 0 and 1\nB) 1 and -1\nC) 0 and 2\nD) i and -i", "a": "B",
         "knowledge": "For [[0,1],[1,0]], det(A - λI) = λ² - 1 = 0, giving λ = ±1. This is a reflection matrix (also a Pauli-X matrix in quantum computing). Its eigenvectors are [1,1] (λ=1) and [1,-1] (λ=-1)."},
        {"q": "∫₀^∞ e^(-x²) dx equals:\nA) 1\nB) √π\nC) √π/2\nD) π", "a": "C",
         "knowledge": "The Gaussian integral ∫₋∞^∞ e^(-x²) dx = √π. By symmetry, ∫₀^∞ e^(-x²) dx = √π/2. This is proven using polar coordinates (squaring the integral and converting to r,θ). It's fundamental to probability theory and statistical mechanics."},
        {"q": "If P(A) = 0.3, P(B) = 0.4, and A and B are independent, what is P(A ∪ B)?\nA) 0.70\nB) 0.58\nC) 0.12\nD) 0.42", "a": "B",
         "knowledge": "For independent events: P(A ∩ B) = P(A)·P(B) = 0.3 × 0.4 = 0.12. By inclusion-exclusion: P(A ∪ B) = P(A) + P(B) - P(A ∩ B) = 0.3 + 0.4 - 0.12 = 0.58."},
        {"q": "The Taylor series expansion of e^x around x = 0 is:\nA) Σ x^n/n! for n = 0 to ∞\nB) Σ x^n/(n+1)! for n = 0 to ∞\nC) Σ (-1)^n · x^n/n! for n = 0 to ∞\nD) Σ x^(2n)/(2n)! for n = 0 to ∞", "a": "A",
         "knowledge": "e^x = Σ(n=0 to ∞) x^n/n! = 1 + x + x²/2! + x³/3! + ... This converges for all real x. Option C is e^(-x). Option D is cosh(x). The radius of convergence is infinite."},
        {"q": "The rank of a 3×3 matrix with two identical rows is at most:\nA) 3\nB) 2\nC) 1\nD) 0", "a": "B",
         "knowledge": "If two rows are identical, they are linearly dependent, so the row space has dimension at most 2. Therefore rank ≤ 2. If the third row is independent of the first, rank = 2. If all three rows are identical, rank = 1."},
        {"q": "The divergence of the curl of any vector field F is:\nA) |F|\nB) ∇²F\nC) 0\nD) F × ∇", "a": "C",
         "knowledge": "∇ · (∇ × F) = 0 always (divergence of curl is zero). This is a vector calculus identity following from the symmetry of mixed partial derivatives. Similarly, ∇ × (∇f) = 0 (curl of gradient is zero)."},
        {"q": "What is lim(x→0) sin(x)/x?\nA) 0\nB) 1\nC) ∞\nD) Does not exist", "a": "B",
         "knowledge": "lim(x→0) sin(x)/x = 1. This fundamental limit can be proven geometrically (squeeze theorem with unit circle areas) or by L'Hôpital's rule: lim cos(x)/1 = 1. It's the basis for the derivative of sin(x)."},
        {"q": "The number of spanning trees of the complete graph K₄ is:\nA) 4\nB) 8\nC) 16\nD) 12", "a": "C",
         "knowledge": "By Cayley's formula, the complete graph Kₙ has n^(n-2) spanning trees. For K₄: 4^(4-2) = 4² = 16. For K₃: 3^1 = 3. This can also be computed via the matrix tree theorem (determinant of any cofactor of the Laplacian)."},
        {"q": "If f(x) = ln(ln(x)), what is f'(x)?\nA) 1/(x·ln(x))\nB) 1/ln(x)\nC) ln(x)/x\nD) 1/(x²·ln(x))", "a": "A",
         "knowledge": "By the chain rule: f'(x) = (1/ln(x)) · (1/x) = 1/(x·ln(x)). The outer derivative of ln(u) is 1/u, then multiply by the derivative of the inner function ln(x) which is 1/x."},
    ],
}

# ── Main ──────────────────────────────────────────────────────────────

def run_track_a(tok, model):
    print("\n" + "=" * 70)
    print("TRACK A: Novel Reasoning with Memory Palace")
    print("=" * 70)
    results = {"track": "A", "model": MODEL_ID, "timestamp": datetime.now().isoformat(), "scenarios": []}

    for sc in REASONING_SCENARIOS:
        print(f"\n--- {sc['title']} ---")
        cache, prefix_len = build_cache(sc["knowledge"], tok, model)

        # Baseline
        resp_b, lat_b = query_baseline(sc["question"], tok, model, max_new=400)
        sc_b, found_b = kw_score(resp_b, sc["scoring_keywords"])
        pass_b = sc_b >= sc["min_keywords"]
        print(f"  Baseline: {sc_b}/{len(sc['scoring_keywords'])} keywords ({lat_b:.1f}s) {'PASS' if pass_b else 'FAIL'}")

        # Palace
        resp_p, lat_p = query_palace(sc["question"], cache, prefix_len, tok, model, max_new=400)
        sc_p, found_p = kw_score(resp_p, sc["scoring_keywords"])
        pass_p = sc_p >= sc["min_keywords"]
        print(f"  Palace:   {sc_p}/{len(sc['scoring_keywords'])} keywords ({lat_p:.1f}s) {'PASS' if pass_p else 'FAIL'}")

        # RAG
        resp_r, lat_r = query_rag(sc["question"], sc["knowledge"], tok, model, max_new=400)
        sc_r, found_r = kw_score(resp_r, sc["scoring_keywords"])
        pass_r = sc_r >= sc["min_keywords"]
        print(f"  RAG:      {sc_r}/{len(sc['scoring_keywords'])} keywords ({lat_r:.1f}s) {'PASS' if pass_r else 'FAIL'}")

        results["scenarios"].append({
            "id": sc["id"], "title": sc["title"], "question": sc["question"],
            "keywords": sc["scoring_keywords"], "min_keywords": sc["min_keywords"],
            "baseline": {"response": resp_b, "score": sc_b, "found": found_b, "pass": pass_b, "latency": lat_b},
            "palace":   {"response": resp_p, "score": sc_p, "found": found_p, "pass": pass_p, "latency": lat_p},
            "rag":      {"response": resp_r, "score": sc_r, "found": found_r, "pass": pass_r, "latency": lat_r},
        })
        del cache
        gc.collect()
        torch.cuda.empty_cache()

    # Summary
    b_pass = sum(1 for s in results["scenarios"] if s["baseline"]["pass"])
    p_pass = sum(1 for s in results["scenarios"] if s["palace"]["pass"])
    r_pass = sum(1 for s in results["scenarios"] if s["rag"]["pass"])
    results["summary"] = {"baseline": b_pass, "palace": p_pass, "rag": r_pass, "total": len(REASONING_SCENARIOS)}
    print(f"\nSummary: Baseline {b_pass}/5  Palace {p_pass}/5  RAG {r_pass}/5")

    out = RESULTS_DIR / "track_a_novel_reasoning.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved: {out}")
    return results


def run_track_b(tok, model):
    print("\n" + "=" * 70)
    print("TRACK B: Industry Standard Benchmarks")
    print("=" * 70)
    results = {"track": "B", "model": MODEL_ID, "timestamp": datetime.now().isoformat(), "subjects": {}}

    for subj, questions in BENCHMARK_QUESTIONS.items():
        print(f"\n--- {subj.upper()} ({len(questions)} questions) ---")
        base_score = think_score = palace_score = 0
        details = []

        for i, q in enumerate(questions):
            print(f"  Q{i+1}: {q['q'][:55]}... [correct={q['a']}]")

            # Condition 1: Baseline (no think)
            resp1, lat1 = query_baseline(q["q"], tok, model, max_new=50, thinking=False)
            ans1 = extract_mcq(resp1)
            p1 = ans1 == q["a"]
            base_score += int(p1)

            # Condition 2: Baseline + thinking
            resp2, lat2 = query_baseline(q["q"], tok, model, max_new=2048, thinking=True)
            ans2 = extract_mcq(resp2)
            p2 = ans2 == q["a"]
            think_score += int(p2)

            # Condition 3: Palace + thinking (build cache from knowledge)
            cache, pl = build_cache(q["knowledge"], tok, model)
            resp3, lat3 = query_palace(q["q"], cache, pl, tok, model, max_new=2048, thinking=True)
            ans3 = extract_mcq(resp3)
            p3 = ans3 == q["a"]
            palace_score += int(p3)
            del cache; gc.collect(); torch.cuda.empty_cache()

            sym = lambda p: "✓" if p else "✗"
            print(f"    Base: {ans1} {sym(p1)} ({lat1:.1f}s)  Think: {ans2} {sym(p2)} ({lat2:.1f}s)  Palace+Think: {ans3} {sym(p3)} ({lat3:.1f}s)")

            details.append({
                "question": q["q"][:100], "correct": q["a"],
                "baseline": {"answer": ans1, "correct": p1, "latency": lat1, "response": resp1[:200]},
                "thinking": {"answer": ans2, "correct": p2, "latency": lat2, "response": resp2[:300]},
                "palace_thinking": {"answer": ans3, "correct": p3, "latency": lat3, "response": resp3[:300]},
            })

        n = len(questions)
        results["subjects"][subj] = {
            "baseline": base_score, "thinking": think_score, "palace_thinking": palace_score, "total": n,
            "baseline_pct": base_score / n * 100, "thinking_pct": think_score / n * 100,
            "palace_thinking_pct": palace_score / n * 100, "details": details,
        }
        print(f"  Scores: Base {base_score}/{n} ({base_score/n*100:.0f}%)  Think {think_score}/{n} ({think_score/n*100:.0f}%)  Palace+Think {palace_score}/{n} ({palace_score/n*100:.0f}%)")

    # Aggregate
    tb = sum(v["baseline"] for v in results["subjects"].values())
    tt = sum(v["thinking"] for v in results["subjects"].values())
    tp = sum(v["palace_thinking"] for v in results["subjects"].values())
    tn = sum(v["total"] for v in results["subjects"].values())
    results["aggregate"] = {
        "baseline": tb, "thinking": tt, "palace_thinking": tp, "total": tn,
        "baseline_pct": tb / tn * 100, "thinking_pct": tt / tn * 100, "palace_thinking_pct": tp / tn * 100,
    }
    print(f"\nOVERALL: Base {tb}/{tn} ({tb/tn*100:.0f}%)  Think {tt}/{tn} ({tt/tn*100:.0f}%)  Palace+Think {tp}/{tn} ({tp/tn*100:.0f}%)")

    out = RESULTS_DIR / "track_b_industry_benchmarks.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved: {out}")
    return results


if __name__ == "__main__":
    print("=" * 70)
    print(f"Memory Palace Batch 2 — Novel Reasoning + Industry Benchmarks")
    print(f"Model: {MODEL_ID}")
    print(f"Results: {RESULTS_DIR}")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 70)

    print("Loading model...")
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.float16).to("cuda")
    model.eval()
    print(f"Model loaded. VRAM: {torch.cuda.memory_allocated()/1e9:.1f}GB")

    # Warmup
    _ = query_baseline("Hello", tok, model, max_new=5)
    print("Warmup done.\n")

    track_a = run_track_a(tok, model)
    track_b = run_track_b(tok, model)

    # Combined summary
    combined = RESULTS_DIR / "batch2_combined.json"
    with open(combined, "w") as f:
        json.dump({"track_a": track_a, "track_b": track_b, "completed": datetime.now().isoformat()}, f, indent=2)
    print(f"\nAll done! Combined: {combined}")
    print(f"Finished: {datetime.now().isoformat()}")
