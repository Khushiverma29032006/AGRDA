"""
Memory Bandwidth Monitoring (MBM) telemetry collector — Stage 6 stub.

Reads from the Intel RDT resctrl monitoring data:
  /sys/fs/resctrl/mon_data/mon_L3_<socket>/mbm_total_bytes
  /sys/fs/resctrl/mon_data/mon_L3_<socket>/mbm_local_bytes

MBM gives a hardware-level count of memory bandwidth consumed by each
CLOSID (class of service), which directly measures the memory pathway
used by each suspect and the victim.

THIS IS A STUB — not implemented until Linux hardware with RDT is available.

Role in AGRDA:
  C2 (Intervention Strength):
    first-stage check: does MBA throttle actually reduce suspect bandwidth?
    → measure suspect_mbm_bw before and after dose change

  Primary mediator proxy (M in causal model):
    victim mem_stall_cpi correlates with victim MBM bandwidth
    → high victim bandwidth → high stall CPI → low throughput

Notes from AGRDA document:
  - MBM counters sample at a fixed rate; the read interval must be
    at least 1 second for stable readings.
  - total_bytes includes NUMA-local + NUMA-remote accesses.
  - local_bytes counts only local-node DRAM accesses.
  - For multi-socket systems, read all sockets and sum.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

RESCTRL_MON = Path("/sys/fs/resctrl/mon_data")


@dataclass
class MBMReading:
    """One MBM bandwidth reading for one CLOSID and socket."""
    closid: str             # e.g. "agrda_suspect_1"
    socket: int
    mbm_total_bytes: Optional[int]    # cumulative total bytes
    mbm_local_bytes: Optional[int]    # cumulative local bytes
    timestamp_ns: int = 0
    missing: bool = False
    missing_reason: str = ""


@dataclass
class MBMWindow:
    """
    Memory bandwidth delta over one measurement window.

    Computed as: (end_reading - start_reading) / window_duration_s
    Gives bandwidth in bytes/sec (convert to GB/s: / 1e9).
    """
    window_id: int
    closid: str
    duration_s: float
    mbm_total_gbps: Optional[float]   # total bandwidth GB/s
    mbm_local_gbps: Optional[float]   # local bandwidth GB/s
    source: str = "REAL"
    errors: List[str] = field(default_factory=list)


class MBMTelemetryCollector:
    """
    Reads Intel RDT MBM bandwidth counters from resctrl.

    STUB — not yet implemented.

    Usage pattern (when implemented):
      collector = MBMTelemetryCollector()
      start = collector.read_all_closids()
      # ... run workload window ...
      end = collector.read_all_closids()
      bw = collector.compute_bandwidth(start, end, window_s)

    Resctrl layout:
      /sys/fs/resctrl/
        mon_data/
          mon_L3_00/          (socket 0)
            mbm_total_bytes   (cumulative total BW counter)
            mbm_local_bytes   (cumulative local BW counter)
        <closid>/
          mon_data/
            mon_L3_00/        (per-CLOSID counters)
              mbm_total_bytes
              mbm_local_bytes
    """

    def __init__(
        self,
        resctrl_path: Path = Path("/sys/fs/resctrl"),
    ) -> None:
        self.resctrl_path = resctrl_path

    def is_available(self) -> bool:
        """Check if resctrl MBM monitoring is available."""
        mon = self.resctrl_path / "mon_data"
        return mon.exists()

    def read_closid(self, closid: str, socket: int = 0) -> MBMReading:
        """
        Read current MBM counter values for one CLOSID.

        STUB: raises NotImplementedError.

        Real implementation:
          closid_mon = self.resctrl_path / closid / "mon_data" / f"mon_L3_{socket:02d}"
          total = int((closid_mon / "mbm_total_bytes").read_text().strip())
          local = int((closid_mon / "mbm_local_bytes").read_text().strip())
          return MBMReading(closid=closid, socket=socket,
                            mbm_total_bytes=total, mbm_local_bytes=local)
        """
        raise NotImplementedError(
            "MBMTelemetryCollector.read_closid() not implemented. "
            "Stage 6: implement resctrl MBM reads."
        )

    def read_all_closids(
        self, closids: List[str], n_sockets: int = 1,
    ) -> Dict[str, List[MBMReading]]:
        """
        Read MBM counters for all CLOSIDs across all sockets.

        Returns: {closid: [MBMReading per socket]}

        STUB: raises NotImplementedError.
        """
        raise NotImplementedError(
            "MBMTelemetryCollector.read_all_closids() not implemented."
        )

    def compute_bandwidth(
        self,
        start: Dict[str, List[MBMReading]],
        end: Dict[str, List[MBMReading]],
        duration_s: float,
        window_id: int = 0,
    ) -> Dict[str, MBMWindow]:
        """
        Compute bandwidth from start/end counter snapshots.

        bandwidth_gbps = (end_total - start_total) / duration_s / 1e9

        STUB: raises NotImplementedError.
        """
        raise NotImplementedError(
            "MBMTelemetryCollector.compute_bandwidth() not implemented."
        )
