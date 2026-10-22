"""
WorkloadManager – coordinates starting, stopping, and monitoring
workloads. Acts as the interface layer so the research logic is
independent of whether we use simulation or a real container runtime.

Implementation decision: On this Windows host we default to the
SimWorkloadBackend. The interface is designed so a real Linux
adapter (DockerBackend or CgroupBackend) can be substituted later
without changing any caller code.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .models import WorkloadConfig, WorkloadRole, WorkloadState
from .sim_backend import SimWorkloadBackend, SimMeasurement

logger = logging.getLogger(__name__)


class WorkloadManager:
    """
    Manages all workloads in one experiment run.

    Currently always uses SimWorkloadBackend (simulation mode).
    A real Linux backend would implement the same measure_window()
    and capture_baseline() interface.
    """

    def __init__(
        self,
        configs: List[WorkloadConfig],
        seed: int = 42,
        scenario_label: str = "UNKNOWN",
        dry_run: bool = True,
    ) -> None:
        self.configs = configs
        self.seed = seed
        self.scenario_label = scenario_label
        self.dry_run = dry_run  # always True in simulation

        # Validate: must have exactly one victim
        victims = [c for c in configs if c.role == WorkloadRole.VICTIM]
        if len(victims) != 1:
            raise ValueError(
                f"Exactly 1 victim required; got {len(victims)}"
            )

        self._backend = SimWorkloadBackend(
            configs=configs,
            seed=seed,
            scenario_label=scenario_label,
        )
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_all(self) -> None:
        logger.info(
            "Starting all workloads (simulation mode, scenario=%s)",
            self.scenario_label,
        )
        self._backend.start_all()
        self._started = True

    def stop_all(self) -> None:
        if self._started:
            self._backend.stop_all()
            self._started = False
            logger.info("All workloads stopped.")

    def __enter__(self):
        self.start_all()
        return self

    def __exit__(self, *args):
        self.stop_all()

    # ------------------------------------------------------------------
    # Measurement proxy
    # ------------------------------------------------------------------

    def measure_window(
        self,
        window_id: int,
        doses: Dict[str, float],
    ) -> List[SimMeasurement]:
        if not self._started:
            raise RuntimeError("Call start_all() first")
        return self._backend.measure_window(window_id=window_id, doses=doses)

    def capture_baseline(self, n_windows: int = 5) -> Dict[str, float]:
        if not self._started:
            raise RuntimeError("Call start_all() first")
        return self._backend.capture_baseline(n_windows=n_windows)

    # ------------------------------------------------------------------
    # Safety check
    # ------------------------------------------------------------------

    def check_safety(
        self,
        suspect_name: str,
        proposed_dose: float,
    ) -> tuple:
        return self._backend.check_safety(suspect_name, proposed_dose)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def get_victim(self) -> WorkloadConfig:
        return next(c for c in self.configs if c.role == WorkloadRole.VICTIM)

    def get_suspects(self) -> List[WorkloadConfig]:
        return [c for c in self.configs if c.role == WorkloadRole.SUSPECT]

    def get_placebo(self) -> Optional[WorkloadConfig]:
        placebos = [c for c in self.configs if c.role == WorkloadRole.PLACEBO]
        return placebos[0] if placebos else None

    def get_sentinels(self) -> List[WorkloadConfig]:
        return [
            c for c in self.configs
            if c.role in (WorkloadRole.NEG_SENTINEL, WorkloadRole.POS_SENTINEL)
        ]
