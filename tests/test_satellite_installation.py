"""Installation and dual-ReSpeaker profile regression tests."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from satellite.atlas_satellite.audio import AudioCapture
from satellite.atlas_satellite.config import SatelliteConfig
from satellite.atlas_satellite.diagnostics import Diagnostic, run_diagnostics


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    (
        "profile", "capture_format", "capture_channels",
        "led_type", "led_count", "button_enabled",
    ),
    [
        ("respeaker_2mic", "S16_LE", 2, "respeaker_2mic", 3, True),
        ("respeaker_4mic", "S32_LE", 4, "respeaker_4mic", 12, False),
    ],
)
def test_supported_hat_configurations(
    profile, capture_format, capture_channels, led_type, led_count, button_enabled,
):
    config = SatelliteConfig(
        hardware_profile=profile,
        capture_format=capture_format,
        capture_channels=capture_channels,
        led_type=led_type,
        led_count=led_count,
        button_enabled=button_enabled,
    )
    assert config.hardware_profile == profile


@pytest.mark.parametrize(
    "values",
    [
        {"hardware_profile": "imaginary"},
        {"capture_format": "FLOAT_LE"},
        {"capture_channels": 0},
        {"channels": 4},
        {"led_type": "respeaker_6mic"},
    ],
)
def test_invalid_hardware_configurations_fail_explicitly(values):
    with pytest.raises(ValueError):
        SatelliteConfig(**values)


def test_four_channel_s32_capture_downmixes_to_transport_mono_s16():
    frames = [
        [1000 << 16] * 4,
        [-2000 << 16] * 4,
        [32767 << 16] * 4,
    ]
    raw = struct.pack("<12i", *(sample for frame in frames for sample in frame))
    converted = AudioCapture._to_mono_s16(raw, 4, "S32_LE")
    assert struct.unpack("<3h", converted) == (1000, -2000, 32767)


def test_multichannel_s16_capture_downmixes_without_changing_frame_count():
    raw = struct.pack("<6h", 100, 300, -100, -300, 500, 700)
    converted = AudioCapture._to_mono_s16(raw, 2, "S16_LE")
    assert struct.unpack("<3h", converted) == (200, -200, 600)


def test_diagnostics_reject_invalid_configuration(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hardware_profile": "not-real"}))
    results = run_diagnostics(path, check_server=False)
    assert any(result.name == "config" and result.status == "fail" for result in results)


def test_diagnostics_reject_missing_configuration(tmp_path):
    results = run_diagnostics(tmp_path / "missing.json", check_server=False)
    assert any(result.name == "config" and result.status == "fail" for result in results)


def test_diagnostics_reports_valid_profile_without_physical_claim(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_path = tmp_path / "config.json"
    SatelliteConfig(
        mode="shared",
        data_dir=str(data_dir),
        hardware_profile="respeaker_4mic",
        capture_format="S32_LE",
        capture_channels=4,
        led_type="respeaker_4mic",
        led_count=12,
        button_enabled=False,
    ).save(config_path)

    capture = "card 1: seeed4micvoicec [seeed-4mic-voicecard], device 0: ac108"
    playback = "card 0: Headphones [bcm2835 Headphones], device 0"
    with (
        patch(
            "satellite.atlas_satellite.diagnostics._audio_listing",
            side_effect=[(True, capture), (True, playback)],
        ),
        patch(
            "satellite.atlas_satellite.diagnostics._device_check",
            return_value=Diagnostic("device", "pass", "available"),
        ),
        patch(
            "satellite.atlas_satellite.diagnostics.importlib.util.find_spec",
            return_value=object(),
        ),
    ):
        results = run_diagnostics(config_path, check_server=False)

    assert not [result for result in results if result.required and not result.passed]
    assert next(result for result in results if result.name == "server").status == "info"


def test_installer_contains_direct_service_update_and_rollback_contracts():
    installer = (ROOT / "satellite" / "install.sh").read_text()
    assert "Digitalbrainstem/atlas-cortex" in installer
    assert "respeaker-2mic" in installer and "respeaker-4mic" in installer
    assert "CONFIG_PROFILE=respeaker_2mic" in installer
    assert "CONFIG_PROFILE=respeaker_4mic" in installer
    assert '"hardware_profile": os.environ["CONFIG_PROFILE"]' in installer
    assert "atlas-satellite.service" in installer
    assert "--rollback" in installer and "--uninstall" in installer
    assert "Automatic HAT detection found no configured ReSpeaker ALSA card." in installer
    assert '-f "$candidate/manifest.json"' in installer
    assert '-x "$candidate/venv/bin/atlas-satellite"' in installer
    assert 'STAGING_RELEASE="$release_dir"' in installer
    assert 'mv -Tf "$INSTALL_ROOT/.current.new" "$INSTALL_ROOT/current"' in installer


def test_systemd_service_uses_dedicated_identity_and_disables_setup_server():
    unit = (ROOT / "satellite" / "systemd" / "atlas-satellite.service").read_text()
    assert "User=atlas-satellite" in unit
    assert "--setup-port 0" in unit
    assert "Restart=on-failure" in unit
    assert "ReadWritePaths=/etc/atlas-satellite /var/lib/atlas-satellite" in unit
