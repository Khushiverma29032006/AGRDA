"""
Data models for workloads.

WorkloadRole  – which role in the experiment (victim, suspect, sentinel…)
WorkloadProfile – synthetic performance-traffic profile type.
WorkloadConfig  – full specification for a single container/process.
WorkloadState   – live runtime state of a running workload.
"""
from __future__ import annotations

import enum
import math
from abc import ABC
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Union

import numpy as np


class ActuatorType(enum.Enum):
    MBA = "mba"
    CPU = "cpu"
    PLACEBO = "placebo"


class ActuatorInterface(ABC):
    """Simulation-only actuator contract; implementations never touch the host."""

    actuator_type: ActuatorType

    def __init__(self) -> None:
        self.applied_doses: Dict[str, int] = {}
        self.host_mutations = 0

    def apply(self, target: str, dose: int) -> int:
        if isinstance(dose, bool) or dose not in (0, 1, 2, 3):
            raise ValueError("dose must be one of 0, 1, 2, or 3")
        self.applied_doses[target] = dose
        return dose


class MBAActuator(ActuatorInterface):
    actuator_type = ActuatorType.MBA


class CPUActuator(ActuatorInterface):
    actuator_type = ActuatorType.CPU


class PlaceboActuator(ActuatorInterface):
    actuator_type = ActuatorType.PLACEBO


class SimulationScenario(enum.Enum):
    S0 = "S0"
    S1 = "S1"
    S3 = "S3"
    S4 = "S4"
    S6 = "S6"


class EnvironmentSimulator:
    """Reproducible synthetic telemetry for selected AGRDA scenarios."""

    WORKLOADS = ("V", "S1", "S2", "P", "N-", "N+")
    BASELINES = {
        "V": (1000.0, 20.0), "S1": (800.0, 15.0), "S2": (800.0, 12.0),
        "P": (500.0, 0.5), "N-": (700.0, 1.0), "N+": (600.0, 10.0),
    }

    def __init__(
        self,
        scenario: Union[SimulationScenario, str] = SimulationScenario.S0,
        actuator: Optional[ActuatorInterface] = None,
        seed: int = 0,
        noise_fraction: float = 0.0,
    ) -> None:
        self.scenario = SimulationScenario(scenario)
        self.actuator = actuator or MBAActuator()
        if not isinstance(self.actuator, ActuatorInterface):
            raise TypeError("actuator must implement ActuatorInterface")
        if not math.isfinite(noise_fraction) or noise_fraction < 0:
            raise ValueError("noise_fraction must be finite and non-negative")
        self.noise_fraction = noise_fraction
        self._rng = np.random.default_rng(seed)
        self._previous_interference = 0.0
        self._window_id = 0

    def simulate(
        self,
        dose: Union[int, Mapping[str, int]],
        *,
        phase: Optional[int] = None,
        washout_s: float = 20.0,
    ) -> Dict[str, Dict[str, Union[str, float, int, bool]]]:
        if not math.isfinite(washout_s) or washout_s < 0:
            raise ValueError("washout_s must be finite and non-negative")
        scalar_target = "P" if self.actuator.actuator_type == ActuatorType.PLACEBO else "S1"
        doses = {scalar_target: dose} if isinstance(dose, int) and not isinstance(dose, bool) else dict(dose) if isinstance(dose, Mapping) else None
        if doses is None:
            raise ValueError("dose must be an integer level or workload-to-dose mapping")
        normalized: Dict[str, float] = {}
        for target, level in doses.items():
            self.actuator.apply(target, level)
            normalized[target] = level / 3.0

        d1 = normalized.get("S1", 0.0)
        d2 = normalized.get("S2", 0.0)
        phase_value = self._window_id % 4 if phase is None else phase
        if isinstance(phase_value, bool) or not isinstance(phase_value, int):
            raise ValueError("phase must be an integer")
        phase_load = (0.0, 0.08, 0.16, 0.04)[phase_value % 4]
        memory_effect = 0.0
        other_effect = 0.0
        carryover = 0.0

        if self.scenario == SimulationScenario.S1 and self.actuator.actuator_type == ActuatorType.MBA:
            memory_effect = 0.20 * (1.0 - d1) + 0.08 * (1.0 - d2)
        elif self.scenario == SimulationScenario.S4 and self.actuator.actuator_type == ActuatorType.CPU:
            other_effect = 0.18 * (1.0 - d1)
        elif self.scenario == SimulationScenario.S6 and self.actuator.actuator_type == ActuatorType.MBA:
            memory_effect = 0.14 * (1.0 - d1)
            carryover = self._previous_interference * math.exp(-washout_s / 15.0)

        if self.scenario == SimulationScenario.S3:
            other_effect = phase_load

        total_effect = min(0.8, memory_effect + other_effect + carryover)
        frequency = 2.4 + (0.45 * d1 if self.scenario == SimulationScenario.S4 and self.actuator.actuator_type == ActuatorType.CPU else 0.0)
        result: Dict[str, Dict[str, Union[str, float, int, bool]]] = {}
        for name in self.WORKLOADS:
            base_tp, base_bw = self.BASELINES[name]
            workload_dose = normalized.get(name, d1 if name == "S1" else d2 if name == "S2" else 0.0)
            throughput = base_tp
            if name == "V":
                throughput *= 1.0 - total_effect
            elif name in ("S1", "S2"):
                throughput *= 1.0 - 0.9 * workload_dose
            elif name == "N+":
                throughput *= 1.0 - total_effect
            if self.scenario == SimulationScenario.S3 and name in ("V", "S1"):
                throughput *= 1.0 - (phase_load if name == "V" else phase_load * 0.5)

            traffic_factor = 1.0 - 0.8 * workload_dose if name in ("S1", "S2") else 0.05 if name == "P" else 1.0
            traffic = base_bw * traffic_factor
            noise = self._rng.normal(0.0, self.noise_fraction)
            throughput = max(0.0, throughput * (1.0 + noise))
            result[name] = {
                "source": "SIMULATION",
                "scenario": self.scenario.value,
                "window_id": self._window_id,
                "phase": phase_value % 4,
                "applied_dose": int(round(workload_dose * 3)),
                "throughput": throughput,
                "mem_bw_gbps": traffic,
                "mem_stall_cpi": 0.5 + 2.0 * memory_effect if name in ("V", "N+") else 0.0,
                "llc_miss_rate": 0.05 + 0.4 * memory_effect if name in ("V", "N+") else 0.0,
                "frequency_ghz": frequency,
                "memory_effect_fraction": memory_effect if name == "V" else 0.0,
                "alternative_effect_fraction": other_effect if name == "V" else 0.0,
                "carryover_fraction": carryover if name == "V" else 0.0,
                "quality_ok": True,
            }
        self._previous_interference = memory_effect
        self._window_id += 1
        return result


