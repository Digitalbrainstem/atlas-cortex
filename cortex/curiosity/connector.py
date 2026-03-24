"""Cross-domain knowledge connector — maps analogies between scientific domains.

Based on Derek Thomas's original Curiosity Engine design.  The secret weapon:
breakthroughs come from connecting transformers to fluid dynamics (eddy currents),
quantum mechanics (Bloch sphere), chaos theory (strange attractors), and NMR
(Larmor precession).  A systematic analogy library makes this reproducible.

Module ownership: Curiosity cross-domain analogies
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Analogy:
    """A cross-domain analogy suggestion."""

    source_domain: str
    target_domain: str
    archetype: str
    analogy: str
    prompt: str


# ── Archetype analogy graph ───────────────────────────────────────

ANALOGIES: dict[str, dict[str, str]] = {
    "bottleneck": {
        "transformers": "GQA compression, KV dimension reduction",
        "fluids": "Venturi tube constriction, pressure increase",
        "electronics": "transistor gate, impedance mismatch",
        "quantum": "measurement, wavefunction collapse",
        "biology": "enzyme active site, selective permeability",
        "chaos": "period-doubling bottleneck before chaos onset",
        "optics": "pinhole aperture, spatial filtering",
    },
    "feedback_loop": {
        "transformers": "residual connection, skip connection",
        "fluids": "eddy current, recirculation zone",
        "electronics": "op-amp feedback, oscillator circuit",
        "chaos": "iterated map, strange attractor",
        "biology": "homeostasis, predator-prey cycle",
        "quantum": "quantum error correction, syndrome measurement",
        "optics": "laser cavity round-trip, gain saturation",
    },
    "nonlinear_gate": {
        "transformers": "SiLU/GELU activation, attention softmax",
        "fluids": "turbulent transition (Reynolds number)",
        "electronics": "transistor saturation, diode forward bias",
        "chaos": "logistic map parameter r, bifurcation point",
        "quantum": "tunneling probability, barrier height",
        "biology": "action potential threshold, ion channel gating",
        "optics": "optical bistability, saturable absorber",
    },
    "resonance": {
        "transformers": "standing wave in layer stack, n=2 mode",
        "acoustics": "pipe harmonics, drum Bessel modes",
        "electronics": "LC circuit, antenna tuning",
        "quantum": "energy levels, Rabi oscillation",
        "optics": "Fabry-Perot cavity, laser mode selection",
        "biology": "circadian rhythm, neural oscillation",
        "chaos": "Arnold tongues, mode locking",
    },
    "decay": {
        "transformers": "information decay through layers, lambda=ln(2)/11",
        "nuclear": "radioactive half-life",
        "electronics": "RC circuit discharge",
        "optics": "Beer-Lambert absorption",
        "biology": "drug metabolism half-life",
        "chaos": "transient decay to attractor",
        "quantum": "decoherence time, T2 relaxation",
    },
}


class CrossDomainConnector:
    """Maps concepts between domains to generate novel hypotheses.

    Given a concept identified in one domain, automatically suggests
    parallel concepts in other domains via shared archetypes.

    Example from research::

        'gate layer' in transformers → 'fold line' in chaos theory
        'LayerNorm' in transformers → 'repeater' in fiber optics
        'GQA ratio' in transformers → 'Venturi constriction' in fluids
    """

    def suggest_analogies(
        self,
        concept: str,
        current_domain: str = "transformers",
    ) -> list[Analogy]:
        """Given a concept in one domain, suggest parallel concepts."""
        suggestions: list[Analogy] = []
        concept_lower = concept.lower()

        for archetype, domains in ANALOGIES.items():
            source_desc = domains.get(current_domain, "")
            if not source_desc:
                continue
            # Check if the concept matches the source domain description
            if concept_lower not in source_desc.lower():
                continue

            for domain, description in domains.items():
                if domain == current_domain:
                    continue
                suggestions.append(Analogy(
                    source_domain=current_domain,
                    target_domain=domain,
                    archetype=archetype,
                    analogy=description,
                    prompt=(
                        f"What if we view {concept} as {description}? "
                        f"(archetype: {archetype}, from {domain})"
                    ),
                ))

        return suggestions

    def get_archetypes(self) -> list[str]:
        """Return all known archetype names."""
        return list(ANALOGIES.keys())

    def get_domains(self, archetype: str) -> dict[str, str]:
        """Return all domain mappings for an archetype."""
        return dict(ANALOGIES.get(archetype, {}))
