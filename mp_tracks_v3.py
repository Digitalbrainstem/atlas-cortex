#!/usr/bin/env python3
"""Memory Palace v3 — Harder Validation Tracks

Improvements over v2:
- Track 1v3: Graded difficulty (easy→very hard) with multi-hop questions
- Track 2v3: MMLU with thinking mode enabled for Qwen3-4B
- Track 4v3: Realistic categorization where wrong paper gives WRONG answers
"""
from __future__ import annotations

import os, json, time, gc, re, sys
from pathlib import Path
from datetime import datetime

os.environ["HSA_OVERRIDE_GFX_VERSION"] = "11.0.0"

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, DynamicCache

RESULTS_DIR = Path("/mnt/fastpool/atlas-distillation/results/memory_palace/tracks_v3")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

MODEL_ID = "Qwen/Qwen3-4B"

# ---------------------------------------------------------------------------
# Synthetic Papers for Track 1v3
# ---------------------------------------------------------------------------

PAPER_A = """Title: NeoCardiol (NCD-4417) in Chronic Heart Failure: A Phase III Randomized Controlled Trial
Authors: Martinez JL, Chen W, Okonkwo DA, Petrov IM, Larsson AK
Journal: New England Journal of Cardiology, 2024;391(8):712-723
DOI: 10.1056/NEJCoa2401187

ABSTRACT
Background: Heart failure with reduced ejection fraction (HFrEF) remains a leading cause of morbidity despite current guideline-directed medical therapy. NeoCardiol (NCD-4417) is a novel dual-acting myosin activator and mitochondrial membrane stabilizer that enhances cardiac contractility without increasing oxygen demand.

Methods: We conducted a multicenter, double-blind, placebo-controlled trial enrolling 1,243 patients with NYHA class II-IV HFrEF (LVEF ≤35%) across 87 sites in 14 countries. Patients were randomized 1:1 to NeoCardiol 200mg twice daily or placebo for 52 weeks. The primary endpoint was the composite of cardiovascular death or first hospitalization for heart failure.

Results: NeoCardiol demonstrated an overall efficacy rate of 73% on the primary composite endpoint (HR 0.27, 95% CI 0.21-0.35, p<0.001). Mean LVEF improved by 8.4 percentage points (vs 1.2 in placebo, p<0.001). Six-minute walk distance increased by 47 meters (vs 8 meters, p<0.001). NT-proBNP levels decreased by 62% from baseline.

Safety: Adverse events were reported in 34% of NeoCardiol patients vs 28% placebo. The most common were nausea (12%), dizziness (5%), and hepatotoxicity (2%, defined as ALT >3× ULN). Hepatotoxicity led to treatment discontinuation in 1.3% of patients. All hepatotoxicity cases resolved within 8 weeks of discontinuation. Liver function monitoring every 4 weeks is recommended for the first 6 months. No cases of torsades de pointes or significant QT prolongation were observed.

Conclusions: NeoCardiol significantly reduced cardiovascular death and heart failure hospitalization in patients with HFrEF. The hepatotoxicity signal requires careful monitoring but appears manageable and reversible. NeoCardiol represents a new therapeutic class for heart failure management."""

PAPER_B = """Title: NeuroSyncX Deep Brain Stimulation Implant for Treatment-Resistant Parkinson's Disease: 3-Year Follow-Up of the SYNC-PD Trial
Authors: Nakamura T, Williams SR, Bergström F, Al-Rashidi N, Costa MV
Journal: The Lancet Neurology, 2024;23(5):398-411
DOI: 10.1016/S1474-4422(24)00089-3

ABSTRACT
Background: Deep brain stimulation (DBS) is effective for motor symptoms of Parkinson's disease, but conventional systems have limited battery life and programming flexibility. NeuroSyncX is a closed-loop adaptive DBS implant with real-time beta-oscillation sensing, machine learning-driven stimulation adjustment, and a novel lithium-ceramic battery.

Methods: The SYNC-PD trial enrolled 312 patients with treatment-resistant Parkinson's disease (Hoehn & Yahr stage 3-4, inadequate response to ≥3 dopaminergic agents) at 23 centers. All patients received bilateral subthalamic nucleus (STN) implantation. Primary endpoint: change in UPDRS-III motor score at 36 months. Secondary endpoints included battery longevity, adverse events, and quality of life (PDQ-39).

Results: NeuroSyncX achieved a mean tremor reduction of 68% at 36 months (UPDRS-III tremor subscore improved from 14.2 to 4.5, p<0.001). Overall UPDRS-III improvement was 52% (total score: 47.8 to 22.9). Rigidity improved by 44% and bradykinesia by 39%. The adaptive algorithm reduced stimulation energy consumption by 41% compared to conventional open-loop DBS. Projected battery life is 4.2 years (95% CI 3.8-4.6), verified by telemetric monitoring at 36 months. PDQ-39 quality of life improved by 37 points.

Safety: The device is MRI-conditional (1.5T only, specific absorption rate ≤0.1 W/kg, head coil only). Surgical complications included lead migration (3.2%), infection (2.6%), and transient dysarthria (7.1%). No intracranial hemorrhage events occurred. Stimulation-related side effects: paresthesia (11%), muscle contractions (4%). Hardware malfunction requiring revision occurred in 1.9% of patients.

Conclusions: NeuroSyncX provides sustained motor improvement with adaptive stimulation in treatment-resistant Parkinson's disease. The 4.2-year battery life and closed-loop design represent meaningful advances over current DBS technology."""

PAPER_C = """Title: FloraRestore (FR-8821) Microbiome Transplant Capsule for Recurrent Clostridioides difficile Infection: The RESTORE-CDI Phase III Trial
Authors: Johansson E, Patel RK, Dubois ML, Kim SH, Okafor CN
Journal: Journal of the American Medical Association, 2024;331(14):1198-1210
DOI: 10.1001/jama.2024.2847

ABSTRACT
Background: Recurrent C. difficile infection (rCDI) affects approximately 150,000 patients annually in the United States, with mortality rates of 5-10% in severe cases. FloraRestore (FR-8821) is a standardized, oral microbiome transplant capsule containing a consortium of 47 bacterial strains derived from rigorously screened healthy donors, manufactured under GMP conditions with cryoprotectant stabilization.

Methods: This multicenter, double-blind, placebo-controlled trial enrolled 847 patients with confirmed rCDI (≥2 recurrences within 6 months) at 64 sites across North America and Europe. Patients were randomized 1:1 to FloraRestore (4 capsules daily for 3 days following standard vancomycin taper) or identical placebo capsules. Primary endpoint: recurrence of CDI within 8 weeks. Key secondary endpoints: recurrence at 6 months, gut microbiome diversity (Shannon index), and safety.

Results: CDI recurrence at 8 weeks was 8% with FloraRestore versus 31% with placebo (absolute risk reduction 23%, 95% CI 17-29%, p<0.001, NNT=4.3). At 6 months, recurrence remained lower: 14% vs 42% (p<0.001). Shannon diversity index increased from 1.8 to 4.1 in the FloraRestore group (vs 1.8 to 2.3 in placebo, p<0.001) by week 4, sustained at 6 months. Donor strain engraftment was confirmed in 89% of responders via shotgun metagenomic sequencing. Fecal calprotectin decreased by 71% (vs 23% placebo).

Safety: FloraRestore was well-tolerated. Adverse events: abdominal bloating (18%), mild diarrhea (9%), flatulence (14%). No cases of bacteremia, serious allergic reactions, or transmission of donor-derived infections were observed. One patient (<0.5%) developed transient fever requiring overnight observation.

Conclusions: FloraRestore significantly reduced CDI recurrence with an excellent safety profile. The standardized oral capsule format eliminates the logistical challenges of traditional fecal microbiota transplantation while maintaining comparable efficacy."""