class WorkloadRole(enum.Enum):
    """Role of a container/process in the AGRDA experiment."""
    VICTIM = "victim"            # V – performance we measure
    SUSPECT = "suspect"          # S_k – throttled by actuators
    PLACEBO = "placebo"          # P – same actuation type, low memory
    NEG_SENTINEL = "neg_sentinel"  # N-  – should NOT respond
    POS_SENTINEL = "pos_sentinel"  # N+  – should respond (sanity check)


class WorkloadProfile(enum.Enum):
    """
    Synthetic workload traffic profile.

    These determine how the simulation models memory bandwidth,
    cache pressure, CPU usage, and victim performance.
    """
    MEMORY_BANDWIDTH_BOUND = "memory_bandwidth_bound"
    COMPUTE_BOUND = "compute_bound"
    CACHE_RESIDENT = "cache_resident"
    MIXED_HYBRID = "mixed_hybrid"
    NEGLIGIBLE = "negligible"   # near-zero memory, used for S0 / placebo


@dataclass
class WorkloadConfig:
    """
    Full specification for one workload in an experiment.

    All fields that affect reproducibility must be serialisable.
    """
    name: str                           # e.g. "victim", "suspect_1"
    role: WorkloadRole = WorkloadRole.VICTIM
    profile: WorkloadProfile = WorkloadProfile.MEMORY_BANDWIDTH_BOUND

    # For real Linux deployments (not used in simulation mode)
    image: Optional[str] = None         # Container image
    cmd: Optional[List[str]] = None     # Command override
    cpu_pin: Optional[List[int]] = None # Core IDs to pin to

    # Simulation-specific parameters
    # Base throughput at baseline (ops/s) – ground truth for simulation
    sim_base_throughput: float = 1000.0
    # Memory bandwidth generated (GB/s) – simulation only
    sim_mem_bw_gbps: float = 10.0
    # Fraction of victim degradation this suspect causes at full dose
    sim_victim_impact_fraction: float = 0.0
    # Fraction of impact that goes through memory pathway
    sim_memory_pathway_fraction: float = 1.0
    # Random seed offset for reproducibility
    seed_offset: int = 0

    # Metadata
    description: str = ""


@dataclass
class WorkloadState:
    """
    Runtime state of a running workload.

    This is updated by the measurement module during each window.
    """
    name: str
    role: WorkloadRole
    profile: WorkloadProfile

    # Is the workload currently running?
    running: bool = False

    # Process/container ID (or None for simulation)
    pid: Optional[int] = None

    # Last measured performance
    throughput: float = 0.0        # ops/s
    mem_bw_gbps: float = 0.0      # GB/s traffic

    # Baseline (captured before experiment)
    baseline_throughput: float = 0.0
    baseline_mem_bw_gbps: float = 0.0

    # Current applied dose (0 = baseline)
    current_dose: float = 0.0

    # Safety floor
    min_throughput_fraction: float = 0.50  # 50% of baseline as per document

    errors: List[str] = field(default_factory=list)
