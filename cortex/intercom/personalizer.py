"""Message personalizer for intercom announcements.

Adapts message tone and vocabulary for the target audience before
TTS synthesis.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Friendly prefixes added for child-appropriate messages
_CHILD_PREFIXES = [
    "Hey buddy! ",
    "Hey there! ",
    "Listen up, friends! ",
]


class MessagePersonalizer:
    """Adapt intercom message tone for different age groups."""

    def personalize(
        self, message: str, target_user_age_group: str = "adult"
    ) -> str:
        """Return a version of *message* suited to *target_user_age_group*.

        Supported groups: child, teen, adult (default).
        """
        group = target_user_age_group.lower()
        if group == "child":
            return self._for_child(message)
        if group == "teen":
            return self._for_teen(message)
        return message

    # ── Internal helpers ─────────────────────────────────────────

    def _for_child(self, message: str) -> str:
        prefix = _CHILD_PREFIXES[len(message) % len(_CHILD_PREFIXES)]
        simplified = message
        for old, new in (
            ("immediately", "right now"),
            ("proceed to", "go to"),
            ("Proceed to", "Go to"),
            ("required", "needed"),
            ("attention", "listen up"),
            ("commence", "start"),
            ("Commence", "Start"),
        ):
            simplified = simplified.replace(old, new)
        return f"{prefix}{simplified}"

    def _for_teen(self, message: str) -> str:
        result = message
        for old, new in (
            ("proceed to", "head to"),
            ("Proceed to", "Head to"),
            ("commence", "start"),
            ("Commence", "Start"),
            ("immediately", "now"),
        ):
            result = result.replace(old, new)
        return result