# ---------------------------------------------------------------------------
# Track 1v3 Questions — Graded Difficulty
# ---------------------------------------------------------------------------

TRACK1_QUESTIONS = {
    "easy": [
        {
            "question": "What was NeoCardiol's overall efficacy rate on the primary composite endpoint?",
            "expected_keywords": ["73"],
            "paper_needed": "A",
        },
        {
            "question": "How many patients were enrolled in the RESTORE-CDI trial for FloraRestore?",
            "expected_keywords": ["847"],
            "paper_needed": "C",
        },
        {
            "question": "What is the projected battery life of the NeuroSyncX implant?",
            "expected_keywords": ["4.2"],
            "paper_needed": "B",
        },
        {
            "question": "What was the CDI recurrence rate at 8 weeks in the FloraRestore group?",
            "expected_keywords": ["8%", "8 percent"],
            "paper_needed": "C",
        },
        {
            "question": "What percentage of NeoCardiol patients experienced nausea as an adverse event?",
            "expected_keywords": ["12"],
            "paper_needed": "A",
        },
    ],
    "medium": [
        {
            "question": "Summarize the key safety concerns for NeoCardiol based on the trial results.",
            "expected_keywords": ["hepatotoxicity", "nausea", "dizziness", "liver", "ALT"],
            "min_keywords": 2,
            "paper_needed": "A",
        },
        {
            "question": "Explain how NeuroSyncX differs from conventional DBS systems in terms of stimulation approach.",
            "expected_keywords": ["closed-loop", "adaptive", "beta-oscillation", "machine learning", "real-time"],
            "min_keywords": 2,
            "paper_needed": "B",
        },
        {
            "question": "What evidence supports FloraRestore's mechanism of action in restoring gut health?",
            "expected_keywords": ["shannon", "diversity", "engraftment", "microbiome", "strain", "calprotectin"],
            "min_keywords": 2,
            "paper_needed": "C",
        },
        {
            "question": "Describe the MRI compatibility limitations of the NeuroSyncX implant.",
            "expected_keywords": ["1.5T", "conditional", "SAR", "head coil", "0.1"],
            "min_keywords": 2,
            "paper_needed": "B",
        },
        {
            "question": "What is the clinical significance of NeoCardiol's effect on NT-proBNP levels?",
            "expected_keywords": ["62%", "decrease", "biomarker", "heart failure"],
            "min_keywords": 2,
            "paper_needed": "A",
        },
    ],
    "hard": [
        {
            "question": "Based on the hepatotoxicity rate and monitoring requirements, would NeoCardiol be appropriate as a first-line treatment for elderly patients (>75 years) with pre-existing liver disease? Explain your reasoning.",
            "expected_keywords": ["hepatotoxicity", "liver", "monitor", "caution", "risk", "ALT", "not appropriate", "contraindicated", "careful"],
            "min_keywords": 3,
            "paper_needed": "A",
        },
        {
            "question": "Given NeuroSyncX's 4.2-year battery life and the surgical risks of revision, calculate approximately how many revision surgeries a 55-year-old patient might need over 25 years and discuss the cumulative risk implications.",
            "expected_keywords": ["5", "6", "revision", "risk", "cumulative", "infection", "migration"],
            "min_keywords": 3,
            "paper_needed": "B",
        },
        {
            "question": "FloraRestore showed 89% donor strain engraftment in responders. What does this suggest about the 11% of responders without detectable engraftment — could they have benefited from a different mechanism?",
            "expected_keywords": ["engraftment", "mechanism", "immune", "metabolite", "colonization", "indirect", "resistance"],
            "min_keywords": 2,
            "paper_needed": "C",
        },
        {
            "question": "The NeoCardiol trial excluded patients with LVEF >35%. Based on the mechanism of action (myosin activator + mitochondrial stabilizer), would you expect similar efficacy in HFpEF patients? Why or why not?",
            "expected_keywords": ["HFpEF", "preserved", "ejection fraction", "myosin", "contractility", "different", "mechanism"],
            "min_keywords": 2,
            "paper_needed": "A",
        },
        {
            "question": "NeuroSyncX reduced stimulation energy by 41% via adaptive algorithms. What are the potential neuroplasticity implications of intermittent versus continuous stimulation?",
            "expected_keywords": ["neuroplasticity", "adaptive", "intermittent", "stimulation", "brain", "tolerance", "habituation"],
            "min_keywords": 2,
            "paper_needed": "B",
        },
    ],
    "very_hard": [
        {
            "question": "Across all three studies (NeoCardiol, NeuroSyncX, FloraRestore), which treatment demonstrated the most favorable benefit-to-risk ratio and why? Consider both efficacy endpoints and adverse event profiles.",
            "expected_keywords": ["FloraRestore", "NeoCardiol", "NeuroSyncX", "adverse", "efficacy", "risk", "benefit"],
            "min_keywords": 4,
            "paper_needed": "ALL",
        },
        {
            "question": "If a patient has both chronic heart failure (NYHA III) and recurrent C. difficile infection, and their hepatologist has flagged elevated liver enzymes (ALT 2× ULN), which treatment should be initiated first and why? Consider drug interactions and monitoring burden.",
            "expected_keywords": ["FloraRestore", "liver", "NeoCardiol", "hepatotoxicity", "ALT", "C. difficile", "first"],
            "min_keywords": 4,
            "paper_needed": "ALL",
        },
        {
            "question": "Compare the number-needed-to-treat (NNT) for FloraRestore with the absolute risk reduction for NeoCardiol's primary endpoint. Which intervention provides greater absolute benefit per patient treated?",
            "expected_keywords": ["NNT", "4.3", "73", "absolute", "risk reduction", "FloraRestore", "NeoCardiol"],
            "min_keywords": 3,
            "paper_needed": "ALL",
        },
        {
            "question": "A 60-year-old patient has Parkinson's disease (Hoehn & Yahr stage 3), heart failure (LVEF 30%), and a history of 3 CDI episodes. Design a treatment prioritization plan using findings from all three trials, considering contraindications and monitoring requirements.",
            "expected_keywords": ["NeoCardiol", "NeuroSyncX", "FloraRestore", "priorit", "monitor", "liver", "CDI", "Parkinson"],
            "min_keywords": 4,
            "paper_needed": "ALL",
        },
        {
            "question": "All three trials were randomized controlled trials. Compare their methodological strengths: which trial had the strongest design for detecting its primary endpoint, considering sample size, blinding feasibility, and endpoint objectivity?",
            "expected_keywords": ["sample size", "blinding", "double-blind", "endpoint", "objective", "subjective", "1243", "312", "847"],
            "min_keywords": 3,
            "paper_needed": "ALL",
        },
    ],
}

# ---------------------------------------------------------------------------
# Track 2v3 — MMLU-style Questions with Thinking Mode
# ---------------------------------------------------------------------------

