"""pytest configuration for Atlas Cortex tests."""

import asyncio
import pytest


# Configure asyncio mode for pytest-asyncio
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )
