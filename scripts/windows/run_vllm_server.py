"""Start vLLM OpenAI API on Windows.

Official vLLM imports uvloop, which does not support Windows. This stub
registers a minimal uvloop module that delegates to asyncio.run, then runs
the standard api_server entrypoint with the same CLI flags.
"""

from __future__ import annotations

import asyncio
import runpy
import sys
import types

if sys.platform == "win32":
    _uvloop = types.ModuleType("uvloop")
    _uvloop.run = asyncio.run
    sys.modules["uvloop"] = _uvloop

if __name__ == "__main__":
    runpy.run_module(
        "vllm.entrypoints.openai.api_server",
        run_name="__main__",
        alter_sys=True,
    )
