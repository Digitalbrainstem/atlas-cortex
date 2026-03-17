"""Built-in routine templates that users can instantiate."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cortex.routines.engine import RoutineEngine

logger = logging.getLogger(__name__)


TEMPLATES: dict[str, dict] = {
    "good_morning": {
        "name": "Good Morning",
        "description": "Start your day with lights and a greeting",
        "steps": [
            {"action_type": "tts_announce", "action_config": {"message": "Good morning! Here's your day."}},
            {"action_type": "ha_service", "action_config": {"domain": "light", "service": "turn_on", "entity_id": "light.bedroom"}},
            {"action_type": "delay", "action_config": {"seconds": 2}},
            {"action_type": "tts_announce", "action_config": {"message": "Lights are on. Have a great day!"}},
        ],
        "default_trigger": {"trigger_type": "voice_phrase", "trigger_config": {"phrase": "good morning"}},
    },
    "good_night": {
        "name": "Good Night",
        "description": "Wind down with lights off and a goodnight message",
        "steps": [
            {"action_type": "tts_announce", "action_config": {"message": "Good night! Sleep well."}},
            {"action_type": "ha_service", "action_config": {"domain": "light", "service": "turn_off", "entity_id": "light.bedroom"}},
            {"action_type": "ha_service", "action_config": {"domain": "light", "service": "turn_off", "entity_id": "light.living_room"}},
        ],
        "default_trigger": {"trigger_type": "voice_phrase", "trigger_config": {"phrase": "good night"}},
    },
    "movie_time": {
        "name": "Movie Time",
        "description": "Dim the lights for a movie",
        "steps": [
            {"action_type": "tts_announce", "action_config": {"message": "Setting up for movie time!"}},
            {"action_type": "ha_service", "action_config": {"domain": "light", "service": "turn_on", "entity_id": "light.living_room", "data": {"brightness": 50}}},
            {"action_type": "delay", "action_config": {"seconds": 1}},
            {"action_type": "tts_announce", "action_config": {"message": "Lights are dimmed. Enjoy the movie!"}},
        ],
        "default_trigger": {"trigger_type": "voice_phrase", "trigger_config": {"phrase": "movie time"}},
    },
    "dinner_time": {
        "name": "Dinner Time",
        "description": "Set the mood for dinner",
        "steps": [
            {"action_type": "tts_announce", "action_config": {"message": "Dinner time! Setting the mood."}},
            {"action_type": "ha_service", "action_config": {"domain": "light", "service": "turn_on", "entity_id": "light.dining_room", "data": {"brightness": 180}}},
        ],
        "default_trigger": {"trigger_type": "voice_phrase", "trigger_config": {"phrase": "dinner time"}},
    },
    "leaving_home": {
        "name": "Leaving Home",
        "description": "Turn everything off when you leave",
        "steps": [
            {"action_type": "ha_service", "action_config": {"domain": "light", "service": "turn_off", "entity_id": "all"}},
            {"action_type": "tts_announce", "action_config": {"message": "Everything is off. See you later!"}},
        ],
        "default_trigger": {"trigger_type": "voice_phrase", "trigger_config": {"phrase": "leaving home"}},
    },
    "arriving_home": {
        "name": "Arriving Home",
        "description": "Welcome home with lights on",
        "steps": [
            {"action_type": "ha_service", "action_config": {"domain": "light", "service": "turn_on", "entity_id": "light.living_room"}},
            {"action_type": "tts_announce", "action_config": {"message": "Welcome home!"}},
        ],
        "default_trigger": {"trigger_type": "voice_phrase", "trigger_config": {"phrase": "arriving home"}},
    },
}


async def instantiate_template(
    engine: RoutineEngine, template_id: str, user_id: str = ""
) -> int:
    """Create a routine from a template. Returns routine_id.

    Raises ``KeyError`` if *template_id* is not in :data:`TEMPLATES`.
    """
    template = TEMPLATES[template_id]

    routine_id = await engine.create_routine(
        name=template["name"],
        description=template["description"],
        user_id=user_id,
        template_id=template_id,
    )

    for step in template.get("steps", []):
        await engine.add_step(
            routine_id=routine_id,
            action_type=step["action_type"],
            action_config=step["action_config"],
        )

    trigger = template.get("default_trigger")
    if trigger:
        await engine.add_trigger(
            routine_id=routine_id,
            trigger_type=trigger["trigger_type"],
            trigger_config=trigger["trigger_config"],
        )

    logger.info("Instantiated template '%s' as routine %d", template_id, routine_id)
    return routine_id
