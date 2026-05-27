"""Cinematic pipeline architectures — 5 distinct approaches.

A — SKELETON      → 5 beats → expand → compress → voice → visuals
B — RETENTION     → 4 spikes at time markers → connect → visuals
C — CURIOSITY     → 14-rung ladder → speakify → visuals
D — CRITIQUE      → draft → self-critique-driven rewrite → visuals
E — CONSTRAINT    → Python word-budget allocation → write each → visuals

All share the same retry-with-feedback wrapper from `_shared.with_retries`.
"""

from . import arch_a_skeleton, arch_b_retention, arch_c_curiosity, arch_d_critique, arch_e_constraint, arch_main
from ._shared import with_retries

ARCHITECTURES = {
    "A_skeleton":   arch_a_skeleton.generate,
    "B_retention":  arch_b_retention.generate,
    "C_curiosity":  arch_c_curiosity.generate,
    "D_critique":   arch_d_critique.generate,
    "E_constraint": arch_e_constraint.generate,
    "MAIN":         arch_main.generate,  # production pipeline
}

__all__ = ["ARCHITECTURES", "with_retries"]
