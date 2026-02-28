"""SSH-based hardware detection for satellite devices.

Connects via SSH and runs detection commands to identify:
  - Platform: RPi model, CPU arch, RAM, storage
  - Audio: microphones, speakers, ALSA/PulseAudio devices
  - LEDs: ReSpeaker, NeoPixel, GPIO, activity LEDs
  - Sensors: I2C devices (temp, humidity, light, mmWave)
  - Resources: CPU cores, RAM, disk space

Uses asyncssh for real SSH; a MockSSH class is provided for tests.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────


@dataclass
class PlatformInfo:
    model: str = "unknown"  # "Raspberry Pi 4 Model B Rev 1.4"
    arch: str = "unknown"  # "aarch64", "x86_64"
    cpu_model: str = ""
    cpu_cores: int = 0
    ram_mb: int = 0
    disk_total_mb: int = 0
    disk_free_mb: int = 0
    os_name: str = ""  # "Raspberry Pi OS (bookworm)"
    os_version: str = ""
    kernel: str = ""


@dataclass
class AudioDevice:
    name: str
    device_type: str  # "capture" or "playback"
    card: int = 0
    device: int = 0
    alsa_id: str = ""


@dataclass
class AudioInfo:
    capture_devices: list[AudioDevice] = field(default_factory=list)
    playback_devices: list[AudioDevice] = field(default_factory=list)
    has_pulseaudio: bool = False
    has_pipewire: bool = False


@dataclass
class LEDInfo:
    led_type: str = ""  # "respeaker_apa102", "neopixel", "gpio", "none"
    count: int = 0
    details: str = ""


@dataclass
class SensorInfo:
    i2c_addresses: list[str] = field(default_factory=list)
    identified: dict[str, str] = field(default_factory=dict)  # addr -> name


@dataclass
class HardwareProfile:
    """Complete hardware profile for a satellite device."""

    platform: PlatformInfo = field(default_factory=PlatformInfo)
    audio: AudioInfo = field(default_factory=AudioInfo)
    leds: LEDInfo = field(default_factory=LEDInfo)
    sensors: SensorInfo = field(default_factory=SensorInfo)

    def to_dict(self) -> dict:
        """Serialize for JSON storage in DB."""
        import dataclasses
        return dataclasses.asdict(self)

    def platform_short(self) -> str:
        """Short platform identifier: rpi4, rpi3, rpizero2w, x86, arm, etc."""
        model = self.platform.model.lower()
        if "raspberry pi 5" in model:
            return "rpi5"
        if "raspberry pi 4" in model:
            return "rpi4"
        if "raspberry pi 3" in model:
            return "rpi3"
        if "raspberry pi zero 2" in model:
            return "rpizero2w"
        if "raspberry pi zero" in model:
            return "rpizero"
        if "raspberry pi" in model:
            return "rpi"
        if self.platform.arch == "x86_64":
            return "x86"
        if "aarch64" in self.platform.arch or "arm" in self.platform.arch:
            return "arm"
        return "unknown"

    def capabilities_dict(self) -> dict:
        """Summarize capabilities as a dict for the DB capabilities column."""
        return {
            "mic": len(self.audio.capture_devices) > 0,
            "speaker": len(self.audio.playback_devices) > 0,
            "led": self.leds.led_type != "" and self.leds.led_type != "none",
            "led_type": self.leds.led_type,
            "led_count": self.leds.count,
            "sensors": len(self.sensors.identified) > 0,
            "sensor_list": list(self.sensors.identified.values()),
            "aec": any("seeed" in d.name.lower() or "respeaker" in d.name.lower()
                       for d in self.audio.capture_devices),
        }


# ── SSH abstraction ───────────────────────────────────────────────


class SSHConnection(Protocol):
    """Protocol for SSH connections — real asyncssh or mock."""

    async def run(self, command: str) -> "SSHResult":
        ...

    async def close(self) -> None:
        ...


@dataclass
class SSHResult:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class AsyncSSHConnection:
    """Real SSH connection using asyncssh."""

    def __init__(self, conn) -> None:
        self._conn = conn

    async def run(self, command: str) -> SSHResult:
        result = await self._conn.run(command, check=False)
        return SSHResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            returncode=result.returncode or 0,
        )

    async def close(self) -> None:
        self._conn.close()
        await self._conn.wait_closed()


class MockSSHConnection:
    """Mock SSH for testing — returns pre-configured responses."""

    def __init__(self, responses: dict[str, SSHResult] | None = None) -> None:
        self._responses = responses or {}
        self._default = SSHResult(stdout="", returncode=1)

    async def run(self, command: str) -> SSHResult:
        # Check exact match first, then prefix match
        if command in self._responses:
            return self._responses[command]
        for key, val in self._responses.items():
            if command.startswith(key):
                return val
        return self._default

    async def close(self) -> None:
        pass


async def connect_ssh(
    host: str,
    username: str = "atlas",
    password: str | None = None,
    key_path: str | None = None,
    port: int = 22,
) -> AsyncSSHConnection:
    """Open an asyncssh connection to a satellite host."""
    import asyncssh

    kwargs: dict = {
        "host": host,
        "port": port,
        "username": username,
        "known_hosts": None,  # Accept any host key for satellites
    }
    if password:
        kwargs["password"] = password
    if key_path:
        kwargs["client_keys"] = [key_path]

    conn = await asyncssh.connect(**kwargs)
    return AsyncSSHConnection(conn)


# ── Hardware detector ─────────────────────────────────────────────


class HardwareDetector:
    """Detect satellite hardware capabilities via SSH."""

    async def detect(self, ssh: SSHConnection) -> HardwareProfile:
        """Run all detection and return a complete hardware profile."""
        profile = HardwareProfile()
        profile.platform = await self.detect_platform(ssh)
        profile.audio = await self.detect_audio(ssh)
        profile.leds = await self.detect_leds(ssh)
        profile.sensors = await self.detect_sensors(ssh)
        return profile

    async def detect_platform(self, ssh: SSHConnection) -> PlatformInfo:
        """Identify platform: RPi model, CPU, RAM, disk, OS."""
        info = PlatformInfo()

        # Device model (RPi, OrangePi, BeagleBone, etc.)
        result = await ssh.run("cat /proc/device-tree/model 2>/dev/null || echo unknown")
        info.model = result.stdout.strip().rstrip("\x00") or "unknown"

        # CPU architecture
        result = await ssh.run("uname -m")
        info.arch = result.stdout.strip()

        # CPU model and core count
        result = await ssh.run("nproc")
        try:
            info.cpu_cores = int(result.stdout.strip())
        except ValueError:
            pass

        result = await ssh.run("grep -m1 'model name' /proc/cpuinfo | cut -d: -f2")
        info.cpu_model = result.stdout.strip()

        # RAM
        result = await ssh.run("grep MemTotal /proc/meminfo | awk '{print $2}'")
        try:
            info.ram_mb = int(result.stdout.strip()) // 1024
        except ValueError:
            pass

        # Disk
        result = await ssh.run("df / --output=size,avail -B M | tail -1")
        parts = result.stdout.strip().replace("M", "").split()
        if len(parts) >= 2:
            try:
                info.disk_total_mb = int(parts[0])
                info.disk_free_mb = int(parts[1])
            except ValueError:
                pass

        # OS info
        result = await ssh.run("cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'")
        info.os_name = result.stdout.strip()

        result = await ssh.run("uname -r")
        info.kernel = result.stdout.strip()

        return info

    async def detect_audio(self, ssh: SSHConnection) -> AudioInfo:
        """Find microphones and speakers via ALSA."""
        audio = AudioInfo()

        # Capture devices (microphones)
        result = await ssh.run("arecord -l 2>/dev/null || true")
        for match in re.finditer(
            r"card (\d+): .+\[(.+?)\].*device (\d+): (.+?) \[",
            result.stdout,
        ):
            audio.capture_devices.append(AudioDevice(
                name=match.group(4).strip(),
                device_type="capture",
                card=int(match.group(1)),
                device=int(match.group(3)),
                alsa_id=f"hw:{match.group(1)},{match.group(3)}",
            ))

        # Playback devices (speakers)
        result = await ssh.run("aplay -l 2>/dev/null || true")
        for match in re.finditer(
            r"card (\d+): .+\[(.+?)\].*device (\d+): (.+?) \[",
            result.stdout,
        ):
            audio.playback_devices.append(AudioDevice(
                name=match.group(4).strip(),
                device_type="playback",
                card=int(match.group(1)),
                device=int(match.group(3)),
                alsa_id=f"hw:{match.group(1)},{match.group(3)}",
            ))

        # Check for PulseAudio/PipeWire
        result = await ssh.run("pactl info 2>/dev/null && echo PA_OK || true")
        audio.has_pulseaudio = "PA_OK" in result.stdout

        result = await ssh.run("pw-cli info 0 2>/dev/null && echo PW_OK || true")
        audio.has_pipewire = "PW_OK" in result.stdout

        return audio

    async def detect_leds(self, ssh: SSHConnection) -> LEDInfo:
        """Detect LED hardware: ReSpeaker APA102, NeoPixel, GPIO."""
        # ReSpeaker HAT (APA102 via SPI)
        result = await ssh.run("ls /dev/spidev* 2>/dev/null || true")
        if "/dev/spidev" in result.stdout:
            # Check for ReSpeaker specifically
            result2 = await ssh.run(
                "i2cdetect -y 1 2>/dev/null | grep -c '3b\\|1a' || echo 0"
            )
            if result2.stdout.strip() != "0":
                return LEDInfo(led_type="respeaker_apa102", count=12, details="ReSpeaker HAT")

        # NeoPixel/WS2812B — check for known SPI/PWM configs
        result = await ssh.run("ls /dev/ws281x* 2>/dev/null || true")
        if "/dev/ws281x" in result.stdout:
            return LEDInfo(led_type="neopixel", count=0, details="WS281x detected")

        # GPIO activity LED (always available on Pi)
        result = await ssh.run("ls /sys/class/leds/ 2>/dev/null || true")
        leds = result.stdout.strip()
        if leds:
            led_names = [l for l in leds.split() if l]
            return LEDInfo(
                led_type="gpio",
                count=len(led_names),
                details=", ".join(led_names[:5]),
            )

        return LEDInfo(led_type="none")

    async def detect_sensors(self, ssh: SSHConnection) -> SensorInfo:
        """Scan I2C bus for known sensors."""
        sensors = SensorInfo()

        result = await ssh.run("i2cdetect -y 1 2>/dev/null || true")
        if result.returncode != 0:
            return sensors

        # Parse i2cdetect output for addresses
        for line in result.stdout.split("\n"):
            for part in line.split():
                if re.match(r"^[0-9a-f]{2}$", part) and part != "--":
                    addr = f"0x{part}"
                    sensors.i2c_addresses.append(addr)
                    # Identify known sensors
                    name = _KNOWN_I2C_SENSORS.get(int(part, 16))
                    if name:
                        sensors.identified[addr] = name

        return sensors


# Known I2C sensor addresses
_KNOWN_I2C_SENSORS = {
    0x23: "BH1750 (light)",
    0x29: "VL53L0X (distance) / TSL2591 (light)",
    0x38: "AHT20 (temp/humidity)",
    0x39: "TSL2561 (light)",
    0x40: "SHT30 (temp/humidity) / HDC1080",
    0x44: "SHT40 (temp/humidity)",
    0x48: "ADS1115 (ADC) / TMP102 (temp)",
    0x49: "ADS1115 (ADC)",
    0x50: "EEPROM",
    0x68: "MPU6050 (IMU) / DS3231 (RTC)",
    0x76: "BME280/BMP280 (temp/pressure/humidity)",
    0x77: "BME680 (air quality)",
}