TRACK2_QUESTIONS = {
    "biology": [
        {"question": "Which organelle is responsible for producing ATP through oxidative phosphorylation?\nA) Ribosome\nB) Golgi apparatus\nC) Mitochondrion\nD) Endoplasmic reticulum", "answer": "C"},
        {"question": "What is the role of mRNA in protein synthesis?\nA) It forms the structural component of ribosomes\nB) It carries amino acids to the ribosome\nC) It carries genetic instructions from DNA to the ribosome\nD) It catalyzes peptide bond formation", "answer": "C"},
        {"question": "During meiosis, crossing over occurs in which phase?\nA) Metaphase I\nB) Prophase I\nC) Anaphase II\nD) Telophase I", "answer": "B"},
        {"question": "Which blood type is considered the universal donor for red blood cells?\nA) AB positive\nB) A negative\nC) B positive\nD) O negative", "answer": "D"},
        {"question": "The Hardy-Weinberg equilibrium requires all of the following EXCEPT:\nA) No mutation\nB) Random mating\nC) Natural selection\nD) Large population size", "answer": "C"},
        {"question": "Which enzyme unwinds the DNA double helix during replication?\nA) DNA polymerase\nB) Helicase\nC) Ligase\nD) Topoisomerase", "answer": "B"},
        {"question": "Photosystem II splits water molecules. What are the products?\nA) CO2, H2, and electrons\nB) O2, H+ ions, and electrons\nC) Glucose and O2\nD) NADPH and ATP", "answer": "B"},
        {"question": "Which of the following is a function of the lymphatic system?\nA) Producing red blood cells\nB) Regulating blood sugar\nC) Returning interstitial fluid to the bloodstream\nD) Secreting digestive enzymes", "answer": "C"},
        {"question": "In a food web, organisms that break down dead organic matter are called:\nA) Primary producers\nB) Herbivores\nC) Decomposers\nD) Apex predators", "answer": "C"},
        {"question": "The process by which a cell engulfs a large particle by wrapping pseudopods around it is called:\nA) Pinocytosis\nB) Exocytosis\nC) Phagocytosis\nD) Osmosis", "answer": "C"},
    ],
    "chemistry": [
        {"question": "What is the oxidation state of sulfur in H2SO4?\nA) +2\nB) +4\nC) +6\nD) +8", "answer": "C"},
        {"question": "Which type of bond involves the sharing of electron pairs between atoms?\nA) Ionic bond\nB) Metallic bond\nC) Covalent bond\nD) Hydrogen bond", "answer": "C"},
        {"question": "According to Le Chatelier's principle, increasing pressure on a gaseous equilibrium will shift the reaction toward:\nA) The side with more moles of gas\nB) The side with fewer moles of gas\nC) Always toward the products\nD) No effect on equilibrium", "answer": "B"},
        {"question": "What is the pH of a 0.001 M HCl solution?\nA) 1\nB) 2\nC) 3\nD) 4", "answer": "C"},
        {"question": "Which of the following is an example of a Lewis acid?\nA) NH3\nB) BF3\nC) OH-\nD) H2O", "answer": "B"},
        {"question": "The ideal gas law is expressed as PV = nRT. What does 'n' represent?\nA) Number of atoms\nB) Number of moles\nC) Avogadro's number\nD) Number of electrons", "answer": "B"},
        {"question": "Which element has the highest electronegativity?\nA) Oxygen\nB) Chlorine\nC) Fluorine\nD) Nitrogen", "answer": "C"},
        {"question": "A catalyst increases the rate of a reaction by:\nA) Increasing the temperature\nB) Lowering the activation energy\nC) Increasing the concentration of reactants\nD) Changing the equilibrium constant", "answer": "B"},
        {"question": "Which quantum number describes the shape of an orbital?\nA) Principal (n)\nB) Angular momentum (l)\nC) Magnetic (ml)\nD) Spin (ms)", "answer": "B"},
        {"question": "What is the molarity of a solution made by dissolving 58.5 g of NaCl (MW = 58.5 g/mol) in enough water to make 2 liters?\nA) 0.25 M\nB) 0.5 M\nC) 1.0 M\nD) 2.0 M", "answer": "B"},
    ],
    "physics": [
        {"question": "An object in free fall near Earth's surface has an acceleration of approximately:\nA) 3.2 m/s²\nB) 9.8 m/s²\nC) 15.4 m/s²\nD) 32 m/s²", "answer": "B"},
        {"question": "According to Newton's third law, if object A exerts a force on object B, then:\nA) Object B exerts an equal force on A in the same direction\nB) Object B exerts an equal force on A in the opposite direction\nC) Object B exerts a greater force on A\nD) Object B does not exert a force on A", "answer": "B"},
        {"question": "The SI unit of electric charge is:\nA) Volt\nB) Ampere\nC) Coulomb\nD) Ohm", "answer": "C"},
        {"question": "A convex lens can produce all of the following EXCEPT:\nA) A real, inverted image\nB) A virtual, upright image\nC) A magnified image\nD) Only virtual, inverted images", "answer": "D"},
        {"question": "The total mechanical energy of a system is conserved when:\nA) Friction is present\nB) Only conservative forces do work\nC) The system is open\nD) External forces are applied", "answer": "B"},
        {"question": "What is the wavelength of a photon with energy E, given E = hc/λ?\nA) λ = E/(hc)\nB) λ = hc/E\nC) λ = h/(Ec)\nD) λ = Ec/h", "answer": "B"},
        {"question": "In a parallel circuit, the total resistance is:\nA) The sum of all individual resistances\nB) Greater than the largest individual resistance\nC) Less than the smallest individual resistance\nD) Equal to the average of all resistances", "answer": "C"},
        {"question": "The Doppler effect causes the frequency of a sound to increase when:\nA) The source moves away from the observer\nB) The source moves toward the observer\nC) The medium's temperature decreases\nD) The amplitude increases", "answer": "B"},
        {"question": "Which of the following is a vector quantity?\nA) Speed\nB) Mass\nC) Temperature\nD) Displacement", "answer": "D"},
        {"question": "A transformer with 100 primary turns and 500 secondary turns receives 120V input. What is the output voltage?\nA) 24 V\nB) 240 V\nC) 600 V\nD) 60,000 V", "answer": "C"},
    ],
    "math": [
        {"question": "What is the derivative of f(x) = 3x² + 2x - 5?\nA) 6x + 2\nB) 3x + 2\nC) 6x² + 2\nD) 6x - 5", "answer": "A"},
        {"question": "The integral of 2x dx from 0 to 3 is:\nA) 3\nB) 6\nC) 9\nD) 12", "answer": "C"},
        {"question": "If log₂(x) = 5, what is x?\nA) 10\nB) 25\nC) 32\nD) 64", "answer": "C"},
        {"question": "What is the sum of the interior angles of a hexagon?\nA) 540°\nB) 720°\nC) 900°\nD) 1080°", "answer": "B"},
        {"question": "In a right triangle, if one leg is 3 and the hypotenuse is 5, what is the other leg?\nA) 2\nB) 4\nC) √34\nD) 6", "answer": "B"},
        {"question": "What is the value of the expression: 5! / 3!?\nA) 10\nB) 20\nC) 30\nD) 60", "answer": "B"},
        {"question": "The probability of getting exactly 2 heads in 3 fair coin flips is:\nA) 1/4\nB) 3/8\nC) 1/2\nD) 3/4", "answer": "B"},
        {"question": "What is the determinant of the matrix [[2, 3], [1, 4]]?\nA) 5\nB) 11\nC) -5\nD) 8", "answer": "A"},
        {"question": "The equation x² - 5x + 6 = 0 has roots:\nA) x = 1 and x = 6\nB) x = 2 and x = 3\nC) x = -2 and x = -3\nD) x = -1 and x = 6", "answer": "B"},
        {"question": "What is the limit of (sin x)/x as x approaches 0?\nA) 0\nB) 1\nC) ∞\nD) Undefined", "answer": "B"},
    ],
}

