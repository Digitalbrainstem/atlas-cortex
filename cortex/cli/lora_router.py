"""LoRA routing stub for Atlas CLI.

Classifies tasks by domain for future expert LoRA hot-swapping.
Currently a stub — returns the domain classification but does not
actually swap adapters.
"""

# Module ownership: LoRA domain routing (stub)
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)


class LoRARouter:
    """Classify tasks by domain for future LoRA hot-swapping.

    Currently a stub — returns the domain classification but doesn't
    actually swap LoRAs.  The infrastructure is ready for when expert
    LoRA adapters are trained and available.
    """

    DOMAINS: dict[str, list[re.Pattern[str]]] = {
        "coding": [
            re.compile(r"\b(code|program|function|class|debug|refactor|implement)\b", re.I),
            re.compile(r"\b(python|javascript|typescript|rust|go|java|c\+\+)\b", re.I),
            re.compile(r"\b(bug|fix|compile|syntax|import|module|package)\b", re.I),
            re.compile(r"\b(git|commit|merge|branch|PR|pull request)\b", re.I),
        ],
        "reasoning": [
            re.compile(r"\b(reason|logic|plan|strategy|analyz|think|explain why)\b", re.I),
            re.compile(r"\b(pros?\s+and\s+cons?|trade-?off|compare|decision)\b", re.I),
            re.compile(r"\b(architecture|design|approach|evaluate)\b", re.I),
        ],
        "math": [
            re.compile(r"\b(math|calcul\w*|equation|formula|integr\w+|derivat\w*|algebra)\b", re.I),
            re.compile(r"\b(statistic\w*|probability|matrix|vector|linear)\b", re.I),
            re.compile(r"\b(sum|product|average|median|percent)\b", re.I),
        ],
        "sysadmin": [
            re.compile(r"\b(server|docker|container|kubernetes|k8s|deploy)\b", re.I),
            re.compile(r"\b(network|firewall|nginx|apache|systemd|ssh)\b", re.I),
            re.compile(r"\b(linux|ubuntu|debian|centos|apt|yum|dnf)\b", re.I),
            re.compile(r"\b(disk|mount|partition|filesystem|backup)\b", re.I),
        ],
    }

    def classify(self, task: str) -> str:
        """Classify a task into a domain. Returns the domain name.

        Falls back to ``"general"`` when no patterns match.
        """
        for domain, patterns in self.DOMAINS.items():
            for pat in patterns:
                if pat.search(task):
                    log.debug("task classified as %s", domain)
                    return domain
        return "general"

    async def route(self, task: str, provider: Any) -> str:
        """Classify *task* and (in future) swap the LoRA adapter.

        Currently returns just the domain string.
        """
        domain = self.classify(task)
        # Future: provider.set_lora(domain) once adapters exist
        return domain

    @property
    def available_loras(self) -> list[str]:
        """List available LoRA adapters (from filesystem or Ollama).

        Returns an empty list until adapters are trained and deployed.
        """
        return []  # stub — no adapters available yet
