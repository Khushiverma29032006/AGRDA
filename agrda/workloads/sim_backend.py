"""
Simulation workload backend.

Generates deterministic, reproducible synthetic measurements that mimic
what real workloads would produce on Linux hardware.

IMPORTANT: All outputs from this module are clearly labelled as
SIMULATION – NOT A MEASURED RESULT. They must never be presented
as real hardware measurements.

Design:
  Each workload has a physics model:
    - victim throughput degrades with total memory pressure
    - suspects generate memory traffic and (optionally) affect victim
    - sentinels have fixed or zero responses
    - noise is Gaussian with configurable sigma

The ground-truth causal structure is stored explicitly so that
tests can verify the audit battery is detecting the right things.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .models import WorkloadConfig, WorkloadProfile, WorkloadRole, WorkloadState


DATA_SOURCE_TAG = "SIMULATION"  # Appended to every measurement record


@dataclass
class SimMeasurement:
    """
    A single simulated measurement window output.

    source is always DATA_SOURCE_TAG so downstream code can check it.
    """
    source: str = DATA_SOURCE_TAG   # Always "SIMULATION"
    window_id: int = 0
    workload_name: str = ""
    role: str = ""

    # Outcomes
    throughput: float = 0.0         # ops/s  (victim primary outcome Y)
    mem_bw_gbps: float = 0.0        # suspect/victim memory traffic (proxy M)
    mem_stall_cpi: float = 0.0      # victim memory stall cycles/instruction
    llc_miss_rate: float = 0.0      # LLC miss rate (fraction)

    # Applied dose (0 = baseline)
    applied_dose: float = 0.0

    # Ground-truth causal breakdown (simulation only, never in real data)
    gt_memory_effect: float = 0.0   # portion of degradation via memory path
    gt_other_effect: float = 0.0    # portion via alternative pathways
    gt_total_effect: float = 0.0    # total degradation from all suspects

    # Quality flags
    quality_ok: bool = True
    quality_notes: List[str] = field(default_factory=list)


class SimWorkloadBackend:
    """
    Deterministic simulation backend for AGRDA workloads.

    Given a set of WorkloadConfigs and a scenario, this produces
    measurements that honour the causal structure described in the
    AGRDA document.

    Usage:
        backend = SimWorkloadBackend(configs, seed=42)
        backend.start_all()
        meas = backend.measure_window(window_id=0, doses={'suspect_1': 0.3})
        backend.stop_all()
    """

    # Noise sigma as fraction of baseline throughput
    THROUGHPUT_NOISE_SIGMA = 0.02   # 2% relative noise
    MEM_BW_NOISE_SIGMA = 0.03       # 3% relative noise

    def __init__(
        self,
        configs: List[WorkloadConfig],
        seed: int = 42,
        scenario_label: str = "UNKNOWN",
    ) -> None:
        self.configs = {c.name: c for c in configs}
        self.seed = seed
        self.scenario_label = scenario_label
        self._rng = np.random.default_rng(seed)
        self._states: Dict[str, WorkloadState] = {}
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_all(self) -> None:
        """Initialise simulation state for all configured workloads."""
        self._states = {}
        for name, cfg in self.configs.items():
            state = WorkloadState(
                name=name,
                role=cfg.role,
                profile=cfg.profile,
                running=True,
                baseline_throughput=cfg.sim_base_throughput,
                baseline_mem_bw_gbps=cfg.sim_mem_bw_gbps,
                throughput=cfg.sim_base_throughput,
                mem_bw_gbps=cfg.sim_mem_bw_gbps,
            )
            self._states[name] = state
        self._running = True

    def stop_all(self) -> None:
        for state in self._states.values():
            state.running = False
        self._running = False

    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------

    def measure_window(
        self,
        window_id: int,
        doses: Dict[str, float],   # suspect_name -> dose in [0, 1]
    ) -> List[SimMeasurement]:
        """
        Produce one measurement window for all workloads.

        doses: maps suspect/placebo name -> throttle dose [0=baseline,1=full]

        Returns one SimMeasurement per configured workload.
        The victim's measurement encodes ground-truth causal breakdowns.
        """
        assert self._running, "Call start_all() first"

        results = []

        # First compute how much victim is affected
        victim_mem_effect = 0.0
        victim_other_effect = 0.0
        suspect_configs = {
            n: c for n, c in self.configs.items()
            if c.role == WorkloadRole.SUSPECT
        }

        for s_name, s_cfg in suspect_configs.items():
            dose = doses.get(s_name, 0.0)
            # Throttling reduces memory bandwidth (linear for simplicity)
            # At dose 1.0, suspect generates (1-dose)*baseline traffic
            effective_dose = max(0.0, min(1.0, dose))
            # impact on victim is proportional to dose (more throttle =
            # less memory interference = LESS degradation to victim)
            # At dose=0 (baseline), suspect causes full impact.
            # At dose=1, impact is 0.
            mem_impact = (
                s_cfg.sim_victim_impact_fraction
                * s_cfg.sim_memory_pathway_fraction
                * (1.0 - effective_dose)
            )
            other_impact = (
                s_cfg.sim_victim_impact_fraction
                * (1.0 - s_cfg.sim_memory_pathway_fraction)
                * (1.0 - effective_dose)
            )
            victim_mem_effect += mem_impact
            victim_other_effect += other_impact

        total_degradation = victim_mem_effect + victim_other_effect

        for name, cfg in self.configs.items():
            dose = doses.get(name, 0.0)
            effective_dose = max(0.0, min(1.0, dose))
            meas = SimMeasurement(
                source=DATA_SOURCE_TAG,
                window_id=window_id,
                workload_name=name,
                role=cfg.role.value,
                applied_dose=effective_dose,
            )

            if cfg.role == WorkloadRole.VICTIM:
                base = cfg.sim_base_throughput
                # Victim throughput = baseline * (1 - total_degradation) + noise
                signal = base * (1.0 - total_degradation)
                noise = self._rng.normal(0, base * self.THROUGHPUT_NOISE_SIGMA)
                meas.throughput = max(0.0, signal + noise)
                # Memory stall CPI is proportional to memory interference
                meas.mem_stall_cpi = 0.5 + 2.0 * victim_mem_effect + \
                    self._rng.normal(0, 0.05)
                meas.mem_bw_gbps = cfg.sim_mem_bw_gbps + \
                    self._rng.normal(0, cfg.sim_mem_bw_gbps * self.MEM_BW_NOISE_SIGMA)
                meas.llc_miss_rate = 0.05 + 0.4 * victim_mem_effect + \
                    self._rng.normal(0, 0.01)
                # Ground-truth breakdown (fraction of baseline)
                meas.gt_memory_effect = victim_mem_effect
                meas.gt_other_effect = victim_other_effect
                meas.gt_total_effect = total_degradation

            elif cfg.role == WorkloadRole.SUSPECT:
                base_bw = cfg.sim_mem_bw_gbps
                # Throttle reduces memory traffic
                signal_bw = base_bw * (1.0 - 0.8 * effective_dose)
                noise_bw = self._rng.normal(
                    0, base_bw * self.MEM_BW_NOISE_SIGMA
                )
                meas.mem_bw_gbps = max(0.0, signal_bw + noise_bw)
                # Suspect's own throughput (application metric)
                base_tp = cfg.sim_base_throughput
                signal_tp = base_tp * (1.0 - 0.9 * effective_dose)
                noise_tp = self._rng.normal(
                    0, base_tp * self.THROUGHPUT_NOISE_SIGMA
                )
                meas.throughput = max(0.0, signal_tp + noise_tp)

            elif cfg.role == WorkloadRole.PLACEBO:
                # Placebo: same actuation type but negligible memory
                # Victim should NOT respond to placebo actuation
                base_tp = cfg.sim_base_throughput
                noise = self._rng.normal(
                    0, base_tp * self.THROUGHPUT_NOISE_SIGMA
                )
                meas.throughput = max(0.0, base_tp + noise)
                meas.mem_bw_gbps = cfg.sim_mem_bw_gbps * 0.05  # negligible

            elif cfg.role == WorkloadRole.NEG_SENTINEL:
                # Negative sentinel: placed on disjoint resources
                # Should NOT respond to any suspect actuation
                base_tp = cfg.sim_base_throughput
                noise = self._rng.normal(
                    0, base_tp * self.THROUGHPUT_NOISE_SIGMA
                )
                meas.throughput = max(0.0, base_tp + noise)

            elif cfg.role == WorkloadRole.POS_SENTINEL:
                # Positive sentinel: memory-bound, same victim resources
                # SHOULD respond similarly to victim
                base_tp = cfg.sim_base_throughput
                signal = base_tp * (1.0 - total_degradation)
                noise = self._rng.normal(
                    0, base_tp * self.THROUGHPUT_NOISE_SIGMA
                )
                meas.throughput = max(0.0, signal + noise)

            self._update_state(name, meas)
            results.append(meas)

        return results

    def capture_baseline(self, n_windows: int = 5) -> Dict[str, float]:
        """
        Measure baseline (dose=0) over n_windows and return mean throughput
        per workload. Used by the pilot module.
        """
        assert self._running, "Call start_all() first"
        sums: Dict[str, float] = {n: 0.0 for n in self.configs}
        for i in range(n_windows):
            measurements = self.measure_window(window_id=i, doses={})
            for m in measurements:
                sums[m.workload_name] += m.throughput
        return {n: v / n_windows for n, v in sums.items()}

    # ------------------------------------------------------------------
    # Safety check (mirrors real backend interface)
    # ------------------------------------------------------------------

    def check_safety(
        self,
        name: str,
        proposed_dose: float,
    ) -> Tuple[bool, str]:
        """
        Return (ok, reason).  In simulation, we enforce the 50% floor.
        """
        cfg = self.configs.get(name)
        if cfg is None:
            return False, f"Unknown workload: {name}"
        state = self._states.get(name)
        if state is None:
            return False, "Workload not started"
        min_tp = state.baseline_throughput * state.min_throughput_fraction
        projected = cfg.sim_base_throughput * (1.0 - 0.9 * proposed_dose)
        if projected < min_tp:
            return False, (
                f"Dose {proposed_dose:.2f} would reduce throughput to "
                f"{projected:.1f} < floor {min_tp:.1f} (50% of baseline)"
            )
        return True, "OK"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_state(self, name: str, meas: SimMeasurement) -> None:
        state = self._states[name]
        state.throughput = meas.throughput
        state.mem_bw_gbps = meas.mem_bw_gbps
