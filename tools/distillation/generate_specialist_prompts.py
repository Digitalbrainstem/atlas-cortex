#!/usr/bin/env python3
"""Generate postdoc/researcher-level specialist prompts for all 22 LoRA domains.

Outputs individual JSONL files per domain to data/distillation/specialist/.
Each prompt is structured as {"id": "domain_NNNN", "domain": "...", "prompt": "..."}.

Usage:
    python -m tools.distillation.generate_specialist_prompts
    python tools/distillation/generate_specialist_prompts.py
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "distillation" / "specialist"

# ---------------------------------------------------------------------------
# Prompt templates — each domain has multiple template styles to ensure
# diversity: explain, compare, derive, critique, design, troubleshoot, etc.
# ---------------------------------------------------------------------------

TEMPLATES = {
    "explain_mechanism": "Explain the mechanism of {topic} in detail, including the underlying {framework} principles. What are the current limitations and open research questions?",
    "compare_approaches": "Compare and contrast {approach_a} and {approach_b} for {application}. Under what conditions does each approach excel, and what are the trade-offs?",
    "derive": "Derive the {equation_or_relationship} from first principles. Show each step and explain the physical/mathematical significance of the key assumptions.",
    "design_system": "Design a {system} for {application}. Specify the key components, their interactions, performance requirements, and how you would validate the design.",
    "critique_paper": "A recent paper proposes {claim}. Critically evaluate this claim: what are the strengths of the evidence, potential confounds, and what experiments would strengthen or refute it?",
    "troubleshoot": "A {system} is exhibiting {symptom}. Walk through a systematic diagnostic approach, listing the most likely causes in order of probability and the tests to confirm each.",
    "cutting_edge": "What are the most promising recent advances in {field}? Describe 2-3 breakthroughs from the last 2 years, their significance, and remaining challenges.",
    "interdisciplinary": "How does {concept_a} from {field_a} relate to {concept_b} from {field_b}? Explain the cross-disciplinary connections and potential for synergistic research.",
    "quantitative": "Given {parameters}, calculate {target}. Show your work step by step, state all assumptions, and discuss how sensitive the result is to each assumption.",
    "historical_development": "Trace the historical development of {topic} from its origins to the current state of the art. What were the key paradigm shifts and who drove them?",
    "ethical_implications": "Discuss the ethical implications of {technology_or_practice}. What safeguards should be in place, and how do different ethical frameworks evaluate it?",
    "experimental_design": "Design an experiment to test {hypothesis}. Specify the independent and dependent variables, controls, sample size considerations, and statistical analysis plan.",
    "review_synthesis": "Synthesize the current understanding of {topic} across multiple sub-fields. Where is there consensus, where is there disagreement, and what are the key open questions?",
    "failure_analysis": "Analyze the failure of {case}. What went wrong, what were the warning signs, and what design/process changes would prevent recurrence?",
    "scale_challenge": "Discuss the challenges of scaling {technology} from laboratory to industrial/clinical/production scale. What are the key bottlenecks and proposed solutions?",
}

# ---------------------------------------------------------------------------
# Domain definitions: each has subtopics and domain-specific prompt seeds
# ---------------------------------------------------------------------------

DOMAINS: dict[str, dict] = {}

# ── ENGINEERING ────────────────────────────────────────────────────────────

DOMAINS["aerospace"] = {
    "count": 2500,
    "topics": [
        ("supersonic boundary layer transition", "compressible flow"),
        ("scramjet combustion instability", "hypersonic propulsion"),
        ("orbital debris collision probability", "Kessler syndrome"),
        ("solar sail attitude control", "radiation pressure dynamics"),
        ("composite cryotank design for LOX/LH2", "thermal cycling fatigue"),
        ("reusable TPS materials for Mars entry", "ablation modeling"),
        ("ion thruster beam divergence", "Hall-effect vs gridded"),
        ("satellite constellation mega-constellation interference", "spectrum management"),
        ("aeroelastic flutter prediction", "unsteady CFD coupling"),
        ("additive manufacturing of turbine blades", "Inconel 718 microstructure"),
        ("autonomous rendezvous and docking GNC", "relative navigation"),
        ("plasma-assisted combustion", "nanosecond pulse discharge"),
        ("space debris active removal", "net capture vs harpoon"),
        ("hypersonic waverider optimization", "shock-body interaction"),
        ("electric propulsion mission design", "low-thrust trajectory optimization"),
        ("re-entry vehicle thermal protection", "UHTC ceramics"),
        ("satellite drag estimation", "thermospheric density models"),
        ("structural health monitoring in aircraft", "Lamb wave propagation"),
        ("morphing wing mechanisms", "shape memory alloys vs piezoelectric"),
        ("launch vehicle stage separation dynamics", "multi-body simulation"),
        ("space nuclear propulsion", "NERVA heritage vs modern designs"),
        ("CubeSat propulsion systems", "electrospray vs cold gas"),
        ("rotorcraft blade vortex interaction", "acoustic prediction"),
        ("scramjet inlet design", "shock train dynamics"),
        ("cislunar trajectory design", "three-body problem halo orbits"),
    ],
    "systems": [
        "adaptive flight control system for damaged aircraft",
        "GNC system for precision lunar landing",
        "thermal management system for high-power LEO satellite",
        "propellant feed system for pressure-fed bipropellant engine",
        "autonomous UAV swarm coordination architecture",
    ],
    "claims": [
        "rotating detonation engines can achieve 25% higher Isp than conventional rocket engines",
        "machine learning can replace RANS turbulence models for aerodynamic design",
        "space elevators are economically viable with current carbon nanotube technology",
        "reusable launch vehicles will reduce cost-to-orbit below $100/kg by 2030",
    ],
}

DOMAINS["mechanical"] = {
    "count": 2500,
    "topics": [
        ("creep-fatigue interaction in Ni-based superalloys", "Larson-Miller parameter"),
        ("topology optimization under manufacturing constraints", "additive manufacturing"),
        ("MEMS resonator nonlinear dynamics", "Duffing oscillator"),
        ("tribological behavior of DLC coatings", "pin-on-disc characterization"),
        ("digital twin for predictive maintenance", "physics-informed neural networks"),
        ("metamaterial auxetic structures", "negative Poisson's ratio"),
        ("friction stir welding of dissimilar metals", "Al-Cu joint microstructure"),
        ("biomimetic surface texturing for drag reduction", "shark skin riblets"),
        ("peridynamics for fracture simulation", "bond-based vs state-based"),
        ("thermoelectric generator efficiency", "Seebeck coefficient optimization"),
        ("compliant mechanism design", "pseudo-rigid-body model"),
        ("ice accretion modeling on wind turbines", "Messinger model"),
        ("acoustic emission for crack detection", "wavelet analysis"),
        ("metal foam energy absorption", "Gibson-Ashby scaling"),
        ("soft robotics actuator modeling", "McKibben artificial muscles"),
        ("shape memory polymer composites", "recovery stress modeling"),
        ("laser powder bed fusion process simulation", "melt pool dynamics"),
        ("hydraulic fracturing mechanics", "cohesive zone modeling"),
        ("vibration energy harvesting", "piezoelectric cantilever optimization"),
        ("multi-scale modeling of composites", "homogenization theory"),
        ("bearing fault diagnosis", "envelope analysis and kurtogram"),
        ("thermal barrier coating failure", "TGO growth and spallation"),
        ("lattice structure design for lightweight components", "strut-based vs TPMS"),
        ("contact mechanics of rough surfaces", "Greenwood-Williamson model"),
        ("cryogenic material behavior", "ductile-to-brittle transition"),
    ],
    "systems": [
        "variable-stiffness prosthetic ankle joint",
        "self-healing structural composite for aircraft fuselage",
        "micro-scale heat exchanger for electronics cooling",
        "robotic end-effector for delicate fruit harvesting",
        "passive vibration isolation platform for precision instruments",
    ],
    "claims": [
        "generative design AI can outperform experienced engineers in topology optimization",
        "4D printing will make traditional assembly processes obsolete for shape-adaptive structures",
        "perovskite thermoelectrics will surpass bismuth telluride ZT values within 5 years",
    ],
}

DOMAINS["chemical_eng"] = {
    "count": 2500,
    "topics": [
        ("single-atom catalyst selectivity", "coordination environment effects"),
        ("MOF-based membrane separation", "gas permeability/selectivity trade-off"),
        ("continuous flow photochemistry", "microreactor scale-up"),
        ("electrochemical CO2 reduction to ethanol", "copper catalyst selectivity"),
        ("polymer electrolyte membrane degradation", "Fenton degradation mechanism"),
        ("reactive distillation column design", "TAME synthesis"),
        ("nanocellulose production and applications", "TEMPO-mediated oxidation"),
        ("process intensification via oscillatory baffled reactor", "mixing characterization"),
        ("solid oxide electrolysis cell degradation", "delamination mechanisms"),
        ("supercritical CO2 extraction optimization", "solubility modeling"),
        ("fluidized bed reactor hydrodynamics", "CFD-DEM coupling"),
        ("enzymatic biodiesel production", "lipase immobilization"),
        ("lithium-sulfur battery polysulfide shuttle", "interlayer design"),
        ("zeolite synthesis and characterization", "structure-directing agents"),
        ("pharmaceutical crystallization control", "polymorphism and nucleation"),
        ("carbon capture with amine scrubbing", "solvent degradation pathways"),
        ("perovskite solar cell scale-up", "slot-die coating optimization"),
        ("hydrogen storage in metal hydrides", "thermodynamic destabilization"),
        ("atomic layer deposition for catalysis", "conformal coating in porous media"),
        ("biomass pyrolysis mechanism", "cellulose decomposition pathways"),
        ("membrane bioreactor fouling", "EPS characterization"),
        ("electrospinning nanofiber membranes", "Taylor cone stability"),
        ("Sabatier reaction for Mars ISRU", "catalyst deactivation"),
        ("redox flow battery electrolyte design", "vanadium vs organic"),
        ("microplastic degradation", "advanced oxidation processes"),
    ],
    "systems": [
        "modular green hydrogen production plant",
        "continuous pharmaceutical manufacturing line with PAT",
        "direct air capture system using solid sorbents",
        "closed-loop lithium battery recycling process",
        "artificial photosynthesis device for solar fuel production",
    ],
    "claims": [
        "MOF water harvesting can solve water scarcity in arid regions at scale",
        "solid-state batteries will replace liquid electrolyte cells for EVs by 2030",
        "direct lithium extraction from brine will eliminate mining dependency",
    ],
}

DOMAINS["biomedical_eng"] = {
    "count": 2500,
    "topics": [
        ("CRISPR delivery via lipid nanoparticles", "endosomal escape mechanisms"),
        ("brain-computer interface signal processing", "motor cortex decoding"),
        ("3D bioprinting vascularized tissue", "sacrificial ink strategies"),
        ("ultrasound-mediated drug delivery", "cavitation thresholds"),
        ("implantable glucose sensor drift", "foreign body response"),
        ("organ-on-chip microfluidics", "PDMS gas permeability effects"),
        ("neural probe chronic biocompatibility", "glial scarring mitigation"),
        ("cardiac tissue engineering", "electromechanical coupling maturation"),
        ("nanoparticle pharmacokinetics", "EPR effect controversy"),
        ("MRI-compatible implant materials", "susceptibility artifact reduction"),
        ("exosome-based therapeutics", "isolation and characterization"),
        ("optogenetic actuator design", "channelrhodopsin kinetics"),
        ("degradable metallic implants", "magnesium alloy corrosion control"),
        ("CAR-T cell manufacturing", "lentiviral transduction optimization"),
        ("wearable biosensor sweat analysis", "ion-selective electrode drift"),
        ("hydrogel scaffold stiffness tuning", "mechanotransduction"),
        ("cochlear implant electrode array design", "insertion trauma"),
        ("microRNA biomarker detection", "rolling circle amplification"),
        ("retinal prosthesis image processing", "phosphene mapping"),
        ("antimicrobial surface coatings", "contact-killing vs release-based"),
        ("musculoskeletal finite element modeling", "cartilage constitutive models"),
        ("targeted radionuclide therapy", "alpha vs beta emitters"),
        ("point-of-care diagnostics", "lateral flow assay optimization"),
        ("spinal cord stimulation", "dorsal column activation modeling"),
        ("tissue decellularization protocols", "ECM preservation assessment"),
    ],
    "systems": [
        "closed-loop deep brain stimulation system for Parkinson's",
        "wearable continuous glucose monitor with predictive alerts",
        "patient-specific 3D-printed cranial implant workflow",
        "lab-on-chip for rapid sepsis diagnosis",
        "bioresorbable vascular scaffold with drug elution",
    ],
    "claims": [
        "mRNA therapeutics will enable in-vivo tissue reprogramming within a decade",
        "current organ-on-chip models are sufficient to replace Phase I clinical trials",
        "brain organoids exhibit emergent consciousness-like activity",
    ],
}

DOMAINS["electrical_eng"] = {
    "count": 2500,
    "topics": [
        ("GaN HEMT reliability", "hot electron degradation"),
        ("silicon photonics Mach-Zehnder modulator", "electro-optic bandwidth"),
        ("grid-forming inverter control", "virtual synchronous machine"),
        ("mmWave 5G beamforming", "hybrid analog-digital architecture"),
        ("SiC MOSFET short-circuit ruggedness", "gate oxide reliability"),
        ("quantum dot single-photon source", "Purcell enhancement"),
        ("wireless power transfer efficiency", "magnetic resonance coupling"),
        ("neuromorphic chip architecture", "spiking neural network mapping"),
        ("high-voltage DC transmission", "modular multilevel converter"),
        ("MEMS inertial sensor bias stability", "Brownian noise floor"),
        ("radar signal processing", "CFAR detection in clutter"),
        ("power electronics thermal management", "double-sided cooling"),
        ("electromagnetic compatibility", "near-field scanning techniques"),
        ("photovoltaic maximum power point tracking", "incremental conductance vs P&O"),
        ("superconducting qubit coherence", "dielectric loss tangent"),
        ("flexible electronics strain sensors", "crack-based mechanism"),
        ("motor drive harmonic mitigation", "active power filter topologies"),
        ("antenna-in-package design", "substrate integrated waveguide"),
        ("battery management system", "state of health estimation"),
        ("FPGA-based real-time control", "hardware-in-the-loop validation"),
        ("terahertz imaging systems", "photoconductive antenna design"),
        ("energy harvesting for IoT", "rectenna efficiency"),
        ("power system stability analysis", "small-signal vs transient"),
        ("LiDAR receiver circuit design", "SiPM vs APD comparison"),
        ("digital predistortion for PA linearization", "memory polynomial model"),
    ],
    "systems": [
        "solid-state transformer for DC microgrid",
        "phased array radar for automotive ADAS",
        "implantable wireless neural recording system",
        "multi-port GaN charger with USB-PD",
        "grid-tied battery energy storage system with black start",
    ],
    "claims": [
        "room-temperature superconductors will revolutionize power transmission within 20 years",
        "neuromorphic computing will be more energy-efficient than GPUs for all AI workloads",
        "perovskite-silicon tandem cells will dominate the solar market by 2030",
    ],
}

DOMAINS["mechatronics"] = {
    "count": 2000,
    "topics": [
        ("sensor fusion for autonomous vehicles", "EKF vs UKF vs particle filter"),
        ("industrial robot compliance control", "impedance vs admittance"),
        ("PLC ladder logic to structured text migration", "IEC 61131-3"),
        ("Stewart platform kinematics", "workspace analysis"),
        ("SCADA system cybersecurity", "ICS-CERT threat landscape"),
        ("ROS 2 real-time control", "DDS middleware latency"),
        ("servo motor sizing methodology", "inertia matching"),
        ("machine vision defect detection", "deep learning vs traditional CV"),
        ("collaborative robot safety", "ISO/TS 15066 force limiting"),
        ("embedded Linux for real-time control", "PREEMPT_RT vs Xenomai"),
        ("pneumatic muscle actuator modeling", "hysteresis compensation"),
        ("CNC machine tool thermal error", "compensation strategies"),
        ("haptic feedback device design", "transparency and stability"),
        ("mobile robot SLAM", "LiDAR-inertial odometry"),
        ("EtherCAT vs PROFINET", "deterministic communication"),
        ("piezoelectric precision positioning", "hysteresis and creep"),
        ("quadruped locomotion control", "model predictive control"),
        ("automated guided vehicle fleet", "traffic management algorithms"),
        ("delta robot dynamics", "computed torque control"),
        ("condition monitoring with vibration analysis", "order tracking"),
    ],
    "systems": [
        "pick-and-place system with force-controlled assembly",
        "autonomous underwater vehicle with manipulator arm",
        "smart factory digital twin with real-time synchronization",
        "agricultural robot for selective harvesting",
        "exoskeleton for industrial lifting assistance",
    ],
    "claims": [
        "reinforcement learning will replace PID control in industrial applications",
        "soft robots will outperform rigid robots in unstructured environments",
    ],
}

# ── NATURAL SCIENCES ──────────────────────────────────────────────────────

DOMAINS["physics"] = {
    "count": 2500,
    "topics": [
        ("quantum entanglement entropy", "Page curve and black hole information"),
        ("topological insulator surface states", "ARPES measurements"),
        ("dark matter direct detection", "WIMP cross-section limits"),
        ("Bose-Einstein condensate vortex dynamics", "Gross-Pitaevskii equation"),
        ("quantum chromodynamics lattice calculations", "quark mass determination"),
        ("gravitational wave template bank", "post-Newtonian approximation"),
        ("high-harmonic generation in solids", "Bloch oscillation mechanism"),
        ("neutrino oscillation parameters", "PMNS matrix precision"),
        ("quantum error correction", "surface code threshold"),
        ("plasma wakefield acceleration", "beam loading and energy spread"),
        ("spin-orbit torque switching", "SOT-MRAM device physics"),
        ("Casimir effect measurement", "proximity force approximation"),
        ("quantum spin liquid candidates", "kitaev model materials"),
        ("ultrafast electron diffraction", "coherent phonon dynamics"),
        ("axion search experiments", "haloscope sensitivity"),
        ("topological superconductivity", "Majorana zero modes"),
        ("quantum thermodynamics", "fluctuation theorems"),
        ("metamaterial cloaking", "transformation optics"),
        ("muon g-2 anomaly", "hadronic vacuum polarization"),
        ("quantum simulation with cold atoms", "Hubbard model realization"),
        ("cosmic microwave background polarization", "B-mode detection"),
        ("high-temperature superconductor mechanism", "cuprate phase diagram"),
        ("quantum key distribution", "BB84 vs continuous-variable"),
        ("neutron star equation of state", "tidal deformability constraints"),
        ("photonic crystal fiber", "dispersion engineering"),
    ],
    "systems": [
        "trapped-ion quantum computer with 100+ qubits",
        "next-generation gravitational wave detector beyond LIGO",
        "compact proton therapy accelerator",
        "quantum sensor for gravitational field mapping",
        "fusion reactor plasma confinement diagnostic system",
    ],
    "claims": [
        "room-temperature superconductivity is achievable at ambient pressure with hydride materials",
        "quantum advantage has been definitively demonstrated for a practical problem",
        "the standard model is complete and no new particles will be found at foreseeable energies",
    ],
}

DOMAINS["chemistry"] = {
    "count": 2500,
    "topics": [
        ("asymmetric organocatalysis", "enamine/iminium activation"),
        ("metal-organic framework gas storage", "methane vs hydrogen uptake"),
        ("CRISPR-based chemical biology tools", "proximity labeling"),
        ("mechanochemistry for green synthesis", "ball milling parameters"),
        ("electrochemical nitrogen reduction", "selectivity vs HER competition"),
        ("machine learning force fields", "neural network potentials accuracy"),
        ("total synthesis strategies", "retrosynthetic analysis of natural products"),
        ("surface-enhanced Raman spectroscopy", "single-molecule detection"),
        ("photocatalytic water splitting", "Z-scheme vs tandem systems"),
        ("supramolecular polymer self-assembly", "host-guest chemistry"),
        ("click chemistry bioconjugation", "strain-promoted azide-alkyne cycloaddition"),
        ("solid-state NMR of amorphous materials", "dynamic nuclear polarization"),
        ("radical polymerization control", "ATRP vs RAFT mechanisms"),
        ("lanthanide luminescence", "antenna effect and quantum yield"),
        ("heterogeneous catalysis microkinetic modeling", "DFT-derived rate constants"),
        ("mass spectrometry-based proteomics", "data-independent acquisition"),
        ("battery electrolyte decomposition", "SEI formation mechanism"),
        ("flow chemistry for API synthesis", "residence time distribution"),
        ("molecular dynamics of ionic liquids", "transport property prediction"),
        ("covalent organic framework design", "reticular chemistry principles"),
        ("photoswitchable molecules", "azobenzene vs diarylethene"),
        ("electrochemiluminescence sensors", "co-reactant pathway"),
        ("enzyme engineering by directed evolution", "screening assay design"),
        ("perovskite quantum dots", "halide exchange and stability"),
        ("computational reaction mechanism elucidation", "nudged elastic band"),
    ],
    "systems": [
        "automated high-throughput catalyst screening platform",
        "artificial enzyme for selective C-H functionalization",
        "molecular machine for controlled drug release",
        "electrochemical sensor array for environmental monitoring",
        "continuous flow system for nanoparticle synthesis with size control",
    ],
    "claims": [
        "AI-designed catalysts will outperform human-designed ones for most reactions within 5 years",
        "supramolecular chemistry will enable programmable matter",
        "flow chemistry will fully replace batch processing in pharmaceutical manufacturing",
    ],
}

DOMAINS["biology"] = {
    "count": 2500,
    "topics": [
        ("single-cell RNA sequencing analysis", "trajectory inference algorithms"),
        ("CRISPR base editing off-target effects", "genome-wide detection methods"),
        ("gut microbiome metabolomics", "short-chain fatty acid signaling"),
        ("phase separation in cell biology", "liquid-liquid phase separation"),
        ("long-read sequencing for structural variants", "ONT vs PacBio accuracy"),
        ("synthetic minimal genome", "essential gene determination"),
        ("epigenetic inheritance mechanisms", "transgenerational effects"),
        ("protein structure prediction", "AlphaFold limitations and extensions"),
        ("marine microbiome ecology", "deep-sea vent chemosynthesis"),
        ("insect decline causes", "neonicotinoid vs habitat loss evidence"),
        ("horizontal gene transfer in eukaryotes", "endosymbiotic gene transfer"),
        ("circadian clock molecular mechanisms", "transcription-translation feedback"),
        ("CAR-T therapy resistance mechanisms", "antigen escape and exhaustion"),
        ("mycorrhizal network nutrient sharing", "common mycorrhizal networks"),
        ("RNA interference therapeutic delivery", "GalNAc conjugation"),
        ("SARS-CoV-2 evolution and immune evasion", "convergent mutations"),
        ("optogenetics in behavioral neuroscience", "fiber photometry"),
        ("ancient DNA analysis", "damage patterns and authentication"),
        ("telomere biology and aging", "shelterin complex regulation"),
        ("quorum sensing in biofilms", "autoinducer signaling"),
        ("metamorphosis hormonal control", "ecdysone and juvenile hormone"),
        ("bioluminescence mechanisms", "luciferin diversity across taxa"),
        ("coral reef bleaching recovery", "symbiont shuffling"),
        ("parasitic manipulation of host behavior", "Toxoplasma and rodent behavior"),
        ("plant immune system", "pattern-triggered vs effector-triggered immunity"),
    ],
    "systems": [
        "high-throughput CRISPR screening pipeline for drug targets",
        "environmental DNA monitoring system for biodiversity assessment",
        "organoid-based disease model for personalized medicine",
        "synthetic gene circuit for metabolic engineering",
        "automated insect identification system using computer vision",
    ],
    "claims": [
        "de-extinction of the woolly mammoth is scientifically and ethically justified",
        "the human microbiome is more predictive of health outcomes than the genome",
        "gain-of-function research on pathogens should be permanently halted",
    ],
}

DOMAINS["astronomy"] = {
    "count": 2000,
    "topics": [
        ("exoplanet atmospheric characterization", "JWST transmission spectroscopy"),
        ("fast radio burst progenitor models", "magnetar vs cosmic string"),
        ("dark energy equation of state", "DESI BAO measurements"),
        ("stellar nucleosynthesis r-process", "kilonova observations"),
        ("supermassive black hole formation", "direct collapse vs seed models"),
        ("galaxy cluster mass estimation", "Sunyaev-Zeldovich effect"),
        ("cosmic reionization timeline", "21cm tomography"),
        ("gravitational lensing mass mapping", "weak lensing systematics"),
        ("pulsar timing array gravitational waves", "nanohertz GW background"),
        ("protoplanetary disk substructure", "ALMA gap morphology"),
        ("Type Ia supernova standardization", "Phillips relation scatter"),
        ("Hubble tension", "H0 measurement discrepancy"),
        ("magnetar giant flares", "QED effects in ultrastrong fields"),
        ("active galactic nuclei unification model", "torus geometry constraints"),
        ("primordial gravitational waves", "tensor-to-scalar ratio constraints"),
        ("stellar stream dynamics", "dark matter subhalo perturbation"),
        ("interstellar medium phases", "warm-hot intergalactic medium"),
        ("technosignature search strategies", "radio vs optical vs infrared"),
        ("solar wind acceleration", "Parker Solar Probe findings"),
        ("galaxy morphological classification", "CNN vs human classification"),
    ],
    "systems": [
        "next-generation extremely large telescope adaptive optics system",
        "space-based gravitational wave detector (LISA-like)",
        "automated transient classification pipeline for LSST",
        "cubesat constellation for radio astronomy aperture synthesis",
        "coronagraph for direct exoplanet imaging",
    ],
    "claims": [
        "the Hubble tension requires new physics beyond ΛCDM",
        "biosignatures have been tentatively detected in exoplanet atmospheres",
        "dark matter will be directly detected within the next decade",
    ],
}

DOMAINS["earth_environmental"] = {
    "count": 2000,
    "topics": [
        ("mantle convection models", "slab stagnation in transition zone"),
        ("ice sheet dynamics", "marine ice cliff instability hypothesis"),
        ("earthquake early warning", "P-wave magnitude estimation"),
        ("ocean acidification biological impact", "calcifier response"),
        ("atmospheric river prediction", "integrated water vapor transport"),
        ("volcanic eruption forecasting", "InSAR deformation monitoring"),
        ("permafrost thaw feedback", "thermokarst and methane release"),
        ("paleoclimate proxy calibration", "speleothem δ18O interpretation"),
        ("mineral resource estimation", "kriging vs machine learning"),
        ("groundwater contamination modeling", "reactive transport"),
        ("carbon cycle feedback", "land sink saturation evidence"),
        ("tsunami numerical modeling", "Boussinesq vs shallow water equations"),
        ("urban heat island mitigation", "cool roof albedo effects"),
        ("sediment transport modeling", "bedload vs suspended load"),
        ("Milankovitch cycle forcing", "100kyr problem"),
        ("geothermal energy extraction", "enhanced geothermal systems"),
        ("soil carbon sequestration", "biochar stability"),
        ("remote sensing land use classification", "SAR vs optical fusion"),
        ("wildfire behavior prediction", "coupled fire-atmosphere models"),
        ("deep ocean circulation changes", "AMOC weakening evidence"),
    ],
    "systems": [
        "real-time seismic monitoring network for induced seismicity",
        "precision agriculture soil moisture monitoring system",
        "coastal erosion prediction and early warning platform",
        "carbon capture, utilization, and storage monitoring system",
        "autonomous underwater glider for ocean observation",
    ],
    "claims": [
        "tipping cascades in the Earth system could trigger irreversible climate change within a decade",
        "large-scale geoengineering (solar radiation management) is necessary to meet Paris Agreement targets",
        "deep-sea mining can be conducted without significant ecological damage",
    ],
}

# ── COMPUTER SCIENCE & TECHNOLOGY ─────────────────────────────────────────

DOMAINS["computer_science"] = {
    "count": 3000,
    "topics": [
        ("consensus algorithms", "Raft vs PBFT vs HotStuff"),
        ("zero-knowledge proof systems", "zk-SNARKs vs zk-STARKs"),
        ("persistent data structures", "log-structured merge trees"),
        ("formal verification of distributed protocols", "TLA+ model checking"),
        ("cache-oblivious algorithms", "van Emde Boas layout"),
        ("homomorphic encryption performance", "CKKS vs BFV scheme"),
        ("quantum error correction codes", "surface code vs color code"),
        ("memory safety in systems programming", "ownership vs garbage collection"),
        ("CRDT conflict resolution", "state-based vs operation-based"),
        ("database query optimization", "adaptive query processing"),
        ("network congestion control", "BBR vs CUBIC vs QUIC"),
        ("side-channel attack mitigation", "speculative execution defenses"),
        ("type theory for programming languages", "dependent types"),
        ("operating system scheduling", "CFS vs EEVDF"),
        ("distributed transaction protocols", "Spanner TrueTime vs Calvin"),
        ("binary analysis and decompilation", "lifting to IR challenges"),
        ("WebAssembly for edge computing", "component model"),
        ("graph database query languages", "Cypher vs GQL vs SPARQL"),
        ("software-defined networking", "P4 programmable data planes"),
        ("container runtime security", "gVisor vs Kata Containers"),
        ("differential privacy mechanisms", "Gaussian vs Laplace noise"),
        ("compiler auto-vectorization", "polyhedral model"),
        ("real-time operating systems", "priority inversion prevention"),
        ("post-quantum cryptography", "lattice-based vs hash-based schemes"),
        ("serverless cold start optimization", "snapshot restore techniques"),
    ],
    "systems": [
        "globally distributed database with sub-millisecond reads",
        "privacy-preserving federated analytics platform",
        "real-time stream processing engine with exactly-once semantics",
        "secure multi-party computation framework for healthcare data",
        "automated vulnerability detection pipeline using symbolic execution",
    ],
    "claims": [
        "formal verification will be required for all safety-critical software within 10 years",
        "quantum computers will break RSA-2048 within 15 years",
        "RISC-V will displace ARM in embedded systems market by 2035",
    ],
}

DOMAINS["ai_ml"] = {
    "count": 3000,
    "topics": [
        ("transformer attention mechanism alternatives", "linear attention and state space models"),
        ("RLHF reward hacking", "specification gaming in language models"),
        ("mixture-of-experts scaling laws", "routing collapse and load balancing"),
        ("test-time compute scaling", "chain-of-thought and search"),
        ("sparse autoencoders for mechanistic interpretability", "feature discovery"),
        ("continual learning without catastrophic forgetting", "elastic weight consolidation"),
        ("diffusion model theory", "score matching and DDPM connection"),
        ("graph neural network expressiveness", "Weisfeiler-Leman hierarchy"),
        ("federated learning heterogeneity", "non-IID data strategies"),
        ("neural architecture search efficiency", "one-shot NAS"),
        ("large language model hallucination detection", "factuality classifiers"),
        ("vision transformer efficiency", "token pruning and merging"),
        ("reinforcement learning from human feedback", "DPO vs PPO comparison"),
        ("knowledge distillation for model compression", "logit vs feature matching"),
        ("adversarial robustness certification", "randomized smoothing"),
        ("in-context learning theory", "induction heads and task vectors"),
        ("multimodal alignment", "CLIP contrastive learning theory"),
        ("world models for planning", "JEPA vs generative approaches"),
        ("data curation for pre-training", "data mixing laws"),
        ("model merging techniques", "TIES-Merging and DARE"),
        ("AI safety and alignment", "constitutional AI and RLAIF"),
        ("quantization-aware training", "SqueezeLLM and GPTQ methods"),
        ("retrieval-augmented generation", "chunk retrieval strategies"),
        ("neurosymbolic AI", "differentiable programming"),
        ("multi-agent LLM systems", "debate and consensus protocols"),
    ],
    "systems": [
        "automated ML pipeline with neural architecture search",
        "real-time deepfake detection system",
        "federated learning platform for hospital networks",
        "LLM-based code review agent with formal verification",
        "multimodal RAG system with visual and textual retrieval",
    ],
    "claims": [
        "scaling laws will continue to hold for another 3 orders of magnitude",
        "current LLMs understand language rather than merely pattern-matching",
        "artificial general intelligence will be achieved through scaling alone",
    ],
}

# ── MATHEMATICS & REASONING ───────────────────────────────────────────────

DOMAINS["math"] = {
    "count": 3000,
    "topics": [
        ("Riemann hypothesis implications", "prime number distribution"),
        ("category theory in programming", "monads and functors"),
        ("optimal transport theory", "Wasserstein distance applications"),
        ("random matrix theory", "Marchenko-Pastur distribution"),
        ("algebraic topology homology groups", "persistent homology in data"),
        ("stochastic differential equations", "Itô vs Stratonovich calculus"),
        ("combinatorial optimization", "semidefinite programming relaxations"),
        ("number theory elliptic curves", "BSD conjecture status"),
        ("information geometry", "Fisher information metric"),
        ("spectral graph theory", "Cheeger inequality and graph partitioning"),
        ("Bayesian nonparametrics", "Dirichlet process mixture models"),
        ("differential geometry Ricci flow", "Perelman's proof of Poincaré"),
        ("ergodic theory and dynamical systems", "Lyapunov exponents"),
        ("algebraic geometry scheme theory", "Grothendieck's contributions"),
        ("mathematical logic and incompleteness", "Gödel's theorems implications"),
        ("harmonic analysis on groups", "representation theory"),
        ("convex optimization duality", "strong duality conditions"),
        ("topological data analysis", "mapper algorithm"),
        ("martingale theory in finance", "fundamental theorem of asset pricing"),
        ("compressed sensing theory", "restricted isometry property"),
        ("geometric measure theory", "minimal surfaces and regularity"),
        ("operads and higher algebra", "homotopy type theory"),
        ("extremal graph theory", "Szemerédi regularity lemma"),
        ("Fourier analysis on finite fields", "additive combinatorics"),
        ("mathematical physics gauge theory", "Yang-Mills mass gap problem"),
    ],
    "systems": [
        "automated theorem proving system for undergraduate math",
        "statistical inference engine for high-dimensional data",
        "cryptographic protocol based on lattice problems",
        "optimization solver for mixed-integer programming",
        "mathematical visualization tool for 4D manifolds",
    ],
    "claims": [
        "machine learning will solve open conjectures in number theory before humans",
        "quantum computing will provide exponential speedup for all NP-hard problems",
        "the P vs NP problem will be resolved within 20 years",
    ],
}

DOMAINS["reasoning_strategy"] = {
    "count": 2500,
    "topics": [
        ("game theory mechanism design", "auction theory and VCG mechanism"),
        ("decision theory under deep uncertainty", "robust decision making"),
        ("intelligence analysis tradecraft", "structured analytic techniques"),
        ("military strategy deterrence theory", "nuclear escalation ladder"),
        ("systems thinking leverage points", "Meadows 12 intervention points"),
        ("formal logic modal systems", "Kripke semantics for epistemic logic"),
        ("bayesian epistemology", "Dutch book arguments"),
        ("geopolitical scenario planning", "STEEP analysis framework"),
        ("negotiation theory", "BATNA and zone of possible agreement"),
        ("cognitive bias mitigation", "premortem and red teaming"),
        ("complex adaptive systems", "emergence and self-organization"),
        ("philosophy of science demarcation", "Popper vs Kuhn vs Lakatos"),
        ("strategic foresight methods", "Delphi technique and cross-impact"),
        ("argumentation theory", "Toulmin model of argument"),
        ("risk analysis frameworks", "bow-tie analysis and fault trees"),
        ("evolutionary game theory", "replicator dynamics"),
        ("multi-criteria decision analysis", "AHP vs ELECTRE"),
        ("counterfactual reasoning", "Lewis possible worlds semantics"),
        ("network analysis for intelligence", "centrality measures"),
        ("competitive intelligence methods", "OODA loop and Boyd cycle"),
        ("policy analysis cost-benefit", "discount rate selection"),
        ("wargaming methodology", "matrix games and seminar games"),
        ("abductive reasoning", "inference to best explanation"),
        ("organizational learning", "double-loop learning theory"),
        ("persuasion and influence", "Cialdini's principles in practice"),
    ],
    "systems": [
        "AI-assisted intelligence analysis workbench",
        "strategic planning tool with scenario simulation",
        "automated logical argument evaluator",
        "multi-stakeholder negotiation support system",
        "horizon scanning platform for emerging threats",
    ],
    "claims": [
        "AI systems can make better strategic decisions than human experts in most domains",
        "prediction markets are more accurate than expert panels for geopolitical forecasting",
        "formal logic-based reasoning is superior to heuristic-based reasoning for all decision types",
    ],
}

# ── MEDICINE & PSYCHOLOGY ─────────────────────────────────────────────────

DOMAINS["medicine"] = {
    "count": 2500,
    "topics": [
        ("checkpoint inhibitor resistance mechanisms", "tumor microenvironment"),
        ("GLP-1 receptor agonist cardiovascular effects", "SUSTAIN-6 and LEADER trials"),
        ("antibiotic resistance plasmid transfer", "conjugation and phage-mediated"),
        ("mRNA vaccine platform adaptability", "updated antigen design"),
        ("liquid biopsy ctDNA analysis", "minimal residual disease detection"),
        ("gut-brain axis in neurodegeneration", "microbiome metabolite signaling"),
        ("gene therapy for sickle cell disease", "BCL11A silencing approach"),
        ("sepsis immunopathology", "immunoparalysis and cytokine storm"),
        ("SGLT2 inhibitor renal protection", "tubuloglomerular feedback"),
        ("CAR-T neurotoxicity management", "ICANS grading and treatment"),
        ("AI in radiology diagnostic accuracy", "FDA-cleared algorithms"),
        ("xenotransplantation progress", "PERV inactivation and immune barriers"),
        ("precision oncology biomarker panels", "NGS-based companion diagnostics"),
        ("long COVID pathophysiology", "viral persistence vs autoimmunity"),
        ("CRISPR therapeutics regulatory pathway", "off-target risk assessment"),
        ("ketamine for treatment-resistant depression", "NMDA and AMPA mechanisms"),
        ("robotic surgery outcomes", "da Vinci system evidence base"),
        ("antimicrobial stewardship", "de-escalation strategies"),
        ("fetal surgery for myelomeningocele", "MOMS trial outcomes"),
        ("pharmacogenomics CYP2D6", "drug dose adjustment algorithms"),
        ("point-of-care ultrasound", "FAST exam and lung ultrasound"),
        ("palliative care integration", "early vs late referral outcomes"),
        ("organ preservation", "machine perfusion vs cold storage"),
        ("telemedicine diagnostic accuracy", "dermatology and ophthalmology"),
        ("pandemic preparedness", "stockpile and surge capacity planning"),
    ],
    "systems": [
        "AI-powered clinical decision support for emergency medicine",
        "closed-loop insulin delivery system (artificial pancreas)",
        "population health surveillance platform for outbreak detection",
        "genomics-guided cancer treatment selection pipeline",
        "remote patient monitoring system for chronic heart failure",
    ],
    "claims": [
        "AI will outperform radiologists in all imaging modalities within 5 years",
        "CRISPR germline editing should be permitted for serious genetic diseases",
        "universal mRNA vaccine platforms will eliminate pandemic risk",
    ],
}

DOMAINS["psychology"] = {
    "count": 2500,
    "topics": [
        ("replication crisis in social psychology", "many labs and open science"),
        ("predictive processing framework", "active inference and free energy"),
        ("ACT vs CBT for anxiety disorders", "transdiagnostic approaches"),
        ("implicit bias measurement validity", "IAT reliability concerns"),
        ("childhood adversity biological embedding", "epigenetic mechanisms"),
        ("language acquisition critical period", "Newport's less-is-more hypothesis"),
        ("working memory capacity limits", "Cowan's embedded processes"),
        ("moral psychology trolley problems", "dual-process theory"),
        ("psychedelic-assisted therapy", "psilocybin mechanism of action"),
        ("computational psychiatry", "reinforcement learning models of addiction"),
        ("attachment theory adult relationships", "internal working models"),
        ("sleep and memory consolidation", "active systems consolidation theory"),
        ("cognitive load theory in education", "element interactivity"),
        ("behavioral economics nudge theory", "libertarian paternalism critique"),
        ("placebo response mechanisms", "neurobiological substrates"),
        ("developmental psychology theory of mind", "false belief tasks"),
        ("forensic psychology eyewitness reliability", "misinformation effect"),
        ("neuropsychological assessment", "ecological validity concerns"),
        ("health behavior change models", "COM-B and behavior change wheel"),
        ("emotion regulation strategies", "cognitive reappraisal vs suppression"),
        ("cross-cultural psychology", "WEIRD samples generalizability"),
        ("psychometrics item response theory", "2PL vs 3PL models"),
        ("social identity theory", "minimal group paradigm"),
        ("trauma-focused therapy comparison", "EMDR vs CPT vs PE"),
        ("motivational interviewing mechanisms", "change talk and sustain talk"),
    ],
    "systems": [
        "digital phenotyping platform for mental health monitoring",
        "AI chatbot for cognitive behavioral therapy delivery",
        "adaptive testing system using computerized adaptive testing",
        "VR exposure therapy system for PTSD",
        "ecological momentary assessment app for mood tracking",
    ],
    "claims": [
        "AI therapy bots will be as effective as human therapists for mild-moderate depression",
        "the replication crisis invalidates most of social psychology's foundational findings",
        "psychedelic therapy will become first-line treatment for depression within a decade",
    ],
}

# ── HUMANITIES ────────────────────────────────────────────────────────────

DOMAINS["history"] = {
    "count": 2500,
    "topics": [
        ("Bronze Age collapse causes", "systems collapse theory"),
        ("Roman economic decline", "Wickham vs Ward-Perkins debate"),
        ("Islamic Golden Age knowledge transfer", "House of Wisdom"),
        ("Columbian Exchange ecological impact", "demographic catastrophe"),
        ("French Revolution causation", "revisionist vs Marxist historiography"),
        ("Atlantic slave trade quantification", "Trans-Atlantic Slave Trade Database"),
        ("Industrial Revolution origins", "Allen's high-wage economy thesis"),
        ("Cold War proxy conflicts", "Korean War to Angola"),
        ("Mongol Empire administration", "Pax Mongolica trade networks"),
        ("Renaissance humanism", "Petrarch to Pico della Mirandola"),
        ("WWI origins", "Fischer thesis vs inadvertent escalation"),
        ("Meiji Restoration modernization", "institutional vs cultural factors"),
        ("Great Depression monetary policy", "Friedman-Schwartz vs Keynesian"),
        ("decolonization in Africa", "neocolonialism thesis"),
        ("Scientific Revolution paradigm", "continuity vs revolution debate"),
        ("Byzantine Empire resilience", "theme system and Greek fire"),
        ("American Civil War causation", "slavery centrality debate"),
        ("Ming Dynasty maritime retreat", "Zheng He to Haijin policy"),
        ("Holocaust historiography", "intentionalist vs functionalist"),
        ("Vietnamese resistance history", "Trung Sisters to Dien Bien Phu"),
        ("Inca imperial administration", "quipu and mit'a system"),
        ("British Empire legacy", "Niall Ferguson vs Shashi Tharoor"),
        ("Medieval climate anomaly", "Little Ice Age social impacts"),
        ("Space Race geopolitical analysis", "Sputnik to Apollo"),
        ("Haitian Revolution significance", "first successful slave revolt"),
    ],
    "systems": [
        "digital humanities text mining pipeline for historical documents",
        "archaeological site geospatial analysis platform",
        "prosopographic database for medieval social networks",
        "oral history preservation and analysis system",
        "historical climate reconstruction from proxy data",
    ],
    "claims": [
        "great man theory of history has been definitively disproven by quantitative approaches",
        "counterfactual history is a legitimate analytical tool rather than mere speculation",
        "digital humanities will transform historical methodology more than any previous innovation",
    ],
}

DOMAINS["language_arts"] = {
    "count": 2000,
    "topics": [
        ("unreliable narrator technique", "Nabokov's Lolita and Ishiguro"),
        ("postcolonial literary theory", "Spivak's subaltern studies"),
        ("Chomsky's universal grammar", "evidence for and against"),
        ("magical realism as genre", "García Márquez to Rushdie"),
        ("computational stylometry", "authorship attribution methods"),
        ("creole language genesis", "substrate vs superstrate debate"),
        ("modernist stream of consciousness", "Woolf vs Joyce technique"),
        ("translation theory equivalence", "Nida dynamic vs formal"),
        ("oral literature preservation", "Parry-Lord oral formulaic theory"),
        ("semiotics sign systems", "Peirce vs Saussure"),
        ("dystopian fiction political commentary", "Orwell to Atwood"),
        ("corpus linguistics methodology", "collocations and concordance"),
        ("literary criticism schools", "New Criticism vs deconstruction"),
        ("pidgin and creole continuum", "decreolization hypothesis"),
        ("rhetoric and persuasion", "Aristotle's ethos pathos logos"),
        ("second language acquisition theory", "Krashen's input hypothesis"),
        ("world literature canonicity", "Damrosch's world literature theory"),
        ("phonological typology", "consonant and vowel systems"),
        ("narrative structure analysis", "Propp's morphology vs Campbell"),
        ("endangered language documentation", "immersion vs archive approaches"),
    ],
    "systems": [
        "NLP-powered literary style analysis tool",
        "interactive creative writing assistant with genre awareness",
        "language documentation toolkit with annotation pipeline",
        "automated essay evaluation with rhetorical structure analysis",
        "multilingual parallel corpus alignment system",
    ],
    "claims": [
        "large language models can produce literature of genuine artistic merit",
        "universal grammar is unnecessary to explain language acquisition",
        "the literary canon should be abolished in favor of diverse reading lists",
    ],
}

# ── CREATIVE ARTS ─────────────────────────────────────────────────────────

DOMAINS["film"] = {
    "count": 2500,
    "topics": [
        ("long take cinematography", "Lubezki's Birdman vs 1917 techniques"),
        ("color grading and color science", "ACES workflow and LUT design"),
        ("sound design for horror", "infrasound and psychoacoustic effects"),
        ("screenwriting three-act structure", "McKee vs Snyder vs Truby"),
        ("virtual production LED volume", "Mandalorian stagecraft workflow"),
        ("documentary ethics", "observational vs participatory modes"),
        ("film score leitmotif technique", "Williams vs Zimmer approaches"),
        ("VFX compositing pipeline", "deep compositing and EXR workflow"),
        ("editing rhythm and pacing", "Murch's rule of six"),
        ("production design world-building", "practical vs digital environments"),
        ("acting methodology", "Stanislavski vs Meisner vs Chekhov"),
        ("film theory apparatus theory", "Baudry and the ideological effect"),
        ("motion capture performance", "uncanny valley and digital humans"),
        ("independent film financing", "gap financing and tax credits"),
        ("genre theory and hybridity", "Altman's semantic/syntactic approach"),
        ("aspect ratio storytelling", "Wes Anderson vs Villeneuve choices"),
        ("stereoscopic 3D cinematography", "convergence and interaxial"),
        ("post-production color pipeline", "dailies to final DI"),
        ("distribution paradigm shift", "streaming vs theatrical economics"),
        ("animation principles", "Disney's 12 principles in 3D"),
        ("mise-en-scène analysis", "deep focus vs shallow focus meaning"),
        ("narrative unreliability in film", "Fight Club to Shutter Island"),
        ("feminist film theory", "Mulvey's male gaze and beyond"),
        ("Foley artistry and ADR", "sync sound vs replacement"),
        ("location scouting methodology", "visual storytelling through place"),
    ],
    "systems": [
        "AI-assisted pre-visualization pipeline",
        "real-time virtual production environment",
        "automated subtitle and captioning system with emotion tags",
        "film restoration and upscaling pipeline using AI",
        "interactive nonlinear storytelling platform",
    ],
    "claims": [
        "AI-generated films will win a major festival award within 5 years",
        "virtual production will entirely replace on-location shooting for most genres",
        "streaming has permanently degraded the cinematic experience",
    ],
}

DOMAINS["music"] = {
    "count": 2500,
    "topics": [
        ("spectral composition techniques", "Grisey and Murail"),
        ("psychoacoustics masking", "critical bands and loudness perception"),
        ("algorithmic composition", "Markov chains to neural networks"),
        ("microtonality and alternative tuning", "just intonation vs equal temperament"),
        ("spatial audio and ambisonics", "HOA encoding and binaural rendering"),
        ("music production compression techniques", "parallel vs multiband"),
        ("ethnomusicology fieldwork methods", "participant observation ethics"),
        ("music information retrieval", "beat tracking and chord recognition"),
        ("orchestration timbral combination", "Rimsky-Korsakov to Adler"),
        ("synthesizer filter design", "Moog ladder vs Sallen-Key"),
        ("film scoring to picture", "hit points and tempo mapping"),
        ("music theory Neo-Riemannian", "tonnetz and voice leading"),
        ("acoustic guitar construction", "top bracing and tonewoods"),
        ("adaptive game audio", "horizontal re-sequencing and vertical layering"),
        ("mixing low-end management", "sub bass and kick drum interaction"),
        ("counterpoint species method", "Fux Gradus to modern application"),
        ("studio acoustics design", "reflection free zone and RT60"),
        ("sampling law and ethics", "copyright and creative commons"),
        ("music therapy evidence base", "neurological music therapy"),
        ("analog vs digital recording", "tape saturation characteristics"),
        ("DJ mixing harmonic theory", "Camelot wheel and energy management"),
        ("choral arranging voice leading", "SATB part writing rules"),
        ("sound synthesis techniques", "FM vs granular vs physical modeling"),
        ("music copyright analysis", "substantial similarity standard"),
        ("concert hall acoustics", "Sabine equation and diffusion"),
    ],
    "systems": [
        "AI-assisted music composition and arrangement tool",
        "real-time pitch correction system with natural formant preservation",
        "immersive spatial audio mixing environment",
        "automatic music transcription pipeline",
        "adaptive soundtrack engine for interactive media",
    ],
    "claims": [
        "AI-composed music is indistinguishable from human-composed in blind tests",
        "lossy audio compression (streaming) has permanently altered listener expectations",
        "music theory is culturally biased and should be rebuilt from non-Western foundations",
    ],
}

DOMAINS["game_design"] = {
    "count": 2500,
    "topics": [
        ("procedural generation algorithms", "wave function collapse and L-systems"),
        ("player psychology flow state", "Csikszentmihalyi's flow in games"),
        ("narrative design branching dialogue", "Inkle's ink vs Yarn Spinner"),
        ("game balance mathematical modeling", "Machinations framework"),
        ("UX research playtesting methodology", "think-aloud protocol"),
        ("level design pacing", "intensity curves and gating"),
        ("monetization ethics", "loot box psychology and regulation"),
        ("game AI behavior trees", "utility AI vs GOAP"),
        ("physics engine architecture", "constraint-based vs impulse-based"),
        ("multiplayer netcode", "rollback vs delay-based and GGPO"),
        ("accessibility in game design", "WCAG adaptation for games"),
        ("emergent gameplay systems", "Dwarf Fortress and Breath of the Wild"),
        ("game feel and juice", "Swink's taxonomy of game feel"),
        ("economy design sink-faucet", "virtual economy inflation control"),
        ("color theory in UI design", "colorblind accessibility"),
        ("sound design for feedback", "audio-haptic synchronization"),
        ("quest design typology", "Desilets' quest classification"),
        ("open world design", "player freedom vs narrative cohesion"),
        ("combat system design", "action game frame data and cancels"),
        ("social deduction mechanics", "information asymmetry design"),
        ("roguelike design principles", "Berlin interpretation"),
        ("game engine ECS architecture", "data-oriented design principles"),
        ("puzzle design difficulty curves", "progressive disclosure"),
        ("live service game design", "battle pass and seasonal content"),
        ("VR interaction design", "locomotion and simulator sickness"),
    ],
    "systems": [
        "procedural world generator with biome coherence",
        "AI director system for dynamic difficulty adjustment",
        "multiplayer matchmaking system with skill-based rating",
        "game analytics dashboard for player behavior analysis",
        "modular dialogue system with character personality modeling",
    ],
    "claims": [
        "AI NPCs with LLM-driven dialogue will replace scripted NPCs entirely",
        "procedural generation produces content that is indistinguishable from hand-crafted",
        "free-to-play monetization is inherently exploitative and should be regulated",
    ],
}


# ---------------------------------------------------------------------------
# Prompt generation engine
# ---------------------------------------------------------------------------

def generate_domain_prompts(domain_name: str, config: dict) -> list[dict]:
    """Generate research-level prompts for a single domain."""
    prompts: list[dict] = []
    target = config["count"]
    topics = config["topics"]
    systems = config.get("systems", [])
    claims = config.get("claims", [])

    idx = 0

    # Template-based generation from topic pairs
    template_keys = list(TEMPLATES.keys())
    for topic_detail, framework in topics:
        for tpl_name in template_keys:
            if idx >= target:
                break
            tpl = TEMPLATES[tpl_name]
            # Map template variables
            prompt_text = tpl.format(
                topic=topic_detail,
                framework=framework,
                approach_a=topic_detail.split(" ")[0] if " " in topic_detail else topic_detail,
                approach_b=framework,
                application=f"{domain_name} applications",
                equation_or_relationship=topic_detail,
                system=topic_detail,
                claim=f"{topic_detail} significantly improves upon {framework}",
                symptom=f"unexpected behavior related to {framework}",
                field=domain_name.replace("_", " "),
                concept_a=topic_detail,
                field_a=domain_name.replace("_", " "),
                concept_b=framework,
                field_b="applied mathematics",
                parameters=f"typical {domain_name.replace('_', ' ')} parameters for {topic_detail}",
                target=f"key performance metrics for {framework}",
                technology_or_practice=topic_detail,
                hypothesis=f"{topic_detail} is governed by {framework}",
                case=f"a well-known failure involving {topic_detail}",
                technology=topic_detail,
            )
            idx += 1
            prompts.append({
                "id": f"{domain_name}_{idx:05d}",
                "domain": domain_name,
                "prompt": prompt_text,
            })
        if idx >= target:
            break

    # System design prompts
    for system_desc in systems:
        if idx >= target:
            break
        idx += 1
        prompts.append({
            "id": f"{domain_name}_{idx:05d}",
            "domain": domain_name,
            "prompt": f"Design a {system_desc}. Provide a detailed architecture including key components, their interactions, performance requirements, failure modes, and validation strategy. Discuss trade-offs between competing design choices.",
        })

    # Critique prompts
    for claim in claims:
        if idx >= target:
            break
        idx += 1
        prompts.append({
            "id": f"{domain_name}_{idx:05d}",
            "domain": domain_name,
            "prompt": f"A researcher claims: \"{claim}\" Critically evaluate this claim from a postdoctoral research perspective. What is the supporting evidence? What are the counterarguments? What experiments or analyses would resolve the debate?",
        })

    # If we still need more, generate cross-topic synthesis prompts
    while idx < target:
        t1_detail, t1_fw = random.choice(topics)
        t2_detail, t2_fw = random.choice(topics)
        if t1_detail == t2_detail:
            continue
        idx += 1
        prompts.append({
            "id": f"{domain_name}_{idx:05d}",
            "domain": domain_name,
            "prompt": f"Explore the intersection of {t1_detail} and {t2_detail} in {domain_name.replace('_', ' ')}. How do insights from {t1_fw} inform our understanding of {t2_fw}? What are the open research questions at this intersection, and what experimental or theoretical approaches would address them?",
        })

    return prompts[:target]


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    random.seed(42)

    total = 0
    for domain_name, config in DOMAINS.items():
        prompts = generate_domain_prompts(domain_name, config)
        output_path = OUTPUT_DIR / f"prompts_{domain_name}.jsonl"
        with open(output_path, "w") as f:
            for p in prompts:
                f.write(json.dumps(p) + "\n")
        print(f"  {domain_name:25s} → {len(prompts):,} prompts → {output_path.name}")
        total += len(prompts)

    print(f"\n  TOTAL: {total:,} prompts across {len(DOMAINS)} domains")
    print(f"  Output: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
