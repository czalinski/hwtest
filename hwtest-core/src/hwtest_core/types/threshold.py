"""Threshold types for state-dependent limits (implemented in Rust).

Classes:
    BoundType: Inclusive or exclusive bound.
    ThresholdBound: One side of a threshold.
    Threshold: Low/high limits for one channel.
    StateThresholds: All channel thresholds for one environmental state.
"""

from hwtest_core._enums import BoundType
from hwtest_core._native import StateThresholds, Threshold, ThresholdBound

__all__ = ["BoundType", "StateThresholds", "Threshold", "ThresholdBound"]
