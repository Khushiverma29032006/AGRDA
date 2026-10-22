"""
Data models for capability detection results.

These are plain dataclasses / enums that represent what the
host machine supports.  They carry no logic.

Exported classes:
  OSType, CgroupVersion, RDTSupport, PerfSupport  – classification enums
  CapabilityLevel                                  – overall experiment level
  CPUTopology                                      – CPU topology summary
  RDTDetails                                       – Intel RDT feature flags
  EBPFSupport                                      – eBPF/BPF tracing flags
  HostCapabilitySnapshot                           – lightweight aggregate
  ConfigThresholds                                 – configurable thresholds
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class OSType(enum.Enum):
    """Broad OS family classification."""
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    UNKNOWN = "unknown"


class CgroupVersion(enum.Enum):
    """Linux cgroup hierarchy version."""
    NONE = "none"          # Not Linux or cgroup fs not mounted
    V1 = "v1"
    V2 = "v2"
    UNKNOWN = "unknown"


class RDTSupport(enum.Enum):
    """Intel Resource Director Technology availability."""
    NOT_AVAILABLE = "not_available"  # Not Intel, no RDT, or not Linux
    PARTIAL = "partial"              # Some features missing
    FULL = "full"                    # MBA + MBM + CMT all present
    UNKNOWN = "unknown"


class PerfSupport(enum.Enum):
    """perf_event / perf tool availability."""
    NOT_AVAILABLE = "not_available"
    AVAILABLE_LIMITED = "available_limited"   # Some counters available
    AVAILABLE_FULL = "available_full"
    UNKNOWN = "unknown"


class CapabilityLevel(enum.Enum):
    """
    Overall experiment capability level for the host.

    SIMULATION_ONLY: Must use synthetic backends (e.g., Windows, macOS,
                     VMs without required kernel features).
    PARTIAL_REAL:    Some real hardware features available but incomplete.
    FULL_REAL:       All required features detected; real experiments
                     are possible after explicit confirmation.
    """
    SIMULATION_ONLY = "simulation_only"
    PARTIAL_REAL = "partial_real"
    FULL_REAL = "full_real"


@dataclass
class CPUTopology:
    """CPU topology summary."""
    manufacturer: str = "UNKNOWN"
    model_name: str = "UNKNOWN"
    physical_cores: int = 0
    logical_processors: int = 0
    sockets: int = 1
    numa_nodes: int = 1
    smt_enabled: Optional[bool] = None  # None = could not determine
    max_freq_mhz: int = 0
    # Whether the CPU is Intel (RDT requires Intel)
    is_intel: bool = False
    # Notes about topology detection limits
    topology_notes: List[str] = field(default_factory=list)


@dataclass
class RDTDetails:
    """Fine-grained Intel RDT feature availability."""
    resctrl_mounted: bool = False
    cat_supported: bool = False    # Cache Allocation Technology
    cmt_supported: bool = False    # Cache Monitoring Technology
    mba_supported: bool = False    # Memory Bandwidth Allocation
    mbm_total: bool = False        # Memory Bandwidth Monitoring (total)
    mbm_local: bool = False        # Memory Bandwidth Monitoring (local)
    mba_mbps_mode: bool = False    # MBA in MB/s mode (vs. percentage)
    mba_granularity_pct: Optional[int] = None  # Finest controllable step %
    notes: List[str] = field(default_factory=list)


@dataclass
class EBPFSupport:
    """eBPF/BPF tracing availability."""
    available: bool = False
    bpftool_found: bool = False
    notes: List[str] = field(default_factory=list)


@dataclass
class ConfigThresholds:
    """
    Configurable thresholds for the audit battery and decision policy.

    These start as initial values from the AGRDA document and must be
    FROZEN (written to a versioned config file) before confirmatory runs.
    Tuning thresholds on confirmatory results violates the research design.

    Status values:
      "UNFROZEN" – defaults only, pilot not yet run
      "FROZEN"   – locked after pilot, cannot change without new version
    """
    # Core decision / audit thresholds
    delta_eff: float = 0.05          # relevance threshold (5% of baseline)
    delta_eq: float = 0.0125         # equivalence margin (25% of Delta_eff)
    kappa: float = 0.25              # mediator-to-outcome ratio threshold
    icc_min: float = 0.60            # further lowered ICC threshold for simulation
    first_stage_min: float = 0.10    # minimum first-stage effect for C2

    # Freeze tracking
    status: str = "UNFROZEN"         # "UNFROZEN" | "FROZEN"
    frozen_at: Optional[str] = None  # ISO timestamp when frozen
    pilot_run_id: Optional[str] = None  # pilot run that established these
    schema_version: str = "1.0"
    notes: List[str] = field(default_factory=list)

    def is_frozen(self) -> bool:
        return self.status == "FROZEN"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delta_eff": self.delta_eff,
            "delta_eq": self.delta_eq,
            "kappa": self.kappa,
            "icc_min": self.icc_min,
            "first_stage_min": self.first_stage_min,
            "status": self.status,
            "frozen_at": self.frozen_at,
            "pilot_run_id": self.pilot_run_id,
            "schema_version": self.schema_version,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ConfigThresholds":
        return cls(
            delta_eff=d.get("delta_eff", 0.05),
            delta_eq=d.get("delta_eq", 0.0125),
            kappa=d.get("kappa", 0.25),
            icc_min=d.get("icc_min", 0.60),
            first_stage_min=d.get("first_stage_min", 0.10),
            status=d.get("status", "UNFROZEN"),
            frozen_at=d.get("frozen_at"),
            pilot_run_id=d.get("pilot_run_id"),
            schema_version=d.get("schema_version", "1.0"),
            notes=d.get("notes", []),
        )


@dataclass
class HostCapabilitySnapshot:
    """
    Lightweight capability snapshot used in run records and reports.

    This is a simplified, JSON-serialisable summary of CapabilityReport.
    It is embedded in RunRecord so every saved run knows the host's level
    when the experiment was executed.

    Note: For the full structured report (including RDTDetails, EBPFSupport,
    etc.) use CapabilityReport from agrda.capabilities.detector.
    """
    os_type: str = OSType.UNKNOWN.value
    kernel_version: str = "UNKNOWN"
    cpu_model: str = "UNKNOWN"
    physical_cores: int = 0
    logical_processors: int = 0
    is_intel: bool = False
    total_ram_mb: int = 0
    cgroup_version: str = CgroupVersion.NONE.value
    rdt_support: str = RDTSupport.NOT_AVAILABLE.value
    perf_support: str = PerfSupport.NOT_AVAILABLE.value
    ebpf_available: bool = False
    docker_found: bool = False
    podman_found: bool = False
    level: str = CapabilityLevel.SIMULATION_ONLY.value
    unsupported_requirements: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "os_type": self.os_type,
            "kernel_version": self.kernel_version,
            "cpu_model": self.cpu_model,
            "physical_cores": self.physical_cores,
            "logical_processors": self.logical_processors,
            "is_intel": self.is_intel,
            "total_ram_mb": self.total_ram_mb,
            "cgroup_version": self.cgroup_version,
            "rdt_support": self.rdt_support,
            "perf_support": self.perf_support,
            "ebpf_available": self.ebpf_available,
            "docker_found": self.docker_found,
            "podman_found": self.podman_found,
            "level": self.level,
            "unsupported_requirements": self.unsupported_requirements,
        }

    @classmethod
    def from_capability_report(cls, report: Any) -> "HostCapabilitySnapshot":
        """
        Build a snapshot from a full CapabilityReport object.
        Import is deferred to avoid circular imports.
        """
        return cls(
            os_type=report.os_type,
            kernel_version=report.kernel_version,
            cpu_model=report.cpu.model_name,
            physical_cores=report.cpu.physical_cores,
            logical_processors=report.cpu.logical_processors,
            is_intel=report.cpu.is_intel,
            total_ram_mb=report.total_ram_mb,
            cgroup_version=report.cgroup_version,
            rdt_support=report.rdt_support,
            perf_support=report.perf_support,
            ebpf_available=report.ebpf.available,
            docker_found=report.docker_found,
            podman_found=report.podman_found,
            level=report.level,
            unsupported_requirements=list(report.unsupported_requirements),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "OSType",
    "CgroupVersion",
    "RDTSupport",
    "PerfSupport",
    "CapabilityLevel",
    # Dataclasses
    "CPUTopology",
    "RDTDetails",
    "EBPFSupport",
    "ConfigThresholds",
    "HostCapabilitySnapshot",
]
