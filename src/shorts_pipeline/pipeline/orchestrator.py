"""Production pipeline orchestrator boundary.

This module is the forward-compatible import location for the stage coordinator.
The legacy `shorts_pipeline.orchestrator` module reuses the same implementation
so existing scripts continue to work during the production refactor.
"""

from shorts_pipeline.orchestrator import PipelineOrchestrator, orchestrator_stage_handler

__all__ = ["PipelineOrchestrator", "orchestrator_stage_handler"]