# ---------------------------------------------------------------------------
# Track 4v3 — Realistic Categorization Papers
# ---------------------------------------------------------------------------

TRACK4_PAPERS = {
    "cardio_A": {
        "category": "cardiology",
        "title": "VasoRelax (VR-2201) for Resistant Hypertension",
        "text": """Title: VasoRelax (VR-2201) for Resistant Hypertension: The RELAX-BP Trial
Authors: Thompson DR, Yamamoto K, Singh P
Journal: Circulation, 2024

A randomized trial of 562 patients with resistant hypertension (BP >140/90 on ≥3 agents). VasoRelax, a novel endothelin receptor antagonist, reduced systolic BP by 22.4 mmHg vs 4.1 mmHg placebo (p<0.001) over 24 weeks. Unique mechanism: dual ET-A/ET-B blockade with renal-protective prostaglandin release. Key adverse events: peripheral edema (15%), headache (8%), and fluid retention requiring diuretic adjustment (6%). Contraindicated in pregnancy (Category X). Notable drug interaction: reduces efficacy of cyclosporine by 40%. Subgroup analysis showed greater benefit in African American patients (28.1 mmHg reduction).""",
    },
    "cardio_B": {
        "category": "cardiology",
        "title": "RhythmGuard (RG-550) for Atrial Fibrillation",
        "text": """Title: RhythmGuard (RG-550) Antiarrhythmic for Persistent Atrial Fibrillation: GUARD-AF Trial
Authors: Morales EC, Björk L, Zhao Q
Journal: European Heart Journal, 2024

A phase III trial of 891 patients with persistent atrial fibrillation (AF duration >7 days). RhythmGuard, a selective IKur channel blocker, achieved sinus rhythm restoration in 61% vs 23% placebo at 12 months (p<0.001). Unlike amiodarone, RhythmGuard has no thyroid or pulmonary toxicity. Mean time to first recurrence: 287 days vs 94 days. Adverse events: QT prolongation >500ms in 3.1%, requiring discontinuation. GI upset (11%), visual disturbances (4%). Unique finding: simultaneous reduction in AF-related stroke risk by 34% independent of anticoagulation status. Dosing: 150mg twice daily with mandatory ECG at weeks 1, 4, and 12.""",
    },
    "neuro_A": {
        "category": "neurology",
        "title": "ClearMind (CM-7700) for Early-Onset Alzheimer's Disease",
        "text": """Title: ClearMind (CM-7700) Anti-Amyloid Antibody for Early-Onset Alzheimer's: The CLARITY Trial
Authors: Bergman HJ, Li X, Oduya F
Journal: Annals of Neurology, 2024

A trial of 724 patients with early-onset Alzheimer's (age 45-65, positive amyloid PET). ClearMind, a bispecific antibody targeting both Aβ42 protofibrils and tau phospho-epitopes, slowed cognitive decline by 35% on ADAS-Cog13 over 18 months vs placebo (p<0.001). Amyloid PET SUVr decreased by 0.42 (vs 0.03 placebo). Key risk: ARIA-E (amyloid-related imaging abnormality - edema) occurred in 28% (symptomatic in 8%). Infusion reactions in 12%. Required: APOE genotyping before initiation — APOE4 homozygotes had 3× higher ARIA-E risk (41% vs 14%). Administered IV every 2 weeks at 10mg/kg. Cost: approximately $26,000/year.""",
    },
    "neuro_B": {
        "category": "neurology",
        "title": "SpinalBridge (SB-3300) for Spinal Cord Injury",
        "text": """Title: SpinalBridge (SB-3300) Neural Scaffold for Acute Spinal Cord Injury: The BRIDGE Trial
Authors: Kowalski AJ, Tanaka R, Fernandez-Vega I
Journal: The Lancet Neurology, 2024

A trial of 198 patients with acute traumatic spinal cord injury (ASIA grade A/B, injury within 72 hours). SpinalBridge, an injectable hydrogel containing aligned nanofiber channels and BDNF-releasing microspheres, was delivered via CT-guided injection to the injury epicenter. At 12 months, 31% of SpinalBridge patients improved by ≥2 ASIA grades vs 7% control (p<0.001). Motor score (UEMS+LEMS) improved by 18.4 points vs 4.2. Complications: injection site hematoma (5%), transient fever (22%), mild allergic reaction (3%). No tumor formation detected on serial MRI over 24 months. Window of efficacy: must be administered within 96 hours of injury. Storage: -80°C, single-use, 4-hour thaw requirement.""",
    },
    "onco_A": {
        "category": "oncology",
        "title": "OncoLock (OL-9100) for Triple-Negative Breast Cancer",
        "text": """Title: OncoLock (OL-9100) CDK7 Inhibitor for Triple-Negative Breast Cancer: LOCK-TNBC Trial
Authors: Dubois C, Asante WK, Petersen ML
Journal: Journal of Clinical Oncology, 2024

A phase III trial of 634 patients with metastatic triple-negative breast cancer (TNBC) who progressed on ≥1 prior line. OncoLock, a selective CDK7 inhibitor that disrupts super-enhancer-driven transcription in basal-like tumors, showed median PFS of 7.8 months vs 3.2 months with chemotherapy (HR 0.41, p<0.001). Overall survival: 19.2 vs 12.7 months. Objective response rate: 42% vs 18%. Biomarker: SOX10-positive tumors showed 58% ORR vs 21% in SOX10-negative. Adverse events: neutropenia (44%), fatigue (31%), mucositis (19%), cardiac toxicity (QTc prolongation) in 5%. Recommended companion diagnostic: SOX10 IHC testing before initiation.""",
    },
    "onco_B": {
        "category": "oncology",
        "title": "TumorVax (TV-4500) for Non-Small Cell Lung Cancer",
        "text": """Title: TumorVax (TV-4500) Personalized Neoantigen Vaccine for NSCLC: The BREATH-FREE Trial
Authors: Rodriguez A, Chen HY, Mbeki TN
Journal: Nature Medicine, 2024

A trial of 412 patients with stage IIIB-IV non-small cell lung cancer (NSCLC) with high tumor mutational burden (TMB ≥10 mut/Mb). TumorVax is an mRNA-based personalized vaccine encoding up to 34 patient-specific neoantigens, administered with anti-PD-1. Median PFS: 11.4 months vs 6.8 months with anti-PD-1 alone (HR 0.52, p<0.001). 2-year OS: 48% vs 31%. Complete response rate: 12% vs 3%. Vaccine manufacturing turnaround: 6 weeks from biopsy. Adverse events: injection site reactions (67%), flu-like symptoms (38%), cytokine release syndrome grade ≥3 in 4%. Key limitation: not effective in TMB-low tumors (<10 mut/Mb), where ORR was only 8% vs 7% control.""",
    },
    "infect_A": {
        "category": "infectious_disease",
        "title": "FungoClear (FC-1100) for Invasive Aspergillosis",
        "text": """Title: FungoClear (FC-1100) for Invasive Pulmonary Aspergillosis: The CLEAR-LUNG Trial
Authors: Nkomo B, Andersson F, Wu J
Journal: Clinical Infectious Diseases, 2024

A trial of 387 immunocompromised patients (hematologic malignancy or solid organ transplant) with proven/probable invasive pulmonary aspergillosis. FungoClear, a novel orotomide-class antifungal targeting fungal dihydroorotate dehydrogenase (DHODH), achieved complete/partial response in 68% vs 52% with voriconazole at week 6 (p=0.003). All-cause mortality at 12 weeks: 18% vs 27% (p=0.04). Critically, FungoClear has no CYP450 interactions — a major advantage in transplant patients on tacrolimus/cyclosporine. Adverse events: mild hepatotoxicity (7%), rash (5%), QTc prolongation absent. Oral bioavailability 92%, allowing IV-to-oral step-down. Unique spectrum: active against Aspergillus fumigatus azole-resistant strains (TR34/L98H mutation).""",
    },
    "infect_B": {
        "category": "infectious_disease",
        "title": "BacterioShield (BS-6600) for Extensively Drug-Resistant Tuberculosis",
        "text": """Title: BacterioShield (BS-6600) for Extensively Drug-Resistant Tuberculosis: The SHIELD-TB Trial
Authors: Gupta V, Okonkwo DI, Larsen K
Journal: New England Journal of Medicine, 2024

A trial of 289 patients with XDR-TB (resistant to isoniazid, rifampicin, fluoroquinolones, and ≥1 injectable agent). BacterioShield targets the mycobacterial DprE1 enzyme, disrupting cell wall arabinogalactan synthesis. Culture conversion at 6 months: 71% vs 42% with best available regimen (p<0.001). Treatment success at 24 months: 64% vs 38%. BacterioShield enables a 9-month all-oral regimen (vs 18-20 months standard). Adverse events: peripheral neuropathy (14%), hepatotoxicity (9%, ALT >5× ULN), optic neuritis (2%). Required monitoring: monthly visual acuity tests and liver function panels. Drug interaction: increases bedaquiline levels by 35%, requiring dose reduction. Active against Beijing lineage strains.""",
    },
}

