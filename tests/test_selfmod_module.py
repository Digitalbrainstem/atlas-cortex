"""Tests for cortex/selfmod/ — zone classification and change validation.

Prove that frozen/mutable zones are correctly enforced by the rules engine.
Every zone test below verifies actual fnmatch behavior against _ZONE_RULES.
"""
from __future__ import annotations

import pytest

from cortex.selfmod.zones import (
    Zone,
    ZoneRule,
    get_zone,
    validate_change,
    FROZEN_ZONES,
    MUTABLE_ZONES,
)


# ===========================================================================
# Zone enum basics
# ===========================================================================

class TestZoneEnum:
    def test_frozen_value(self):
        assert Zone.FROZEN.value == "frozen"

    def test_mutable_value(self):
        assert Zone.MUTABLE.value == "mutable"

    def test_generated_value(self):
        assert Zone.GENERATED.value == "generated"


# ===========================================================================
# ZoneRule dataclass
# ===========================================================================

class TestZoneRule:
    def test_is_frozen_dataclass(self):
        rule = ZoneRule("test/*", Zone.FROZEN, "test reason")
        with pytest.raises(AttributeError):
            rule.pattern = "other/*"

    def test_fields(self):
        rule = ZoneRule("safety/*", Zone.FROZEN, "Safety is critical")
        assert rule.pattern == "safety/*"
        assert rule.zone == Zone.FROZEN
        assert rule.reason == "Safety is critical"


# ===========================================================================
# get_zone — FROZEN paths
# ===========================================================================

class TestGetZoneFrozen:
    def test_safety_jailbreak(self):
        rule = get_zone("safety/jailbreak.py")
        assert rule.zone == Zone.FROZEN

    def test_selfmod_zones(self):
        rule = get_zone("selfmod/zones.py")
        assert rule.zone == Zone.FROZEN

    def test_integrity_init(self):
        rule = get_zone("integrity/__init__.py")
        assert rule.zone == Zone.FROZEN

    def test_pipeline_init(self):
        rule = get_zone("pipeline/__init__.py")
        assert rule.zone == Zone.FROZEN

    def test_pipeline_events(self):
        rule = get_zone("pipeline/events.py")
        assert rule.zone == Zone.FROZEN

    def test_auth_py(self):
        rule = get_zone("auth.py")
        assert rule.zone == Zone.FROZEN

    def test_db_py(self):
        rule = get_zone("db.py")
        assert rule.zone == Zone.FROZEN

    def test_unknown_file_defaults_frozen(self):
        """Catch-all rule: unknown paths default to FROZEN."""
        rule = get_zone("unknown/new_file.py")
        assert rule.zone == Zone.FROZEN

    def test_deeply_nested_safety(self):
        rule = get_zone("safety/deep/nested/module.py")
        assert rule.zone == Zone.FROZEN

    def test_cortex_prefix_stripped(self):
        """Paths with 'cortex/' prefix should still match."""
        rule = get_zone("cortex/safety/jailbreak.py")
        assert rule.zone == Zone.FROZEN

    def test_selfmod_any_file(self):
        rule = get_zone("selfmod/anything.py")
        assert rule.zone == Zone.FROZEN


# ===========================================================================
# get_zone — MUTABLE paths
# ===========================================================================

class TestGetZoneMutable:
    def test_content_jokes(self):
        rule = get_zone("content/jokes.py")
        assert rule.zone == Zone.MUTABLE

    def test_pipeline_layer1_instant(self):
        rule = get_zone("pipeline/layer1_instant.py")
        assert rule.zone == Zone.MUTABLE

    def test_pipeline_layer2_plugins(self):
        rule = get_zone("pipeline/layer2_plugins.py")
        assert rule.zone == Zone.MUTABLE

    def test_filler_subpath(self):
        rule = get_zone("filler/phrases.py")
        assert rule.zone == Zone.MUTABLE

    def test_content_subpath(self):
        rule = get_zone("content/anything.py")
        assert rule.zone == Zone.MUTABLE

    def test_learning_integration(self):
        rule = get_zone("integrations/learning/patterns.py")
        assert rule.zone == Zone.MUTABLE


# ===========================================================================
# get_zone — first match wins ordering
# ===========================================================================

class TestGetZoneOrdering:
    def test_pipeline_init_frozen_despite_mutable_layers(self):
        """pipeline/__init__.py is FROZEN even though layer files are MUTABLE."""
        init_rule = get_zone("pipeline/__init__.py")
        layer_rule = get_zone("pipeline/layer1_instant.py")
        assert init_rule.zone == Zone.FROZEN
        assert layer_rule.zone == Zone.MUTABLE

    def test_pipeline_non_listed_file_is_frozen(self):
        """pipeline/layer3_llm.py is not in any specific rule → catch-all FROZEN."""
        rule = get_zone("pipeline/layer3_llm.py")
        assert rule.zone == Zone.FROZEN


# ===========================================================================
# validate_change
# ===========================================================================

class TestValidateChange:
    def test_frozen_zone_disallowed(self):
        allowed, reason = validate_change("safety/jailbreak.py")
        assert allowed is False
        assert isinstance(reason, str)
        assert len(reason) > 0

    def test_mutable_zone_allowed(self):
        allowed, reason = validate_change("content/jokes.py")
        assert allowed is True
        assert isinstance(reason, str)

    def test_unknown_path_disallowed(self):
        allowed, reason = validate_change("some/random/module.py")
        assert allowed is False

    def test_selfmod_disallowed(self):
        allowed, reason = validate_change("selfmod/zones.py")
        assert allowed is False

    def test_integrity_disallowed(self):
        allowed, reason = validate_change("integrity/__init__.py")
        assert allowed is False

    def test_pipeline_orchestrator_disallowed(self):
        allowed, reason = validate_change("pipeline/__init__.py")
        assert allowed is False

    def test_pipeline_layer1_allowed(self):
        allowed, reason = validate_change("pipeline/layer1_instant.py")
        assert allowed is True

    def test_filler_allowed(self):
        allowed, reason = validate_change("filler/cache.py")
        assert allowed is True

    def test_cortex_prefix_handled(self):
        """Paths prefixed with 'cortex/' should work the same."""
        allowed, reason = validate_change("cortex/content/jokes.py")
        assert allowed is True

    def test_return_type(self):
        result = validate_change("safety/jailbreak.py")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)


# ===========================================================================
# Convenience sets
# ===========================================================================

class TestConvenienceSets:
    def test_frozen_zones_contains_safety(self):
        assert "safety/*" in FROZEN_ZONES

    def test_frozen_zones_contains_selfmod(self):
        assert "selfmod/*" in FROZEN_ZONES

    def test_mutable_zones_contains_content(self):
        assert "content/*" in MUTABLE_ZONES

    def test_mutable_zones_contains_filler(self):
        assert "filler/*" in MUTABLE_ZONES

    def test_no_overlap(self):
        """FROZEN_ZONES and MUTABLE_ZONES should not overlap."""
        overlap = FROZEN_ZONES & MUTABLE_ZONES
        assert overlap == set(), f"Overlapping zones: {overlap}"

    def test_catch_all_is_frozen(self):
        assert "*" in FROZEN_ZONES
