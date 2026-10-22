"""
CPU-Quota actuator (cgroup v2 cpu.max).

Stage 6 implementation — requires:
  - Linux host with cgroup v2
  - The suspect process/container must be in a cgroup

IMPORTANT DISTINCTION:
  CPU-quota and MBA are DIFFERENT actuators with DIFFERENT causal pathways.
  This actuator controls CPU time allocation, NOT memory bandwidth.

  CPU-quota → limits suspect CPU time
           → may reduce suspect memory access frequency
           → may indirectly reduce memory pressure on victim
           → BUT ALSO: changes suspect scheduling, frequency, working set

  This is used as a COMPARATOR (B4 / C6) to test whether effects differ
  from MBA. If they are the same, there may be a common non-memory pathway.
  If they differ, this supports (but does not prove) the memory specificity
  of MBA effects.

  DO NOT describe this as a memory-bandwidth controller.
  DO NOT substitute it for MBAActuator.

THIS FILE IS A STUB.  Real implementation in Stage 6.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .interface import (
    ActuatorInterface, ActuatorLogEntry, ActuatorResult, ActuatorType,
)

logger = logging.getLogger(__name__)


class CpuQuotaActuator(ActuatorInterface):
    """
    cgroup v2 cpu.max actuator (STUB — not yet implemented).

    Controls CPU time via the cgroup v2 cpu.max file:
      format: "<quota_us> <period_us>"
      e.g.:   "100000 100000" = 100% CPU
              "50000 100000"  = 50% CPU

    dose=0.0  → cpu.max = "max 100000"  (no quota limit)
    dose=1.0  → cpu.max = "10000 100000"  (10% CPU, approximately)

    Causal pathway:
      CPU quota → reduced CPU time for suspect
               → less computation → less memory access frequency
               → may reduce memory bandwidth pressure on DRAM
               → BUT: also changes scheduling, TLB, cache, frequency

    This actuator is used ONLY as:
      1. A C6 (alternative actuator) comparator
      2. B4 partial audit test
    Never as the primary causal actuator for memory attribution.
    """

    ACTUATOR_TYPE = ActuatorType.CPU_QUOTA
    PERIOD_US = 100_000   # 100 ms standard period

    def __init__(
        self,
        dry_run: bool = True,
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        min_quota_fraction: float = 0.10,  # floor at 10% CPU
    ) -> None:
        super().__init__(dry_run=dry_run)
        self.cgroup_root = cgroup_root
        self.min_quota_fraction = min_quota_fraction
        self._cgroup_paths: Dict[str, Path] = {}

    def is_available(self) -> bool:
        """Check if cgroup v2 is available."""
        cpu_max_check = self.cgroup_root / "cpu.max"
        return self.cgroup_root.exists() and cpu_max_check.exists()

    def get_capabilities(self) -> Dict[str, Any]:
        return {
            "type": self.ACTUATOR_TYPE.value,
            "is_available": self.is_available(),
            "dry_run": self.dry_run,
            "cgroup_root": str(self.cgroup_root),
            "note": (
                "CPU-quota controls CPU time, NOT memory bandwidth. "
                "Used only as a C6/B4 comparator actuator. Stage 6 stub."
            ),
        }

    def capture_baseline(self, target_names: List[str]) -> None:
        """Record baseline (no quota limit) for all targets."""
        for name in target_names:
            self._baselines[name] = "max"  # no quota
        logger.info(
            "CpuQuotaActuator: baseline captured (STUB) for %s", target_names
        )

    def apply(self, name: str, dose: float) -> ActuatorResult:
        """
        Apply CPU quota to the named target.

        dose=0.0 → no quota (max)
        dose=1.0 → 10% CPU (min_quota_fraction)

        quota_us = round(period_us * max(min_fraction, 1 - dose))
        cpu.max  = f"{quota_us} {period_us}"

        STUB: Returns error (not implemented).
        """
        ok, msg = self.validate_dose(dose)
        if not ok:
            result = ActuatorResult(
                success=False, actuator_type=self.ACTUATOR_TYPE,
                target_name=name, requested_dose=dose, error=msg,
            )
            self._append_log(result.to_log_entry("apply"))
            return result

        # Convert dose to quota
        if dose == 0.0:
            cpu_max_str = f"max {self.PERIOD_US}"
            quota_us = None
        else:
            fraction = max(self.min_quota_fraction, 1.0 - dose)
            quota_us = round(fraction * self.PERIOD_US)
            cpu_max_str = f"{quota_us} {self.PERIOD_US}"

        if self.dry_run:
            logger.info(
                "CpuQuotaActuator DRY RUN: would set %s cpu.max=%s (dose=%.2f)",
                name, cpu_max_str, dose,
            )
            result = ActuatorResult(
                success=True, actuator_type=self.ACTUATOR_TYPE,
                target_name=name, requested_dose=dose,
                applied_setting=cpu_max_str,
                readback_value=cpu_max_str, readback_matches=True,
                notes=[
                    f"DRY RUN: would write cpu.max={cpu_max_str}",
                    "CAUTION: CPU-quota is NOT a memory actuator. "
                    "Use ONLY as C6/B4 comparator.",
                    "STUB: real cgroup write not implemented",
                ],
            )
        else:
            error_msg = (
                "CpuQuotaActuator: real write NOT IMPLEMENTED (Stage 6 stub). "
                "Implement _write_cpu_max() before enabling real experiments."
            )
            logger.error(error_msg)
            result = ActuatorResult(
                success=False, actuator_type=self.ACTUATOR_TYPE,
                target_name=name, requested_dose=dose,
                applied_setting=None, error=error_msg,
            )

        self._append_log(result.to_log_entry("apply"))
        return result

    def rollback(self, name: str) -> ActuatorResult:
        """Restore cpu.max to 'max' (no quota) for the named target."""
        baseline = self._baselines.get(name, "max")
        if self.dry_run:
            result = ActuatorResult(
                success=True, actuator_type=self.ACTUATOR_TYPE,
                target_name=name, requested_dose=0.0,
                applied_setting=baseline,
                readback_value=baseline, readback_matches=True,
                notes=["DRY RUN rollback — STUB"],
            )
        else:
            result = ActuatorResult(
                success=False, actuator_type=self.ACTUATOR_TYPE,
                target_name=name, requested_dose=0.0,
                error="CpuQuotaActuator rollback: NOT IMPLEMENTED (Stage 6 stub)",
            )
        self._append_log(result.to_log_entry("rollback"))
        return result

    def rollback_all(self) -> List[ActuatorResult]:
        return [self.rollback(name) for name in list(self._baselines.keys())]

    def verify(self, name: str) -> Tuple[Any, bool]:
        """Read back cpu.max from cgroup filesystem. STUB."""
        logger.warning("CpuQuotaActuator.verify(): STUB — not implemented")
        return (None, False)

    # ------------------------------------------------------------------
    # Internal helpers (to be implemented in Stage 6)
    # ------------------------------------------------------------------

    def _write_cpu_max(self, cgroup_path: Path, cpu_max_str: str) -> None:
        """
        Write to <cgroup_path>/cpu.max and verify read-back.
        NOT IMPLEMENTED — Stage 6.
        """
        raise NotImplementedError(
            "_write_cpu_max() not implemented. "
            "Stage 6: implement cgroup v2 write + verification."
        )

    def _read_cpu_max(self, cgroup_path: Path) -> Optional[str]:
        """
        Read current cpu.max value from the cgroup.
        NOT IMPLEMENTED — Stage 6.
        """
        raise NotImplementedError("_read_cpu_max() not implemented.")