TRACK4_QUESTIONS = [
    # --- Inference questions (10) — need the correct paper ---
    {
        "question": "Based on VasoRelax's mechanism of action, why would it be particularly dangerous to prescribe to a pregnant patient?",
        "correct_paper": "cardio_A",
        "wrong_paper": "cardio_B",
        "expected_keywords": ["endothelin", "pregnancy", "category X", "contraindicated"],
        "min_keywords": 2,
        "type": "inference",
    },
    {
        "question": "If a patient on RhythmGuard shows a QTc interval of 510ms at their week-4 ECG, what should be the clinical response?",
        "correct_paper": "cardio_B",
        "wrong_paper": "cardio_A",
        "expected_keywords": ["discontinu", "QT", "prolong", "500ms", "stop"],
        "min_keywords": 2,
        "type": "inference",
    },
    {
        "question": "Why should APOE genotyping be performed before starting ClearMind therapy?",
        "correct_paper": "neuro_A",
        "wrong_paper": "neuro_B",
        "expected_keywords": ["APOE4", "ARIA", "risk", "homozygote", "edema", "3"],
        "min_keywords": 2,
        "type": "inference",
    },
    {
        "question": "What is the critical time constraint for administering SpinalBridge and why?",
        "correct_paper": "neuro_B",
        "wrong_paper": "neuro_A",
        "expected_keywords": ["96 hours", "72 hours", "acute", "window", "efficacy", "injury"],
        "min_keywords": 2,
        "type": "inference",
    },
    {
        "question": "Why is SOX10 testing recommended as a companion diagnostic for OncoLock?",
        "correct_paper": "onco_A",
        "wrong_paper": "onco_B",
        "expected_keywords": ["SOX10", "response", "ORR", "58%", "21%", "biomarker", "predict"],
        "min_keywords": 2,
        "type": "inference",
    },
    {
        "question": "What fundamental limitation makes TumorVax ineffective for some NSCLC patients?",
        "correct_paper": "onco_B",
        "wrong_paper": "onco_A",
        "expected_keywords": ["TMB", "mutational burden", "low", "neoantigen", "8%"],
        "min_keywords": 2,
        "type": "inference",
    },
    {
        "question": "What makes FungoClear particularly advantageous for transplant patients compared to existing antifungals?",
        "correct_paper": "infect_A",
        "wrong_paper": "infect_B",
        "expected_keywords": ["CYP450", "interaction", "tacrolimus", "cyclosporine", "no"],
        "min_keywords": 2,
        "type": "inference",
    },
    {
        "question": "How does BacterioShield's treatment regimen improve upon standard XDR-TB therapy duration?",
        "correct_paper": "infect_B",
        "wrong_paper": "infect_A",
        "expected_keywords": ["9-month", "9 month", "oral", "18", "20", "shorter"],
        "min_keywords": 2,
        "type": "inference",
    },
    {
        "question": "Given OncoLock's adverse event profile, what cardiac monitoring would you recommend?",
        "correct_paper": "onco_A",
        "wrong_paper": "onco_B",
        "expected_keywords": ["QTc", "cardiac", "ECG", "prolong", "5%"],
        "min_keywords": 2,
        "type": "inference",
    },
    {
        "question": "Why is FungoClear effective against azole-resistant Aspergillus strains?",
        "correct_paper": "infect_A",
        "wrong_paper": "infect_B",
        "expected_keywords": ["DHODH", "orotomide", "different", "target", "mechanism", "TR34", "azole"],
        "min_keywords": 2,
        "type": "inference",
    },
    # --- Multi-hop questions (5) — need correct paper + reasoning ---
    {
        "question": "A transplant patient on cyclosporine develops invasive aspergillosis. Explain why FungoClear is preferable to voriconazole in this scenario, referencing specific pharmacological properties.",
        "correct_paper": "infect_A",
        "wrong_paper": "infect_B",
        "expected_keywords": ["CYP450", "cyclosporine", "interaction", "voriconazole", "no interaction", "tacrolimus"],
        "min_keywords": 3,
        "type": "multihop",
    },
    {
        "question": "A 50-year-old APOE4 homozygote with early Alzheimer's is being considered for ClearMind. Calculate their ARIA-E risk and explain whether the benefit justifies this risk given the 35% cognitive decline reduction.",
        "correct_paper": "neuro_A",
        "wrong_paper": "neuro_B",
        "expected_keywords": ["41%", "ARIA", "APOE4", "homozygote", "35%", "risk-benefit", "cognitive"],
        "min_keywords": 3,
        "type": "multihop",
    },
    {
        "question": "An XDR-TB patient is also taking bedaquiline. How must the BacterioShield regimen be modified and what monitoring is essential?",
        "correct_paper": "infect_B",
        "wrong_paper": "infect_A",
        "expected_keywords": ["bedaquiline", "35%", "dose reduction", "visual", "liver", "monitor"],
        "min_keywords": 3,
        "type": "multihop",
    },
    {
        "question": "A metastatic TNBC patient is SOX10-negative. Based on OncoLock trial data, what is their expected response rate and should an alternative be considered?",
        "correct_paper": "onco_A",
        "wrong_paper": "onco_B",
        "expected_keywords": ["21%", "SOX10", "negative", "lower", "alternative", "chemotherapy"],
        "min_keywords": 3,
        "type": "multihop",
    },
    {
        "question": "An acute spinal cord injury patient arrives 80 hours post-injury. Is SpinalBridge still an option? What logistics must be arranged given the storage requirements?",
        "correct_paper": "neuro_B",
        "wrong_paper": "neuro_A",
        "expected_keywords": ["96 hours", "window", "-80", "thaw", "4 hour", "CT-guided", "time"],
        "min_keywords": 3,
        "type": "multihop",
    },
    # --- Trick questions (5) — wrong paper gives plausible but WRONG answer ---
    {
        "question": "What is the rate of hepatotoxicity reported in the FungoClear trial?",
        "correct_paper": "infect_A",
        "wrong_paper": "infect_B",
        "expected_answer": "7%",
        "wrong_answer": "9%",
        "expected_keywords": ["7%"],
        "min_keywords": 1,
        "type": "trick",
        "explanation": "FungoClear (infect_A) has 7% hepatotoxicity. BacterioShield (infect_B) has 9%. Wrong paper gives a plausible but incorrect number.",
    },
    {
        "question": "What was the overall response rate for the CDK7 inhibitor in the breast cancer trial?",
        "correct_paper": "onco_A",
        "wrong_paper": "onco_B",
        "expected_answer": "42%",
        "wrong_answer": "12%",
        "expected_keywords": ["42%"],
        "min_keywords": 1,
        "type": "trick",
        "explanation": "OncoLock (onco_A) ORR is 42%. TumorVax (onco_B) complete response is 12% — wrong paper gives a different cancer drug's number.",
    },
    {
        "question": "What is the mortality reduction at 12 weeks in the invasive aspergillosis trial?",
        "correct_paper": "infect_A",
        "wrong_paper": "infect_B",
        "expected_answer": "18% vs 27%",
        "wrong_answer": "64% vs 38%",
        "expected_keywords": ["18%", "27%"],
        "min_keywords": 1,
        "type": "trick",
        "explanation": "FungoClear mortality is 18% vs 27%. BacterioShield (wrong paper) reports treatment success 64% vs 38% — a different metric entirely.",
    },
    {
        "question": "In the resistant hypertension trial, what was the systolic BP reduction in the treatment group?",
        "correct_paper": "cardio_A",
        "wrong_paper": "cardio_B",
        "expected_answer": "22.4 mmHg",
        "wrong_answer": "61%",
        "expected_keywords": ["22.4", "mmHg"],
        "min_keywords": 1,
        "type": "trick",
        "explanation": "VasoRelax (cardio_A) reduced BP by 22.4 mmHg. RhythmGuard (cardio_B) reports 61% sinus rhythm — wrong paper gives completely different endpoint.",
    },
    {
        "question": "How many ASIA grades of improvement did patients achieve with the neural scaffold treatment at 12 months?",
        "correct_paper": "neuro_B",
        "wrong_paper": "neuro_A",
        "expected_answer": "≥2 ASIA grades in 31%",
        "wrong_answer": "35%",
        "expected_keywords": ["2 ASIA", "31%", "grade"],
        "min_keywords": 1,
        "type": "trick",
        "explanation": "SpinalBridge (neuro_B) shows ≥2 ASIA grade improvement in 31%. ClearMind (neuro_A) shows 35% slowing — wrong paper gives a percentage that seems plausible but is a completely different measure.",
    },
]


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def score_keywords(response: str, expected_keywords: list[str], min_keywords: int = 1) -> tuple[bool, list[str]]:
    """Score by checking if enough expected keywords appear in the response."""
    resp_lower = response.lower()
    found = [kw for kw in expected_keywords if kw.lower() in resp_lower]
    return len(found) >= min_keywords, found


