"""LoRA routing for Atlas CLI.

Classifies tasks by domain and resolves the best Ollama model to use.
When composed LoRA models are available (via :class:`LoRAManager`), the
router returns the domain-specific model name; otherwise it falls back
to the base model.
"""

# Module ownership: LoRA domain routing
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)


class LoRARouter:
    """Classify tasks by domain and resolve composed LoRA models.

    When a :class:`~cortex.evolution.lora_manager.LoRAManager` is
    available the router returns the composed model name for the
    classified domain.  Without a manager it still returns the domain
    string (backward-compatible).
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
        """Classify *task* and return the best model name.

        If a :class:`LoRAManager` is active and has a composed model
        for the classified domain, the composed model name is returned.
        Otherwise returns the domain string for backward compatibility.
        """
        domain = self.classify(task)
        if domain == "general":
            return domain

        from cortex.evolution.lora_manager import get_lora_manager

        mgr = get_lora_manager()
        if mgr:
            model = mgr.get_model_for_domain(domain)
            if model:
                log.info("Routed to LoRA model %s (domain=%s)", model, domain)
                return model
        return domain

    def resolve_model(self, message: str, base_model: str) -> str:
        """Classify *message* and return either a LoRA model or *base_model*.

        Designed for inline use in the pipeline where a concrete model
        name is always needed.
        """
        domain = self.classify(message)
        if domain == "general":
            return base_model

        from cortex.evolution.lora_manager import get_lora_manager

        mgr = get_lora_manager()
        if mgr:
            model = mgr.get_model_for_domain(domain)
            if model:
                log.debug("LoRA override: %s -> %s", base_model, model)
                return model
        return base_model

    @property
    def available_loras(self) -> list[str]:
        """List available composed LoRA model names."""
        from cortex.evolution.lora_manager import get_lora_manager

        mgr = get_lora_manager()
        if mgr:
            return [cm.model_name for cm in mgr.list_active()]
        return []
