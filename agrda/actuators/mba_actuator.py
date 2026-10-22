"""
MBA (Intel RDT Memory Bandwidth Allocation) actuator.

Stage 6 implementation — requires:
  - Linux host
  - Intel CPU with RDT/MBA support (Xeon Scalable or similar)
  - resctrl filesystem mounted at /sys/fs/resctrl
  - root or CAP_SYS_ADMIN capability

THIS FILE IS A STUB.  It documents the interface contract for the real
Linux adapter that will be implemented when suitable hardware is available.

Safety contract (from AGRDA document):
  - Every write is followed by a read-back verification
  - All operations are logged
  - Rollback completes within 1 second
  - dry_run=True by default; real writes require dry_run=False + explicit
    operator confirmation
  - MBA granularity is respected (typically 10% steps)
  - CPU quota must NOT be used here: MBA and CPU-quota are different
    actuator types with different causal pathways

IMPORTANT:
  MBA controls memory bandwidth allocation — it is NOT a CPU quota.
  Never describe or use this actuator as a compute scheduler.
  The exclusion restriction (MBA affects victim ONLY via memory bandwidth)
  is the core assumption being tested. Using the wrong actuator invalidates
  the test.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .interface import (
    ActuatorInterface, ActuatorLogEntry, ActuatorResult, ActuatorType,
)

logger = logging.getLogger(__name__)

# resctrl filesystem path
RESCTRL_PATH = Path("/sys/fs/resctrl")

# MBA bandwidth allocation schema format:
#   MB:0=<pct>;1=<pct>   (where pct is percentage, 0=min bandwidth)
# Note: some CPUs use MB/s mode. Check info/MB/ctrl for mode.
MBA_SCHEMA_FORMAT = "MB:{socket}={pct}"


class MBAActuator(ActuatorInterface):
    """
    Intel RDT MBA actuator (STUB — not yet implemented).

    On a supported Linux host this would:
      1. Detect and open the resctrl filesystem
      2. Create a CLOSID (class of service ID) for each suspect
      3. Write bandwidth limits to /sys/fs/resctrl/<closid>/schemata
      4. Associate suspect process/cgroup with the CLOSID
      5. Read back the value to verify the write
      6. On rollback: restore to MBA 100% (no throttle)

    Causal pathway:
      MBA throttle → reduced suspect memory bandwidth
      → less memory bus pressure on shared DRAM controller
      → victim's memory latency decreases
      → victim throughput increases

    MBA does NOT affect:
      - CPU scheduling or frequency (directly)
      - Cache allocation (use CAT for that)
      - Network or I/O

    This specificity is what allows the exclusion restriction claim:
    "The intervention (MBA) affects victim performance only via
    the memory subsystem." (Conditional on no confounders.)
    """

    ACTUATOR_TYPE = ActuatorType.MBA

    def __init__(
        self,
        dry_run: bool = True,
        resctrl_path: Path = RESCTRL_PATH,
        mba_granularity_pct: int = 10,   # typically 10% steps
    ) -> None:
        super().__init__(dry_run=dry_run)
        self.resctrl_path = resctrl_path
        self.mba_granularity_pct = mba_granularity_pct
        self._closids: Dict[str, str] = {}  # target_name → closid directory

    def is_available(self) -> bool:
        """Check if resctrl is mounted and MBA is supported."""
        if not self.resctrl_path.exists():
            return False
        mb_info = self.resctrl_path / "info" / "MB"
        return mb_info.exists()

    def get_capabilities(self) -> Dict[str, Any]:
        available = self.is_available()
        caps: Dict[str, Any] = {
            "type": self.ACTUATOR_TYPE.value,
            "is_available": available,
            "dry_run": self.dry_run,
            "resctrl_mounted": self.resctrl_path.exists(),
            "mba_info_present": False,
            "mba_granularity_pct": self.mba_granularity_pct,
            "note": "Stage 6 stub — not yet implemented",
        }
        if available:
            bw_gran = self.resctrl_path / "info" / "MB" / "bandwidth_gran"
            if bw_gran.exists():
                try:
                    caps["mba_granularity_pct"] = int(bw_gran.read_text().strip())
                except Exception:
                    pass
            caps["mba_info_present"] = True
        return caps

    def capture_baseline(self, target_names: List[str]) -> None:
        """
        Record baseline (MBA 100% = no throttle) for all targets.

        STUB: Real implementation would also pin processes to CLOSIDs.
        """
        for name in target_names:
            self._baselines[name] = 100   # 100% = full bandwidth
            self._closids[name] = f"agrda_{name}"
        logger.info(
            "MBAActuator: baseline captured (STUB) for %s. "
            "Real implementation requires root + resctrl.", target_names
        )

    def apply(self, name: str, dose: float) -> ActuatorResult:
        """
        Apply MBA throttle to the named target.

        dose=0.0 → MBA 100% (no throttle, full bandwidth)
        dose=1.0 → MBA 10% (maximum throttle, minimum bandwidth)

        MBA bandwidth value = round((1 - dose) * 100 / gran) * gran
        clamped to [mba_granularity_pct, 100].

        STUB: Returns an error — not implemented.
        """
        ok, msg = self.validate_dose(dose)
        if not ok:
            result = ActuatorResult(
                success=False, actuator_type=self.ACTUATOR_TYPE,
                target_name=name, requested_dose=dose, error=msg,
            )
            self._append_log(result.to_log_entry("apply"))
            return result

        # Convert dose to MBA percentage
        gran = self.mba_granularity_pct
        raw_pct = (1.0 - dose) * 100.0
        mba_pct = max(gran, round(raw_pct / gran) * gran)

        if self.dry_run:
            logger.info(
                "MBAActuator DRY RUN: would set %s MBA=%d%% (dose=%.2f)",
                name, mba_pct, dose,
            )
            result = ActuatorResult(
                success=True, actuator_type=self.ACTUATOR_TYPE,
                target_name=name, requested_dose=dose,
                applied_setting=mba_pct, readback_value=mba_pct,
                readback_matches=True,
                notes=[
                    f"DRY RUN: would write MBA={mba_pct}% to resctrl",
                    "STUB: real resctrl write not implemented",
                ],
            )
        else:
            # STUB: real implementation would write to resctrl
            error_msg = (
                "MBAActuator: real write NOT IMPLEMENTED (Stage 6 stub). "
                "Implement _write_mba_schema() before enabling real experiments."
            )
            logger.error(error_msg)
            result = ActuatorResult(
                success=False, actuator_type=self.ACTUATOR_TYPE,
                target_name=name, requested_dose=dose,
                applied_setting=None,
                error=error_msg,
            )

        self._append_log(result.to_log_entry("apply"))
        return result

    def rollback(self, name: str) -> ActuatorResult:
        """Restore MBA 100% (full bandwidth) for the named target."""
        baseline_pct = self._baselines.get(name, 100)
        if self.dry_run:
            result = ActuatorResult(
                success=True, actuator_type=self.ACTUATOR_TYPE,
                target_name=name, requested_dose=0.0,
                applied_setting=baseline_pct,
                readback_value=baseline_pct, readback_matches=True,
                notes=["DRY RUN rollback — STUB"],
            )
        else:
            result = ActuatorResult(
                success=False, actuator_type=self.ACTUATOR_TYPE,
                target_name=name, requested_dose=0.0,
                error="MBAActuator rollback: NOT IMPLEMENTED (Stage 6 stub)",
            )
        self._append_log(result.to_log_entry("rollback"))
        return result

    def rollback_all(self) -> List[ActuatorResult]:
        """Restore all targets. Must complete within 1 second."""
        return [self.rollback(name) for name in list(self._closids.keys())]

    def verify(self, name: str) -> Tuple[Any, bool]:
        """
        Read back current MBA setting from resctrl schemata.

        STUB: Returns (None, False) until implemented.
        """
        logger.warning("MBAActuator.verify(): STUB — not implemented")
        return (None, False)

    # ------------------------------------------------------------------
    # Internal helpers (to be implemented in Stage 6)
    # ------------------------------------------------------------------

    def _write_mba_schema(self, closid_dir: Path, mba_pct: int) -> None:
        """
        Write MBA bandwidth limit to resctrl schemata file.

        Format: MB:0=<pct>\n  (for single-socket; multi-socket needs all sockets)
        Must read back and verify within MBA granularity tolerance.

        NOT IMPLEMENTED — Stage 6.
        """
        raise NotImplementedError(
            "_write_mba_schema() not implemented. "
            "Stage 6: implement resctrl write + read-back."
        )

    def _read_mba_schema(self, closid_dir: Path) -> Optional[int]:
        """
        Read current MBA percentage from resctrl schemata.

        NOT IMPLEMENTED — Stage 6.
        """
        raise NotImplementedError("_read_mba_schema() not implemented.")