def extract_mcq_answer(response: str) -> str:
    """Extract MCQ letter answer, handling thinking mode output."""
    # Strip thinking block if present
    text = response
    if "</think>" in text:
        text = text.split("</think>")[-1]

    text = text.strip()

    # Look for patterns like "A)", "A.", "**A**", "Answer: A", "The answer is A"
    patterns = [
        r"(?:the answer is|answer is|answer:)\s*\(?([A-D])\)?",
        r"^\s*\(?([A-D])\)?[\.\):]",
        r"\*\*([A-D])\*\*",
        r"\b([A-D])\)",
        r"^([A-D])$",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).upper()

    # Last resort: first standalone letter A-D in the text
    m = re.search(r"\b([A-D])\b", text)
    if m:
        return m.group(1).upper()

    return "?"


# ---------------------------------------------------------------------------
# Core inference functions
# ---------------------------------------------------------------------------

def load_model():
    """Load model and tokenizer."""
    print(f"Loading {MODEL_ID}...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID, dtype=torch.float16, device_map="cuda", trust_remote_code=True
    )
    model.eval()
    print(f"Model loaded. Device: {next(model.parameters()).device}")
    return tokenizer, model


def build_cache(text: str, tokenizer, model):
    """Pre-compute KV cache from knowledge text."""
    msgs = [{"role": "system", "content": text}]
    prefix = tokenizer.apply_chat_template(msgs, tokenize=False, enable_thinking=False)
    ids = tokenizer(prefix, return_tensors="pt", truncation=True, max_length=8192).input_ids.to("cuda")
    prefix_len = ids.shape[1]
    with torch.no_grad():
        out = model(ids, use_cache=True)
    return out.past_key_values, prefix_len


def clone_cache(cache):
    """Deep clone a KV cache for reuse."""
    c = DynamicCache()
    for layer in cache.layers:
        c.update(layer.keys.clone(), layer.values.clone(), len(c.layers))
    return c


def query_with_cache(question: str, cache, prefix_len: int, tokenizer, model,
                     max_new: int = 200, enable_thinking: bool = False) -> tuple[str, float]:
    """Query model using pre-computed KV cache (Memory Palace)."""
    c = clone_cache(cache)
    msgs = [{"role": "user", "content": question}]
    q_text = tokenizer.apply_chat_template(
        msgs, tokenize=False, add_generation_prompt=True, enable_thinking=enable_thinking
    )
    q_ids = tokenizer(q_text, return_tensors="pt").input_ids.to("cuda")
    q_len = q_ids.shape[1]

    pos = torch.arange(prefix_len, prefix_len + q_len, device="cuda").unsqueeze(0)
    mask = torch.ones(1, prefix_len + q_len, device="cuda", dtype=torch.long)

    t0 = time.time()
    with torch.no_grad():
        out = model.generate(
            q_ids, past_key_values=c,
            position_ids=pos, attention_mask=mask,
            max_new_tokens=max_new, do_sample=False,
        )
    latency = time.time() - t0
    response = tokenizer.decode(out[0][q_len:], skip_special_tokens=True)
    return response, latency


def query_baseline(question: str, tokenizer, model,
                   max_new: int = 200, enable_thinking: bool = False) -> tuple[str, float]:
    """Query model with no context (baseline)."""
    msgs = [{"role": "user", "content": question}]
    text = tokenizer.apply_chat_template(
        msgs, tokenize=False, add_generation_prompt=True, enable_thinking=enable_thinking
    )
    ids = tokenizer(text, return_tensors="pt").input_ids.to("cuda")
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=max_new, do_sample=False)
    latency = time.time() - t0
    # For thinking mode, decode with tags so we can parse <think>...</think>
    if enable_thinking:
        response = tokenizer.decode(out[0][ids.shape[1]:], skip_special_tokens=False)
        # Strip end-of-sequence tokens
        for tok in ["<|endoftext|>", "<|im_end|>"]:
            response = response.replace(tok, "")
    else:
        response = tokenizer.decode(out[0][ids.shape[1]:], skip_special_tokens=True)
    return response, latency


