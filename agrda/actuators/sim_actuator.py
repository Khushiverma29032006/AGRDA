"""
Simulation actuator – stores doses in memory, no real system calls.

This is the default actuator for Windows / non-Linux / simulation mode.
All operations are logged exactly as real operations would be, so that
the watchdog, rollback, and audit-log code paths are fully exercised.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .interface import (
    ActuatorInterface, ActuatorLogEntry, ActuatorResult, ActuatorType,
)

logger = logging.getLogger(__name__)


class SimActuator(ActuatorInterface):
    """
    In-memory simulation actuator.

    Stores the current dose for each named target.  The SimWorkloadBackend
    reads doses from the WorkloadManager, which is independent; this class
    just records and validates the requested doses so that the audit trail
    is realistic.
    """

    ACTUATOR_TYPE = ActuatorType.SIMULATION

    def __init__(self, dry_run: bool = True) -> None:
        super().__init__(dry_run=dry_run)
        self._current_doses: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Interface implementation
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return True   # Simulation always available

    def get_capabilities(self) -> Dict[str, Any]:
        return {
            "type": self.ACTUATOR_TYPE.value,
            "is_available": True,
            "dry_run": self.dry_run,
            "note": "Simulation mode – no real system calls",
        }

    def capture_baseline(self, target_names: List[str]) -> None:
        """Record dose=0.0 as baseline for all targets."""
        for name in target_names:
            self._baselines[name] = 0.0
            self._current_doses[name] = 0.0
        logger.debug("SimActuator: baseline captured for %s", target_names)

    def apply(self, name: str, dose: float) -> ActuatorResult:
        ok, msg = self.validate_dose(dose)
        if not ok:
            result = ActuatorResult(
                success=False,
                actuator_type=self.ACTUATOR_TYPE,
                target_name=name,
                requested_dose=dose,
                error=msg,
            )
            self._append_log(result.to_log_entry("apply"))
            return result

        self._current_doses[name] = dose
        result = ActuatorResult(
            success=True,
            actuator_type=self.ACTUATOR_TYPE,
            target_name=name,
            requested_dose=dose,
            applied_setting=dose,
            readback_value=dose,
            readback_matches=True,
            notes=["SIMULATION – no real system change"],
        )
        self._append_log(result.to_log_entry("apply"))
        return result

    def rollback(self, name: str) -> ActuatorResult:
        baseline = self._baselines.get(name, 0.0)
        self._current_doses[name] = baseline
        result = ActuatorResult(
            success=True,
            actuator_type=self.ACTUATOR_TYPE,
            target_name=name,
            requested_dose=baseline,
            applied_setting=baseline,
            readback_value=baseline,
            readback_matches=True,
            notes=["SIMULATION rollback"],
        )
        self._append_log(result.to_log_entry("rollback"))
        return result

    def rollback_all(self) -> List[ActuatorResult]:
        results = []
        for name in list(self._current_doses.keys()):
            results.append(self.rollback(name))
        return results

    def verify(self, name: str) -> Tuple[Any, bool]:
        current = self._current_doses.get(name, 0.0)
        expected = self._current_doses.get(name, 0.0)
        return current, (current == expected)

    def get_current_dose(self, name: str) -> float:
        return self._current_doses.get(name, 0.0)
