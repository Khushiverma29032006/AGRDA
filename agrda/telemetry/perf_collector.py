"""
perf_event telemetry collector — Stage 6 stub.

On a real Linux host this runs:
  perf stat -e mem_load_retired.l3_miss,cycles,instructions,...
           -p <suspect_pid> -- sleep <window_s>

and parses the output into structured metric records.

THIS IS A STUB — not implemented until Linux hardware is available.

Key metrics to collect (from AGRDA document):
  mem_load_retired.l3_miss   – LLC miss count (victim)
  cycles                     – reference CPU cycles
  instructions               – instruction count
  APERF / MPERF              – actual vs max performance frequency ratio
                               (detects CPU throttling for C5 alternative pathway)

Counter multiplexing:
  When more events are requested than the PMU has counters, the kernel
  multiplexes them. The running/enabled ratio must be recorded.
  Windows with running/enabled < 0.70 are flagged as LOW_QUALITY and
  excluded from primary analysis (contributes to C1 check).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SIMULATION_TAG = "SIMULATION"


@dataclass
class PerfCounterReading:
    """One counter reading from perf stat output."""
    event_name: str
    value: Optional[float]       # None if not counted
    unit: str = ""
    running_fraction: float = 1.0   # running/enabled (multiplexing ratio)
    quality_ok: bool = True
    missing_reason: str = ""


@dataclass
class PerfWindow:
    """All perf counters collected during one measurement window."""
    window_id: int
    pid: Optional[int]
    duration_s: float
    readings: List[PerfCounterReading] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    source: str = "REAL"

    def get(self, event_name: str) -> Optional[PerfCounterReading]:
        for r in self.readings:
            if r.event_name == event_name:
                return r
        return None

    def is_quality_ok(self, min_running_fraction: float = 0.70) -> bool:
        """Window is OK if all counters have sufficient running time."""
        if not self.readings:
            return False
        bad = [
            r for r in self.readings
            if r.value is not None and r.running_fraction < min_running_fraction
        ]
        return len(bad) == 0


# ---------------------------------------------------------------------------
# Events to collect
# ---------------------------------------------------------------------------

VICTIM_EVENTS = [
    "mem_load_retired.l3_miss",   # LLC miss count
    "cycles",
    "instructions",
]

PATHWAY_EVENTS = [
    "cpu-cycles",     # for APERF/MPERF ratio approximation
    "ref-cycles",
]

ALL_EVENTS = VICTIM_EVENTS + PATHWAY_EVENTS


# ---------------------------------------------------------------------------
# Collector (STUB)
# ---------------------------------------------------------------------------

class PerfTelemetryCollector:
    """
    Collects perf_event hardware counters via the `perf` CLI.

    STUB — not yet implemented. Raises NotImplementedError on real collection.

    When implemented:
      1. Check perf is available and counters are accessible
         (kernel.perf_event_paranoid must be <= 1)
      2. Run: perf stat -e <events> -p <pid> -- sleep <window_s>
      3. Parse stdout/stderr for counter values and running/enabled ratios
      4. Flag windows where running_fraction < 0.70 as LOW_QUALITY
      5. Return PerfWindow with all readings

    Counter multiplexing note (Section 4.3 of AGRDA document):
      When events > PMU hardware counters, the kernel time-slices them.
      The running/enabled ratio measures how much of the window each counter
      was actually counting. Low ratios increase measurement noise and can
      bias estimates. This is a threat to internal validity (C1 check).
    """

    def __init__(
        self,
        events: Optional[List[str]] = None,
        min_running_fraction: float = 0.70,
    ) -> None:
        self.events = events or ALL_EVENTS
        self.min_running_fraction = min_running_fraction

    def is_available(self) -> bool:
        """Check if perf is installed and usable."""
        if not shutil.which("perf"):
            return False
        # Quick test
        try:
            result = subprocess.run(
                ["perf", "stat", "-e", "cycles", "--", "true"],
                capture_output=True, text=True, timeout=5,
            )
            return "Permission denied" not in (result.stderr or "")
        except Exception:
            return False

    def collect_window(
        self,
        window_id: int,
        pid: int,
        duration_s: float,
    ) -> PerfWindow:
        """
        Collect all perf counters for one measurement window.

        STUB: raises NotImplementedError.

        Real implementation:
          cmd = ["perf", "stat", "-e", ",".join(self.events),
                 "-p", str(pid), "--", "sleep", str(duration_s)]
          result = subprocess.run(cmd, capture_output=True, text=True)
          return self._parse_perf_output(window_id, pid, duration_s,
                                         result.stderr)
        """
        raise NotImplementedError(
            "PerfTelemetryCollector.collect_window() not implemented. "
            "Stage 6: implement perf stat parsing."
        )

    def _parse_perf_output(
        self,
        window_id: int,
        pid: int,
        duration_s: float,
        perf_stderr: str,
    ) -> PerfWindow:
        """
        Parse `perf stat` stderr output into PerfWindow.

        perf stat output format (per counter line):
            <value> <unit> <event-name> # <comment> <running>% of <enabled>

        NOT IMPLEMENTED — Stage 6.
        """
        raise NotImplementedError("_parse_perf_output() not implemented.")
