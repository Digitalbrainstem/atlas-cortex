from __future__ import annotations

from setuptools import find_packages, setup

setup(
    name="atlas-satellite",
    version="0.2.0",
    packages=find_packages(),
    install_requires=[
        "aiohttp>=3.9",
        "pyalsaaudio>=0.10.0",
        "webrtcvad-wheels>=2.0.14",
        "websockets>=12.0",
        "zeroconf>=0.131.0",
    ],
    extras_require={
        "hat": ["gpiod>=2.1", "spidev>=3.6"],
        "wakeword": ["numpy>=1.24", "onnxruntime>=1.17", "openwakeword>=0.6.0"],
    },
    entry_points={
        "console_scripts": [
            "atlas-satellite=atlas_satellite.__main__:main",
            "atlas-satellite-diagnose=atlas_satellite.diagnostics:main",
        ],
    },
    python_requires=">=3.11",
)