def query_rag(question: str, knowledge_text: str, tokenizer, model,
              max_new: int = 200, enable_thinking: bool = False) -> tuple[str, float]:
    """Query model with knowledge in prompt (RAG simulation)."""
    msgs = [
        {"role": "system", "content": knowledge_text},
        {"role": "user", "content": question},
    ]
    text = tokenizer.apply_chat_template(
        msgs, tokenize=False, add_generation_prompt=True, enable_thinking=enable_thinking
    )
    ids = tokenizer(text, return_tensors="pt").input_ids.to("cuda")
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=max_new, do_sample=False)
    latency = time.time() - t0
    response = tokenizer.decode(out[0][ids.shape[1]:], skip_special_tokens=True)
    return response, latency


# ---------------------------------------------------------------------------
# Track 1v3: Graded Difficulty Novel Knowledge
# ---------------------------------------------------------------------------

def run_track1(tokenizer, model) -> dict:
    """Track 1v3: Graded difficulty — easy to very-hard questions with 3 methods."""
    print("\n" + "=" * 70)
    print("TRACK 1v3: Graded Difficulty Novel Knowledge")
    print("=" * 70)

    all_papers = f"{PAPER_A}\n\n---\n\n{PAPER_B}\n\n---\n\n{PAPER_C}"

    # Build cache with all 3 papers
    print("Building Memory Palace cache (all 3 papers)...")
    cache, prefix_len = build_cache(all_papers, tokenizer, model)
    print(f"  Cache built: {prefix_len} tokens")

    results = {"track": "1v3", "model": MODEL_ID, "timestamp": datetime.now().isoformat()}
    difficulty_results = {}

    for difficulty, questions in TRACK1_QUESTIONS.items():
        print(f"\n--- Difficulty: {difficulty.upper()} ({len(questions)} questions) ---")
        level_details = []
        scores = {"baseline": 0, "palace": 0, "rag": 0}

        for i, q in enumerate(questions):
            qtext = q["question"]
            keywords = q["expected_keywords"]
            min_kw = q.get("min_keywords", 1)
            print(f"\n  Q{i+1}: {qtext[:80]}...")

            # Baseline
            resp_b, lat_b = query_baseline(qtext, tokenizer, model, max_new=300)
            pass_b, found_b = score_keywords(resp_b, keywords, min_kw)
            scores["baseline"] += int(pass_b)
            print(f"    Baseline: {'PASS' if pass_b else 'FAIL'} ({lat_b:.1f}s) found={found_b}")

            # Memory Palace
            resp_p, lat_p = query_with_cache(qtext, cache, prefix_len, tokenizer, model, max_new=300)
            pass_p, found_p = score_keywords(resp_p, keywords, min_kw)
            scores["palace"] += int(pass_p)
            print(f"    Palace:   {'PASS' if pass_p else 'FAIL'} ({lat_p:.1f}s) found={found_p}")

            # RAG
            resp_r, lat_r = query_rag(qtext, all_papers, tokenizer, model, max_new=300)
            pass_r, found_r = score_keywords(resp_r, keywords, min_kw)
            scores["rag"] += int(pass_r)
            print(f"    RAG:      {'PASS' if pass_r else 'FAIL'} ({lat_r:.1f}s) found={found_r}")

            level_details.append({
                "question": qtext,
                "expected_keywords": keywords,
                "baseline": {"response": resp_b, "pass": pass_b, "found_keywords": found_b, "latency": lat_b},
                "palace": {"response": resp_p, "pass": pass_p, "found_keywords": found_p, "latency": lat_p},
                "rag": {"response": resp_r, "pass": pass_r, "found_keywords": found_r, "latency": lat_r},
            })

        n = len(questions)
        difficulty_results[difficulty] = {
            "baseline": scores["baseline"],
            "palace": scores["palace"],
            "rag": scores["rag"],
            "total": n,
            "baseline_pct": round(100 * scores["baseline"] / n, 1),
            "palace_pct": round(100 * scores["palace"] / n, 1),
            "rag_pct": round(100 * scores["rag"] / n, 1),
            "details": level_details,
        }
        print(f"\n  {difficulty.upper()} SCORES: Baseline={scores['baseline']}/{n}  Palace={scores['palace']}/{n}  RAG={scores['rag']}/{n}")

    results["results"] = difficulty_results

    # Summary
    print("\n--- TRACK 1v3 SUMMARY ---")
    for diff in ["easy", "medium", "hard", "very_hard"]:
        r = difficulty_results[diff]
        print(f"  {diff:10s}: Baseline={r['baseline_pct']:5.1f}%  Palace={r['palace_pct']:5.1f}%  RAG={r['rag_pct']:5.1f}%")

    # Save
    out_path = RESULTS_DIR / "track_1v3_graded_difficulty.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved: {out_path}")

    # Cleanup
    del cache
    gc.collect()
    torch.cuda.empty_cache()

    return results


# ---------------------------------------------------------------------------
# Track 2v3: MMLU with Thinking Mode
# ---------------------------------------------------------------------------

def run_track2(tokenizer, model) -> dict:
    """Track 2v3: MMLU-style questions — no-thinking vs thinking mode."""
    print("\n" + "=" * 70)
    print("TRACK 2v3: MMLU with Thinking Mode")
    print("=" * 70)

    results = {"track": "2v3", "model": MODEL_ID, "timestamp": datetime.now().isoformat()}
    subject_results = {}

    for subject, questions in TRACK2_QUESTIONS.items():
        print(f"\n--- Subject: {subject.upper()} ({len(questions)} questions) ---")
        details = []
        scores_nothink = 0
        scores_think = 0

        for i, q in enumerate(questions):
            qtext = q["question"]
            correct = q["answer"]
            print(f"\n  Q{i+1}: {qtext[:60]}... [correct={correct}]")

            # No thinking mode
            resp_nt, lat_nt = query_baseline(qtext, tokenizer, model, max_new=50, enable_thinking=False)
            answer_nt = extract_mcq_answer(resp_nt)
            pass_nt = answer_nt == correct
            scores_nothink += int(pass_nt)
            print(f"    No-think: {answer_nt} {'✓' if pass_nt else '✗'} ({lat_nt:.1f}s)")

            # With thinking mode
            resp_th, lat_th = query_baseline(qtext, tokenizer, model, max_new=2048, enable_thinking=True)
            answer_th = extract_mcq_answer(resp_th)
            pass_th = answer_th == correct
            scores_think += int(pass_th)
            print(f"    Thinking: {answer_th} {'✓' if pass_th else '✗'} ({lat_th:.1f}s)")

            details.append({
                "question": qtext,
                "correct_answer": correct,
                "no_thinking": {"response": resp_nt, "extracted": answer_nt, "correct": pass_nt, "latency": lat_nt},
                "thinking": {"response": resp_th, "extracted": answer_th, "correct": pass_th, "latency": lat_th},
            })

        n = len(questions)
        subject_results[subject] = {
            "no_thinking": scores_nothink,
            "thinking": scores_think,
            "total": n,
            "no_thinking_pct": round(100 * scores_nothink / n, 1),
            "thinking_pct": round(100 * scores_think / n, 1),
            "details": details,
        }
        print(f"\n  {subject.upper()}: No-think={scores_nothink}/{n} ({100*scores_nothink/n:.0f}%)  Thinking={scores_think}/{n} ({100*scores_think/n:.0f}%)")

    results["results"] = subject_results

    # Aggregate
    total_nt = sum(r["no_thinking"] for r in subject_results.values())
    total_th = sum(r["thinking"] for r in subject_results.values())
    total_q = sum(r["total"] for r in subject_results.values())
    results["aggregate"] = {
        "no_thinking": total_nt,
        "thinking": total_th,
        "total": total_q,
        "no_thinking_pct": round(100 * total_nt / total_q, 1),
        "thinking_pct": round(100 * total_th / total_q, 1),
    }

    print("\n--- TRACK 2v3 SUMMARY ---")
    for subj, r in subject_results.items():
        print(f"  {subj:12s}: No-think={r['no_thinking_pct']:5.1f}%  Thinking={r['thinking_pct']:5.1f}%")
    print(f"  {'AGGREGATE':12s}: No-think={results['aggregate']['no_thinking_pct']:5.1f}%  Thinking={results['aggregate']['thinking_pct']:5.1f}%")

    out_path = RESULTS_DIR / "track_2v3_mmlu_thinking.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved: {out_path}")

    return results


