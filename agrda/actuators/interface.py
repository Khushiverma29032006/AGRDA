"""
Abstract actuator interface + result type.

Design principles (from AGRDA document):
  1. MBA (Intel RDT/resctrl) and CPU-quota (cgroup v2 cpu.max) are
     DIFFERENT actuators with DIFFERENT causal pathways.
     Do NOT conflate them or describe CPU quota as a memory controller.
  2. Every actuation must be logged (apply + read-back + rollback).
  3. The interface exposes capability checks so callers can skip
     unavailable actuators gracefully.
  4. Dry-run mode must be default; real actuation requires explicit
     confirmation.
"""
from __future__ import annotations

import enum
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ActuatorType(enum.Enum):
    """
    The type of hardware/OS control used.

    MBA: Intel Resource Director Technology — Memory Bandwidth Allocation.
         Acts on the memory subsystem directly.
         ONLY valid on supported Intel Xeon/server CPUs with resctrl.
         DO NOT substitute CPU quota for this.

    CPU_QUOTA: cgroup v2 cpu.max — limits CPU time.
               Alternative-pathway actuator (affects compute, scheduling,
               sometimes frequency). Used as a COMPARATOR, not a
               memory-bandwidth control.

    PLACEBO: Applied to the placebo container (low memory footprint).
             Used to test whether the actuation type itself (not memory
             interference) causes victim response.

    SIMULATION: Synthetic dose — no real hardware changes.
    """
    MBA = "mba"
    CPU_QUOTA = "cpu_quota"
    PLACEBO = "placebo"
    SIMULATION = "simulation"


@dataclass
class ActuatorLogEntry:
    """One entry in the actuator audit log."""
    timestamp: str
    actuator_type: str
    target_name: str
    requested_dose: float
    applied_setting: Any          # The actual value written (e.g., % or quota)
    readback_value: Optional[Any]  # Value read back after write
    readback_matches: Optional[bool]  # Whether it matched requested
    action: str                   # "apply" | "rollback" | "baseline_capture"
    success: bool
    error: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class ActuatorResult:
    """
    Result of a single actuator apply or rollback operation.

    Callers must check `success` and log `entry`.
    """
    success: bool
    actuator_type: ActuatorType
    target_name: str
    requested_dose: float
    applied_setting: Any = None
    readback_value: Any = None
    readback_matches: Optional[bool] = None
    error: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    entry: Optional[ActuatorLogEntry] = None

    def to_log_entry(self, action: str) -> ActuatorLogEntry:
        return ActuatorLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            actuator_type=self.actuator_type.value,
            target_name=self.target_name,
            requested_dose=self.requested_dose,
            applied_setting=self.applied_setting,
            readback_value=self.readback_value,
            readback_matches=self.readback_matches,
            action=action,
            success=self.success,
            error=self.error,
            notes=self.notes,
        )


class ActuatorInterface(ABC):
    """
    Common interface for all actuator backends.

    Subclasses implement:
      - is_available()    : does this actuator work on this host?
      - get_capabilities(): dict of capability details
      - capture_baseline(): record current settings before experiment
      - apply(name, dose): throttle a target workload
      - rollback(name)   : restore baseline for a target
      - rollback_all()   : restore ALL targets (called by watchdog)
      - verify(name)     : read back current setting

    Real (Linux) subclasses must additionally:
      - Require dry_run=False + explicit confirmation to do anything
      - Write to resctrl or cgroupfs
      - Verify write-back within tolerance

    Simulation subclass stores doses in memory only.
    """

    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run
        self._log: List[ActuatorLogEntry] = []
        self._baselines: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Abstract API
    # ------------------------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this actuator can actually be used on the host."""

    @abstractmethod
    def get_capabilities(self) -> Dict[str, Any]:
        """Return a dict of capability details (for capability report)."""

    @abstractmethod
    def capture_baseline(self, target_names: List[str]) -> None:
        """Record current settings as baseline for later rollback."""

    @abstractmethod
    def apply(self, name: str, dose: float) -> ActuatorResult:
        """
        Apply a throttle dose [0=baseline, 1=maximum throttle] to `name`.

        Must enforce:
          - dose in [0, 1]
          - minimum-throughput safeguard (≥50% of baseline)
          - log entry written even on failure

        In dry_run mode, log what WOULD be done but change nothing.
        """

    @abstractmethod
    def rollback(self, name: str) -> ActuatorResult:
        """Restore baseline setting for a single target."""

    @abstractmethod
    def rollback_all(self) -> List[ActuatorResult]:
        """Restore all targets to baseline. Called by watchdog."""

    @abstractmethod
    def verify(self, name: str) -> Tuple[Any, bool]:
        """
        Read back the current setting for `name`.
        Returns (current_value, matches_expected).
        """

    # ------------------------------------------------------------------
    # Log access
    # ------------------------------------------------------------------

    def get_log(self) -> List[ActuatorLogEntry]:
        return list(self._log)

    def _append_log(self, entry: ActuatorLogEntry) -> None:
        self._log.append(entry)
        logger.debug(
            "Actuator log: %s %s %s dose=%.3f ok=%s",
            entry.action, entry.actuator_type,
            entry.target_name, entry.requested_dose, entry.success,
        )

    # ------------------------------------------------------------------
    # Dose validation helper
    # ------------------------------------------------------------------

    @staticmethod
    def validate_dose(dose: float) -> Tuple[bool, str]:
        if not (0.0 <= dose <= 1.0):
            return False, f"Dose {dose} out of range [0, 1]"
        return True, ""
