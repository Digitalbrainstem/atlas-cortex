"""Agent Orchestrator — selects and configures the right agent per request.

Sits above Layer 3 and coordinates model selection (ModelRouter),
system prompts, thinking mode (ThinkingRouter), optional LoRA, and
tool access into a single resolved *AgentConfig* for every incoming
request.

Built-in agent templates: conversation, coding, research, home,
education, sight.  Custom templates can be registered at runtime via
:meth:`AgentOrchestrator.register_agent`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from cortex.pipeline.model_router import ModelRouter
from cortex.pipeline.thinking_router import ThinkingRouter

logger = logging.getLogger(__name__)

# ── keyword banks for agent classification ───────────────────────

_CODING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(write|generate|create)\s+(a\s+)?(code|function|class|script|program|module)\b", re.I),
    re.compile(r"\b(refactor|debug|fix|implement|optimize)\s+(this|the|my|a)?\s*\w*\s*(code|function|class|bug|error)?\b", re.I),
    re.compile(r"\b(python|javascript|typescript|rust|java|golang|c\+\+|ruby|swift)\b", re.I),
    re.compile(r"\b(pull request|code review|unit test|test case|CI/CD|dockerfile)\b", re.I),
    re.compile(r"```", re.I),
    re.compile(r"\b(API|endpoint|REST|GraphQL|SQL|query)\b"),
    re.compile(r"\b(git|commit|merge|branch|rebase)\b", re.I),
]

_RESEARCH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bresearch\b", re.I),
    re.compile(r"\b(summarize|summarise)\s+(this|the|a)\s+(paper|article|study|report)\b", re.I),
    re.compile(r"\b(find|search|look\s+up)\s+(information|studies|papers|evidence|data)\b", re.I),
    re.compile(r"\b(medical|scientific|clinical|peer[- ]reviewed)\b", re.I),
    re.compile(r"\b(hypothesis|methodology|conclusion|abstract)\b", re.I),
    re.compile(r"\b(in[- ]depth|comprehensive|thorough)\s+(analysis|review|overview)\b", re.I),
    re.compile(r"\b(literature|systematic)\s+review\b", re.I),
]

_HOME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(turn|switch)\s+(on|off)\s+(the\s+)?\w+", re.I),
    re.compile(r"\b(dim|brighten)\s+(the\s+)?lights?\b", re.I),
    re.compile(r"\block\s+(the\s+)?(door|house|garage)\b", re.I),
    re.compile(r"\bunlock\s+(the\s+)?(door|house|garage)\b", re.I),
    re.compile(r"\bset\s+(the\s+)?(thermostat|temperature|AC)\b", re.I),
    re.compile(r"\bhome\s*assistant\b", re.I),
    re.compile(r"\b(lights?|fan|heater|AC|air\s+condition(er|ing)?)\b", re.I),
    re.compile(r"\b(garage|door|blinds|curtains|shades)\b", re.I),
]

_EDUCATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(teach|learn|explain\s+to\s+(a\s+)?(kid|child|student))\b", re.I),
    re.compile(r"\b(number\s*quest|science\s*safari|word\s*wizard)\b", re.I),
    re.compile(r"\b(homework|lesson|quiz|flashcard|study\s+guide)\b", re.I),
    re.compile(r"\b(little\s+learner|education|educational)\b", re.I),
    re.compile(r"\b(spell|spelling|math\s+problem|multiplication|division)\b", re.I),
]

_SIGHT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(what\s+(do\s+you|can\s+you)\s+see)\b", re.I),
    re.compile(r"\b(look\s+at|describe)\s+(this|the|what'?s)\b", re.I),
    re.compile(r"\b(navigate|obstacle|path|surroundings)\b", re.I),
    re.compile(r"\b(camera|image|photo|picture|screenshot|vision)\b", re.I),
    re.compile(r"\b(read\s+(this|the)\s+(sign|label|text|screen))\b", re.I),
]

_EXPLICIT_AGENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "coding": re.compile(
        r"^\s*(atlas[,:]?\s+)?(write|generate|create)\s+(me\s+)?(some\s+)?"
        r"(code|a\s+script|a\s+function|a\s+class|a\s+program)",
        re.I,
    ),
    "research": re.compile(
        r"^\s*(atlas[,:]?\s+)?research\b",
        re.I,
    ),
}


# ── data classes ─────────────────────────────────────────────────

@dataclass
class AgentTemplate:
    """Blueprint for an agent persona.

    An 'agent' is NOT a separate process — it is a combination of model
    selection, system prompt domain, thinking mode, optional LoRA, and
    tool access that together shape how Atlas responds.
    """

    name: str
    description: str
    model_tier: str           # "fast" | "expert" | "reasoning"
    prompt_domain: str        # "general" | "coding" | "medical" | …
    thinking_mode: str        # "auto" | "on" | "off"
    lora_domain: str | None = None
    tools: list[str] | None = None
    priority: int = 1         # maps to RequestQueue Priority
    codex_enabled: bool = False


@dataclass
class RequestContext:
    """Per-request metadata supplied by the caller."""

    user_id: str
    room: str | None = None
    session_id: str | None = None
    input_type: str = "text"   # "text" | "voice" | "cli"
    has_image: bool = False
    previous_agent: str | None = None
    conversation_summary: str | None = None


@dataclass
class AgentConfig:
    """Fully resolved agent configuration returned by :meth:`route`.

    Carries the template plus the concrete routing decisions produced
    by ModelRouter and ThinkingRouter.
    """

    template: AgentTemplate
    model_choice: Any = None        # ModelChoice from ModelRouter
    thinking_decision: Any = None   # ThinkingDecision from ThinkingRouter
    context: RequestContext | None = None
    reason: str = ""


# ── built-in templates ───────────────────────────────────────────

CONVERSATION = AgentTemplate(
    name="conversation",
    description="General conversation — fast, friendly, full plugin access",
    model_tier="fast",
    prompt_domain="general",
    thinking_mode="auto",
    tools=["all"],
    priority=1,
    codex_enabled=False,
)

CODING = AgentTemplate(
    name="coding",
    description="Code generation and review with CodexPipeline validation",
    model_tier="expert",
    prompt_domain="coding",
    thinking_mode="off",
    lora_domain="coding",
    tools=["code_sandbox"],
    priority=2,
    codex_enabled=True,
)

RESEARCH = AgentTemplate(
    name="research",
    description="Deep research with thinking enabled and knowledge search",
    model_tier="expert",
    prompt_domain="general",
    thinking_mode="on",
    tools=["knowledge_search"],
    priority=2,
    codex_enabled=False,
)

HOME = AgentTemplate(
    name="home",
    description="Home automation via Home Assistant",
    model_tier="fast",
    prompt_domain="home",
    thinking_mode="off",
    tools=["home_assistant"],
    priority=0,
    codex_enabled=False,
)

EDUCATION = AgentTemplate(
    name="education",
    description="Kid-friendly educational assistant",
    model_tier="fast",
    prompt_domain="education",
    thinking_mode="off",
    tools=["little_learner"],
    priority=1,
    codex_enabled=False,
)

SIGHT = AgentTemplate(
    name="sight",
    description="Atlas Sight — vision and navigation assistance",
    model_tier="fast",
    prompt_domain="sight",
    thinking_mode="off",
    tools=["vision", "navigation"],
    priority=0,
    codex_enabled=False,
)

_BUILTIN_TEMPLATES: list[AgentTemplate] = [
    CONVERSATION,
    CODING,
    RESEARCH,
    HOME,
    EDUCATION,
    SIGHT,
]


# ── orchestrator ─────────────────────────────────────────────────

class AgentOrchestrator:
    """Selects and configures the right agent for each request.

    Routing precedence:
    1. Image input → *sight*
    2. Explicit request detected in message → matching agent
    3. CLI input type → *coding*
    4. Voice input type → *conversation*
    5. Keyword/pattern matching against all registered agents
    6. Complexity score from ModelRouter may upgrade the tier
    7. Fallback → *conversation*
    """

    def __init__(
        self,
        *,
        model_router: ModelRouter | None = None,
        thinking_router: ThinkingRouter | None = None,
    ) -> None:
        self._model_router = model_router or ModelRouter()
        self._thinking_router = thinking_router or ThinkingRouter()
        self._agents: dict[str, AgentTemplate] = {}

        for tpl in _BUILTIN_TEMPLATES:
            self.register_agent(tpl)

    # ── public API ───────────────────────────────────────────────

    def register_agent(self, agent: AgentTemplate) -> None:
        """Register (or overwrite) an agent template by name."""
        self._agents[agent.name] = agent
        logger.debug("registered agent template %r", agent.name)

    def list_agents(self) -> list[AgentTemplate]:
        """Return all registered templates sorted by name."""
        return sorted(self._agents.values(), key=lambda a: a.name)

    async def route(
        self,
        message: str,
        context: RequestContext,
    ) -> AgentConfig:
        """Select the best agent template and resolve model + thinking."""

        template = self._classify(message, context)

        # Model routing: start with the template's tier, but let
        # ModelRouter upgrade if complexity warrants it.
        model_choice = self._model_router.route(message)
        effective_tier = self._effective_tier(template, model_choice)
        if effective_tier != template.model_tier:
            logger.info(
                "upgrading %s tier %s → %s (complexity=%d)",
                template.name,
                template.model_tier,
                effective_tier,
                model_choice.complexity_score,
            )
            # Re-route with the upgraded tier to get the right model name
            model_choice = self._model_router.route(message)

        # Thinking routing
        if template.thinking_mode == "on":
            thinking = self._thinking_router.route(message)
            thinking.use_thinking = True
            thinking.reason = "agent template forces thinking on"
        elif template.thinking_mode == "off":
            thinking = self._thinking_router.route(message)
            thinking.use_thinking = False
            thinking.reason = "agent template forces thinking off"
        else:
            thinking = self._thinking_router.route(message)

        reason = f"classified as {template.name!r}"
        if context.has_image:
            reason = "image input detected → sight"
        elif context.input_type == "cli":
            reason = "CLI input → coding"

        config = AgentConfig(
            template=template,
            model_choice=model_choice,
            thinking_decision=thinking,
            context=context,
            reason=reason,
        )
        logger.info(
            "route → agent=%s tier=%s thinking=%s reason=%s",
            template.name,
            effective_tier,
            thinking.use_thinking,
            reason,
        )
        return config

    async def execute(
        self,
        message: str,
        context: RequestContext,
        provider: Any,
    ) -> AsyncGenerator[str, None]:
        """Route, configure, and stream an LLM response.

        This is the main entry point that wires agent selection into the
        inference pipeline.  The caller supplies an LLM provider; this
        method resolves the agent config and streams the result.
        """

        config = await self.route(message, context)
        tpl = config.template

        # Apply thinking tag
        prepared = message
        if config.thinking_decision is not None:
            prepared = self._thinking_router.apply(
                message, config.thinking_decision,
            )

        # Build minimal messages list
        messages: list[dict[str, str]] = [
            {"role": "system", "content": f"[domain:{tpl.prompt_domain}]"},
            {"role": "user", "content": prepared},
        ]

        model_name = (
            config.model_choice.model_name
            if config.model_choice
            else None
        )

        # Stream from provider
        try:
            async for token in provider.chat(
                messages,
                model=model_name,
                stream=True,
            ):
                yield token
        except Exception:
            logger.exception("execute failed for agent %s", tpl.name)
            raise

    # ── internal classification ──────────────────────────────────

    def _classify(
        self,
        message: str,
        context: RequestContext,
    ) -> AgentTemplate:
        """Determine which agent template should handle *message*."""

        # 1) Image → sight
        if context.has_image:
            return self._agents.get("sight", SIGHT)

        # 2) Explicit request
        for agent_name, pattern in _EXPLICIT_AGENT_PATTERNS.items():
            if pattern.search(message):
                if agent_name in self._agents:
                    return self._agents[agent_name]

        # 3) CLI → coding
        if context.input_type == "cli":
            return self._agents.get("coding", CODING)

        # 4) Voice → conversation (but still check for home/education)
        if context.input_type == "voice":
            # Home commands via voice are common — check first
            if self._pattern_score(message, _HOME_PATTERNS) > 0:
                return self._agents.get("home", HOME)
            if self._pattern_score(message, _EDUCATION_PATTERNS) > 0:
                return self._agents.get("education", EDUCATION)
            return self._agents.get("conversation", CONVERSATION)

        # 5) Pattern matching across all domains
        scores: dict[str, int] = {
            "coding": self._pattern_score(message, _CODING_PATTERNS),
            "research": self._pattern_score(message, _RESEARCH_PATTERNS),
            "home": self._pattern_score(message, _HOME_PATTERNS),
            "education": self._pattern_score(message, _EDUCATION_PATTERNS),
            "sight": self._pattern_score(message, _SIGHT_PATTERNS),
        }

        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        if scores[best] > 0 and best in self._agents:
            return self._agents[best]

        # 6) Fallback → conversation
        return self._agents.get("conversation", CONVERSATION)

    @staticmethod
    def _pattern_score(
        message: str,
        patterns: list[re.Pattern[str]],
    ) -> int:
        """Count how many patterns match *message*."""
        return sum(1 for p in patterns if p.search(message))

    def _effective_tier(
        self,
        template: AgentTemplate,
        model_choice: Any,
    ) -> str:
        """Return the higher of the template tier and the routed tier."""
        tier_rank = {"fast": 0, "expert": 1, "reasoning": 2}
        tpl_rank = tier_rank.get(template.model_tier, 0)
        routed_rank = tier_rank.get(
            getattr(model_choice, "tier", "fast"), 0,
        )
        if routed_rank > tpl_rank:
            return model_choice.tier  # type: ignore[union-attr]
        return template.model_tier