# ---------------------------------------------------------------------------
# Track 4v3: Realistic Categorization
# ---------------------------------------------------------------------------

def run_track4(tokenizer, model) -> dict:
    """Track 4v3: Categorization that REQUIRES the correct paper."""
    print("\n" + "=" * 70)
    print("TRACK 4v3: Realistic Categorization")
    print("=" * 70)

    # Pre-build caches for all 8 papers
    print("Building caches for all 8 papers...")
    caches = {}
    for paper_id, paper in TRACK4_PAPERS.items():
        cache, prefix_len = build_cache(paper["text"], tokenizer, model)
        caches[paper_id] = (cache, prefix_len)
        print(f"  {paper_id}: {prefix_len} tokens")

    # Pick a random paper for each question (deterministic seed)
    import random
    random.seed(42)
    all_paper_ids = list(TRACK4_PAPERS.keys())

    results = {"track": "4v3", "model": MODEL_ID, "timestamp": datetime.now().isoformat()}
    condition_results = {"good": [], "bad": [], "random": []}
    condition_scores = {
        "good": {"inference": 0, "multihop": 0, "trick": 0, "total_i": 0, "total_m": 0, "total_t": 0},
        "bad": {"inference": 0, "multihop": 0, "trick": 0, "total_i": 0, "total_m": 0, "total_t": 0},
        "random": {"inference": 0, "multihop": 0, "trick": 0, "total_i": 0, "total_m": 0, "total_t": 0},
    }

    for qi, q in enumerate(TRACK4_QUESTIONS):
        qtext = q["question"]
        qtype = q["type"]
        correct_id = q["correct_paper"]
        wrong_id = q["wrong_paper"]
        keywords = q["expected_keywords"]
        min_kw = q.get("min_keywords", 1)

        # Random paper (not correct, not wrong — truly random)
        random_id = random.choice([pid for pid in all_paper_ids if pid != correct_id])

        print(f"\n  Q{qi+1} [{qtype}]: {qtext[:70]}...")

        q_detail = {"question": qtext, "type": qtype, "correct_paper": correct_id, "wrong_paper": wrong_id}

        for condition, paper_id in [("good", correct_id), ("bad", wrong_id), ("random", random_id)]:
            cache, prefix_len = caches[paper_id]
            resp, lat = query_with_cache(qtext, cache, prefix_len, tokenizer, model, max_new=300)
            passed, found = score_keywords(resp, keywords, min_kw)

            type_key = qtype
            condition_scores[condition][f"total_{type_key[0]}"] += 1
            condition_scores[condition][type_key] += int(passed)

            label = f"{condition:6s} (paper={paper_id})"
            print(f"    {label}: {'PASS' if passed else 'FAIL'} ({lat:.1f}s) found={found}")

            condition_results[condition].append({
                "question": qtext,
                "type": qtype,
                "paper_loaded": paper_id,
                "response": resp,
                "pass": passed,
                "found_keywords": found,
                "latency": lat,
            })

    # Build summary
    summary = {}
    for cond in ["good", "bad", "random"]:
        cs = condition_scores[cond]
        total = cs["total_i"] + cs["total_m"] + cs["total_t"]
        correct = cs["inference"] + cs["multihop"] + cs["trick"]
        summary[cond] = {
            "overall": correct,
            "overall_total": total,
            "overall_pct": round(100 * correct / total, 1) if total else 0,
            "inference": cs["inference"],
            "inference_total": cs["total_i"],
            "inference_pct": round(100 * cs["inference"] / cs["total_i"], 1) if cs["total_i"] else 0,
            "multihop": cs["multihop"],
            "multihop_total": cs["total_m"],
            "multihop_pct": round(100 * cs["multihop"] / cs["total_m"], 1) if cs["total_m"] else 0,
            "trick": cs["trick"],
            "trick_total": cs["total_t"],
            "trick_pct": round(100 * cs["trick"] / cs["total_t"], 1) if cs["total_t"] else 0,
            "details": condition_results[cond],
        }

    results["results"] = summary

    print("\n--- TRACK 4v3 SUMMARY ---")
    for cond in ["good", "bad", "random"]:
        s = summary[cond]
        print(f"  {cond:8s}: Overall={s['overall_pct']:5.1f}%  Inference={s['inference_pct']:5.1f}%  MultiHop={s['multihop_pct']:5.1f}%  Trick={s['trick_pct']:5.1f}%")

    out_path = RESULTS_DIR / "track_4v3_categorization.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved: {out_path}")

    # Cleanup
    for cache, _ in caches.values():
        del cache
    del caches
    gc.collect()
    torch.cuda.empty_cache()

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Memory Palace v3 — Harder Validation Tracks")
    print(f"Model: {MODEL_ID}")
    print(f"Results: {RESULTS_DIR}")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 70)

    tokenizer, model = load_model()

    all_results = {}

    # Track 1v3
    try:
        all_results["track1v3"] = run_track1(tokenizer, model)
    except Exception as e:
        print(f"\n!!! TRACK 1v3 FAILED: {e}")
        import traceback; traceback.print_exc()

    # Track 2v3
    try:
        all_results["track2v3"] = run_track2(tokenizer, model)
    except Exception as e:
        print(f"\n!!! TRACK 2v3 FAILED: {e}")
        import traceback; traceback.print_exc()

    # Track 4v3
    try:
        all_results["track4v3"] = run_track4(tokenizer, model)
    except Exception as e:
        print(f"\n!!! TRACK 4v3 FAILED: {e}")
        import traceback; traceback.print_exc()

    # Final summary
    print("\n" + "=" * 70)
    print("ALL TRACKS COMPLETE")
    print(f"Finished: {datetime.now().isoformat()}")
    print("=" * 70)

    # Save combined results
    combined_path = RESULTS_DIR / "all_tracks_v3_combined.json"
    with open(combined_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Combined results: {combined_path}")


if __name__ == "__main__":
    main()
