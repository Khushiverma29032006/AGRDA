"""
Telemetry models – metric classification and measurement records.

Metric labels (from document requirements):
  DIRECT_OUTCOME        – victim throughput / latency (primary Y)
  MEDIATOR_PROXY        – victim memory stall CPI (M in the model)
  ALTERNATIVE_PATHWAY   – frequency, scheduling, SMT, cache, IRQ monitors
  CONTEXTUAL            – temperature, NUMA locality, power

Each metric's label determines how it is used:
  - DIRECT_OUTCOME and MEDIATOR_PROXY drive the primary analysis.
  - ALTERNATIVE_PATHWAY metrics are used ONLY in the audit (C5).
    They must NEVER be used as covariates in the primary effect model
    (they are post-treatment variables).
  - CONTEXTUAL metrics are logged but not used in analysis.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class MetricLabel(enum.Enum):
    """Classification of each telemetry metric."""
    DIRECT_OUTCOME = "direct_outcome"       # Y – primary victim metric
    MEDIATOR_PROXY = "mediator_proxy"       # M – memory stall CPI
    ALTERNATIVE_PATHWAY = "alt_pathway"    # C5 audit only
    CONTEXTUAL = "contextual"              # logging only


@dataclass
class MeasurementRecord:
    """
    One measured value for one metric in one window.

    missing: True when the metric could not be collected.
    Never substitute invented values for missing metrics.
    """
    metric_name: str
    label: MetricLabel
    value: Optional[float]          # None when missing
    missing: bool = False
    missing_reason: str = ""
    unit: str = ""
    counter_running_fraction: Optional[float] = None   # perf multiplexing
    quality_ok: bool = True
    quality_note: str = ""
    timestamp: str = ""
    source: str = "SIMULATION"      # Always set


@dataclass
class TelemetryWindow:
    """
    All measurements collected during one window.
    """
    window_id: int
    suspect_name: str
    dose: float
    measurements: List[MeasurementRecord] = field(default_factory=list)
    collection_errors: List[str] = field(default_factory=list)

    def get(self, metric_name: str) -> Optional[MeasurementRecord]:
        for m in self.measurements:
            if m.metric_name == metric_name:
                return m
        return None

    def is_quality_ok(self, max_missing_fraction: float = 0.20) -> bool:
        """Window is OK if fewer than max_missing_fraction of metrics are missing."""
        if not self.measurements:
            return False
        missing = sum(1 for m in self.measurements if m.missing)
        return (missing / len(self.measurements)) <= max_missing_fraction
