"""
Telemetry collector – simulation backend.

On this Windows host (SIMULATION_ONLY), all metrics are synthetic.
On a real Linux host, this would read from:
  - perf_event (via subprocess perf stat, or python-perf bindings)
  - /sys/fs/resctrl/mon_data/ (MBM: memory bandwidth monitoring)
  - /proc/pressure/ (PSI)
  - cgroup cpuacct and memory.stat
  - eBPF schedstat

Implementation decision: The interface is designed so the real Linux
collector can be substituted without changing any analysis code.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from .models import MeasurementRecord, MetricLabel, TelemetryWindow

logger = logging.getLogger(__name__)

SIMULATION_TAG = "SIMULATION"


class TelemetryCollector:
    """
    Simulation telemetry collector.

    Produces synthetic measurements with realistic noise for each window.
    All records are tagged source=SIMULATION.

    Real Linux collector would subclass or replace this with:
      - perf stat subprocess calls
      - resctrl MBM reads
      - PSI/pressure reads
    """

    # Metrics collected (simulation approximations)
    METRIC_DEFINITIONS = [
        # name, label, unit, baseline_value, noise_rel
        ("victim_throughput",  MetricLabel.DIRECT_OUTCOME,   "ops/s",   1000.0, 0.02),
        ("victim_mem_stall_cpi", MetricLabel.MEDIATOR_PROXY, "cycles/instr", 0.5, 0.10),
        ("victim_llc_miss_rate", MetricLabel.MEDIATOR_PROXY, "fraction", 0.05, 0.20),
        ("suspect_mbm_bw",     MetricLabel.CONTEXTUAL,       "GB/s",    10.0,  0.03),
        ("socket_mem_bw",      MetricLabel.CONTEXTUAL,       "GB/s",    30.0,  0.03),
        ("cpu_freq_mhz",       MetricLabel.ALTERNATIVE_PATHWAY, "MHz", 2300.0, 0.01),
        ("runqueue_len",       MetricLabel.ALTERNATIVE_PATHWAY, "tasks",  1.0,  0.15),
        ("irq_rate",           MetricLabel.ALTERNATIVE_PATHWAY, "irq/s", 500.0, 0.05),
    ]

    def __init__(self, seed: int = 42) -> None:
        self._rng = np.random.default_rng(seed)
        self._baselines: Dict[str, float] = {
            name: base for name, _, _, base, _ in self.METRIC_DEFINITIONS
        }

    def collect_window(
        self,
        window_id: int,
        suspect_name: str,
        dose: float,
        victim_effect: float = 0.0,   # fraction of baseline degraded
        pathway_effects: Optional[Dict[str, float]] = None,
    ) -> TelemetryWindow:
        """
        Collect (simulate) all metrics for one measurement window.

        Parameters
        ----------
        victim_effect  : fraction of baseline throughput lost (e.g. 0.15)
        pathway_effects: dict of alternative pathway sizes (for S4/S9)
        """
        pathway_effects = pathway_effects or {}
        now = datetime.now(timezone.utc).isoformat()
        window = TelemetryWindow(
            window_id=window_id,
            suspect_name=suspect_name,
            dose=dose,
        )

        for name, label, unit, baseline, noise_rel in self.METRIC_DEFINITIONS:
            val = self._simulate_metric(
                name=name,
                baseline=baseline,
                noise_rel=noise_rel,
                dose=dose,
                victim_effect=victim_effect,
                pathway_effects=pathway_effects,
            )
            record = MeasurementRecord(
                metric_name=name,
                label=label,
                value=val,
                missing=False,
                unit=unit,
                quality_ok=True,
                timestamp=now,
                source=SIMULATION_TAG,
            )
            window.measurements.append(record)

        return window

    def _simulate_metric(
        self,
        name: str,
        baseline: float,
        noise_rel: float,
        dose: float,
        victim_effect: float,
        pathway_effects: Dict[str, float],
    ) -> float:
        noise = self._rng.normal(0, baseline * noise_rel)

        if name == "victim_throughput":
            return max(0, baseline * (1 - victim_effect) + noise)

        elif name == "victim_mem_stall_cpi":
            # More memory interference → more stall cycles
            return max(0, baseline + 2.0 * victim_effect + noise * 0.1)

        elif name == "victim_llc_miss_rate":
            return max(0.0, min(1.0, baseline + 0.4 * victim_effect + noise * 0.01))

        elif name == "suspect_mbm_bw":
            # Higher dose → lower suspect bandwidth
            return max(0, baseline * (1 - 0.8 * dose) + noise)

        elif name == "cpu_freq_mhz":
            freq_effect = pathway_effects.get("cpu_frequency_drop_fraction", 0.0)
            return max(0, baseline * (1 - freq_effect) + noise)

        elif name == "irq_rate":
            irq_effect = pathway_effects.get("interrupt_rate_change_fraction", 0.0)
            return max(0, baseline * (1 + irq_effect) + noise)

        else:
            return max(0, baseline + noise)
