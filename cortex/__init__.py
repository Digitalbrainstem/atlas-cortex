"""Atlas Cortex — Core AI assistant engine.

Part 1: Core Engine (portable, no infrastructure dependencies).

Quickstart::

    from cortex.providers import get_provider
    from cortex.pipeline import run_pipeline

    provider = get_provider()   # reads LLM_PROVIDER from env (default: ollama)
    async for chunk in await run_pipeline("What time is it?", provider):
        print(chunk, end="", flush=True)
"""

__version__ = "1.0.0"
