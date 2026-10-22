"""
Capability detector – read-only inspection of the host.

Design principle: NEVER changes system settings.
All checks are passive reads.  Any failure to read a file or run a
command is recorded as UNKNOWN rather than raising an exception.

Implementation decision: We use platform.system() and subprocess
to inspect the host.  On Windows we detect absence of Linux-required
features and immediately set CapabilityLevel.SIMULATION_ONLY so that
no real actuators can be enabled.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    CPUTopology, CgroupVersion, CapabilityLevel, EBPFSupport,
    OSType, PerfSupport, RDTDetails, RDTSupport,
)


# ---------------------------------------------------------------------------
# Top-level capability report
# ---------------------------------------------------------------------------

@dataclass
class CapabilityReport:
    """
    Complete capability snapshot of the host machine.

    Each field is either a concrete value or clearly labelled UNKNOWN.
    The overall 'level' tells the rest of the system which backend to use.
    """
    # --- identity ---
    report_timestamp: str = ""
    hostname: str = "UNKNOWN"
    schema_version: str = "1.0"

    # --- OS ---
    os_type: str = OSType.UNKNOWN.value
    os_name: str = "UNKNOWN"
    os_version: str = "UNKNOWN"
    kernel_version: str = "UNKNOWN"

    # --- CPU ---
    cpu: CPUTopology = field(default_factory=CPUTopology)

    # --- Memory ---
    total_ram_mb: int = 0

    # --- Linux-specific ---
    cgroup_version: str = CgroupVersion.NONE.value
    cgroup_mount: str = "N/A"
    rdt_support: str = RDTSupport.NOT_AVAILABLE.value
    rdt_details: RDTDetails = field(default_factory=RDTDetails)
    perf_support: str = PerfSupport.NOT_AVAILABLE.value
    perf_notes: List[str] = field(default_factory=list)
    ebpf: EBPFSupport = field(default_factory=EBPFSupport)

    # --- Container runtime ---
    docker_found: bool = False
    podman_found: bool = False
    container_runtime_notes: List[str] = field(default_factory=list)

    # --- Overall level ---
    level: str = CapabilityLevel.SIMULATION_ONLY.value

    # --- Human-readable summary lines ---
    summary_lines: List[str] = field(default_factory=list)

    # --- Unsupported requirements ---
    unsupported_requirements: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def human_summary(self) -> str:
        lines = [
            "=" * 60,
            "AGRDA Capability Report",
            f"  Host     : {self.hostname}",
            f"  Timestamp: {self.report_timestamp}",
            f"  OS       : {self.os_name} {self.os_version}",
            f"  Kernel   : {self.kernel_version}",
            f"  CPU      : {self.cpu.model_name}",
            f"             {self.cpu.physical_cores} cores / "
            f"{self.cpu.logical_processors} logical / "
            f"{self.cpu.max_freq_mhz} MHz",
            f"  RAM      : {self.total_ram_mb} MB",
            "-" * 60,
            f"  cgroup   : {self.cgroup_version}",
            f"  RDT/MBA  : {self.rdt_support}",
            f"  perf     : {self.perf_support}",
            f"  eBPF     : {'yes' if self.ebpf.available else 'no'}",
            f"  Docker   : {'yes' if self.docker_found else 'no'}",
            f"  Podman   : {'yes' if self.podman_found else 'no'}",
            "-" * 60,
            f"  LEVEL    : {self.level}",
            "-" * 60,
        ]
        if self.unsupported_requirements:
            lines.append("  Unsupported requirements:")
            for u in self.unsupported_requirements:
                lines.append(f"    - {u}")
            lines.append("-" * 60)
        for sl in self.summary_lines:
            lines.append(f"  {sl}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class CapabilityDetector:
    """
    Read-only host capability inspector.

    Usage:
        detector = CapabilityDetector()
        report = detector.detect()
        print(report.human_summary())
    """

    def detect(self) -> CapabilityReport:
        """Run all read-only capability checks and return a CapabilityReport."""
        report = CapabilityReport()
        report.report_timestamp = datetime.now(timezone.utc).isoformat()
        report.hostname = self._safe_hostname()

        self._detect_os(report)
        self._detect_cpu(report)
        self._detect_ram(report)
        self._detect_linux_features(report)
        self._detect_container_runtime(report)
        self._determine_level(report)
        self._build_summary(report)
        return report

    # ------------------------------------------------------------------
    # OS
    # ------------------------------------------------------------------

    def _detect_os(self, r: CapabilityReport) -> None:
        system = platform.system().lower()
        if system == "linux":
            r.os_type = OSType.LINUX.value
        elif system == "windows":
            r.os_type = OSType.WINDOWS.value
        elif system == "darwin":
            r.os_type = OSType.MACOS.value
        else:
            r.os_type = OSType.UNKNOWN.value

        r.os_name = platform.system()
        r.os_version = platform.version()
        r.kernel_version = platform.release()

    # ------------------------------------------------------------------
    # CPU
    # ------------------------------------------------------------------

    def _detect_cpu(self, r: CapabilityReport) -> None:
        topo = CPUTopology()

        # Cross-platform basics via Python stdlib
        import multiprocessing
        topo.logical_processors = multiprocessing.cpu_count()

        # Try platform-specific enrichment
        if r.os_type == OSType.LINUX.value:
            self._detect_cpu_linux(topo)
        elif r.os_type == OSType.WINDOWS.value:
            self._detect_cpu_windows(topo)

        topo.is_intel = "intel" in topo.manufacturer.lower() or \
                        "intel" in topo.model_name.lower()
        r.cpu = topo

    def _detect_cpu_linux(self, topo: CPUTopology) -> None:
        """Parse /proc/cpuinfo on Linux."""
        cpuinfo_path = Path("/proc/cpuinfo")
        if not cpuinfo_path.exists():
            topo.topology_notes.append("/proc/cpuinfo not found")
            return
        try:
            text = cpuinfo_path.read_text()
            # manufacturer
            m = re.search(r"vendor_id\s*:\s*(.+)", text)
            if m:
                topo.manufacturer = m.group(1).strip()
            # model
            m = re.search(r"model name\s*:\s*(.+)", text)
            if m:
                topo.model_name = m.group(1).strip()
            # max freq
            m = re.search(r"cpu MHz\s*:\s*([\d.]+)", text)
            if m:
                topo.max_freq_mhz = int(float(m.group(1)))
            # physical cores
            core_ids = set(re.findall(r"core id\s*:\s*(\d+)", text))
            if core_ids:
                topo.physical_cores = len(core_ids)
            else:
                topo.physical_cores = topo.logical_processors
            # SMT
            if topo.physical_cores > 0:
                topo.smt_enabled = (
                    topo.logical_processors > topo.physical_cores
                )
            # NUMA nodes
            numa_out = self._run_cmd(["lscpu"])
            if numa_out:
                m = re.search(r"NUMA node\(s\):\s*(\d+)", numa_out)
                if m:
                    topo.numa_nodes = int(m.group(1))
        except Exception as e:
            topo.topology_notes.append(f"cpuinfo parse error: {e}")

    def _detect_cpu_windows(self, topo: CPUTopology) -> None:
        """Use WMIC on Windows to get CPU info."""
        out = self._run_cmd([
            "powershell", "-Command",
            "Get-WmiObject -Class Win32_Processor | "
            "Select-Object Name, NumberOfCores, "
            "NumberOfLogicalProcessors, MaxClockSpeed, Manufacturer | "
            "ConvertTo-Json"
        ])
        if out:
            try:
                # WMIC may return a list or a single object
                data = json.loads(out)
                if isinstance(data, list):
                    data = data[0]
                topo.model_name = data.get("Name", "UNKNOWN")
                topo.manufacturer = data.get("Manufacturer", "UNKNOWN")
                topo.physical_cores = int(data.get("NumberOfCores", 0))
                topo.logical_processors = int(
                    data.get("NumberOfLogicalProcessors", 0)
                )
                topo.max_freq_mhz = int(data.get("MaxClockSpeed", 0))
                if topo.physical_cores > 0:
                    topo.smt_enabled = (
                        topo.logical_processors > topo.physical_cores
                    )
            except Exception as e:
                topo.topology_notes.append(f"WMI parse error: {e}")

    # ------------------------------------------------------------------
    # RAM
    # ------------------------------------------------------------------

    def _detect_ram(self, r: CapabilityReport) -> None:
        try:
            import psutil
            r.total_ram_mb = int(psutil.virtual_memory().total / (1024 * 1024))
        except ImportError:
            # Fallback: platform-specific
            if r.os_type == OSType.LINUX.value:
                try:
                    mem = Path("/proc/meminfo").read_text()
                    m = re.search(r"MemTotal:\s*(\d+)", mem)
                    if m:
                        r.total_ram_mb = int(m.group(1)) // 1024
                except Exception:
                    pass
            elif r.os_type == OSType.WINDOWS.value:
                out = self._run_cmd([
                    "powershell", "-Command",
                    "(Get-WmiObject Win32_ComputerSystem).TotalPhysicalMemory"
                ])
                if out:
                    try:
                        r.total_ram_mb = int(out.strip()) // (1024 * 1024)
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Linux-specific features
    # ------------------------------------------------------------------

    def _detect_linux_features(self, r: CapabilityReport) -> None:
        """Detect cgroup, RDT, perf, eBPF — only meaningful on Linux."""
        if r.os_type != OSType.LINUX.value:
            r.cgroup_version = CgroupVersion.NONE.value
            r.rdt_support = RDTSupport.NOT_AVAILABLE.value
            r.perf_support = PerfSupport.NOT_AVAILABLE.value
            r.unsupported_requirements.extend([
                "Linux cgroup v2 (cpu.max, memory) — not Linux",
                "Intel RDT/resctrl (MBA, MBM, CMT) — not Linux",
                "perf_event hardware counters — not Linux",
                "eBPF/scheduler tracing — not Linux",
            ])
            return

        self._detect_cgroup(r)
        self._detect_rdt(r)
        self._detect_perf(r)
        self._detect_ebpf(r)

    def _detect_cgroup(self, r: CapabilityReport) -> None:
        """Check /proc/mounts for cgroup2."""
        try:
            mounts = Path("/proc/mounts").read_text()
        except Exception:
            r.cgroup_version = CgroupVersion.UNKNOWN.value
            return

        if "cgroup2" in mounts:
            r.cgroup_version = CgroupVersion.V2.value
            # Find mount point
            for line in mounts.splitlines():
                if "cgroup2" in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        r.cgroup_mount = parts[1]
                    break
        elif "cgroup" in mounts:
            r.cgroup_version = CgroupVersion.V1.value
            r.unsupported_requirements.append(
                "cgroup v2 required for cpu.max; v1 detected"
            )
        else:
            r.cgroup_version = CgroupVersion.NONE.value
            r.unsupported_requirements.append(
                "No cgroup filesystem mounted"
            )

    def _detect_rdt(self, r: CapabilityReport) -> None:
        """Check /sys/fs/resctrl for Intel RDT features."""
        details = RDTDetails()
        resctrl = Path("/sys/fs/resctrl")

        if not resctrl.exists():
            r.rdt_support = RDTSupport.NOT_AVAILABLE.value
            r.rdt_details = details
            r.unsupported_requirements.append(
                "Intel RDT/resctrl not mounted at /sys/fs/resctrl"
            )
            return

        details.resctrl_mounted = True

        # Read info directory
        info_dir = resctrl / "info"
        if (info_dir / "MB").exists():
            details.mba_supported = True
            # Check granularity
            bw_gran = info_dir / "MB" / "bandwidth_gran"
            if bw_gran.exists():
                try:
                    details.mba_granularity_pct = int(
                        bw_gran.read_text().strip()
                    )
                except Exception:
                    pass
            # MBps mode
            ctrl = info_dir / "MB" / "ctrl"
            if ctrl.exists():
                details.mba_mbps_mode = "MB/s" in ctrl.read_text()

        if (info_dir / "L3").exists():
            details.cat_supported = True
        if (info_dir / "L3_MON").exists():
            details.cmt_supported = True
            details.mbm_total = (
                info_dir / "L3_MON" / "mon_features"
            ).exists()
            details.mbm_local = details.mbm_total  # both come together

        r.rdt_details = details
        if details.mba_supported and details.mbm_total:
            r.rdt_support = RDTSupport.FULL.value
        elif details.resctrl_mounted:
            r.rdt_support = RDTSupport.PARTIAL.value
            r.unsupported_requirements.append(
                "Intel RDT partial: MBA or MBM features missing"
            )
        else:
            r.rdt_support = RDTSupport.NOT_AVAILABLE.value

    def _detect_perf(self, r: CapabilityReport) -> None:
        """Check if perf tool and hardware events are accessible."""
        if not shutil.which("perf"):
            r.perf_support = PerfSupport.NOT_AVAILABLE.value
            r.perf_notes.append("perf binary not found in PATH")
            r.unsupported_requirements.append("perf tool not found in PATH")
            return

        # Try a quick dry-run perf stat
        out = self._run_cmd(
            ["perf", "stat", "-e", "cycles", "--", "true"],
            capture_stderr=True,
        )
        if out is None:
            r.perf_support = PerfSupport.UNKNOWN.value
            r.perf_notes.append("perf stat returned no output")
        elif "Permission denied" in out or "EACCES" in out:
            r.perf_support = PerfSupport.AVAILABLE_LIMITED.value
            r.perf_notes.append(
                "perf permission denied; check kernel.perf_event_paranoid"
            )
            r.unsupported_requirements.append(
                "perf: permission denied (kernel.perf_event_paranoid too high)"
            )
        else:
            r.perf_support = PerfSupport.AVAILABLE_FULL.value
            r.perf_notes.append("perf stat appears functional")

    def _detect_ebpf(self, r: CapabilityReport) -> None:
        ebpf = EBPFSupport()
        ebpf.bpftool_found = shutil.which("bpftool") is not None
        # Check bpf syscall via capability
        bpf_sys = Path("/proc/sys/kernel/unprivileged_bpf_disabled")
        if bpf_sys.exists():
            try:
                val = bpf_sys.read_text().strip()
                ebpf.available = (val == "0")
                if not ebpf.available:
                    ebpf.notes.append(
                        "unprivileged_bpf_disabled=1; root required for eBPF"
                    )
            except Exception:
                ebpf.notes.append("Could not read bpf_disabled flag")
        r.ebpf = ebpf

    # ------------------------------------------------------------------
    # Container runtime
    # ------------------------------------------------------------------

    def _detect_container_runtime(self, r: CapabilityReport) -> None:
        r.docker_found = shutil.which("docker") is not None
        r.podman_found = shutil.which("podman") is not None
        if not r.docker_found and not r.podman_found:
            r.container_runtime_notes.append(
                "Neither docker nor podman found in PATH; "
                "workload containers will use simulation mode."
            )
            r.unsupported_requirements.append(
                "Container runtime (docker/podman) not found"
            )

    # ------------------------------------------------------------------
    # Overall level determination
    # ------------------------------------------------------------------

    def _determine_level(self, r: CapabilityReport) -> None:
        """
        Determine CapabilityLevel.

        SIMULATION_ONLY:
          - Not Linux, OR
          - cgroup v2 missing, OR
          - RDT not available.

        PARTIAL_REAL:
          - Linux + cgroup v2 present, but RDT incomplete or perf limited.

        FULL_REAL:
          - Linux + cgroup v2 + full RDT (MBA+MBM) + perf full.
        """
        if r.os_type != OSType.LINUX.value:
            r.level = CapabilityLevel.SIMULATION_ONLY.value
            return

        has_cgv2 = r.cgroup_version == CgroupVersion.V2.value
        has_rdt_full = r.rdt_support == RDTSupport.FULL.value
        has_perf_full = r.perf_support == PerfSupport.AVAILABLE_FULL.value

        if has_cgv2 and has_rdt_full and has_perf_full:
            r.level = CapabilityLevel.FULL_REAL.value
        elif has_cgv2:
            r.level = CapabilityLevel.PARTIAL_REAL.value
        else:
            r.level = CapabilityLevel.SIMULATION_ONLY.value

    # ------------------------------------------------------------------
    # Summary builder
    # ------------------------------------------------------------------

    def _build_summary(self, r: CapabilityReport) -> None:
        r.summary_lines = []
        if r.level == CapabilityLevel.SIMULATION_ONLY.value:
            r.summary_lines.append(
                "This host is SIMULATION-ONLY. Real actuators are disabled."
            )
            r.summary_lines.append(
                "All experiments will use synthetic/mock backends."
            )
            r.summary_lines.append(
                "Results will be labelled SIMULATION – NOT A MEASURED RESULT."
            )
        elif r.level == CapabilityLevel.PARTIAL_REAL.value:
            r.summary_lines.append(
                "Partial real-hardware support detected."
            )
            r.summary_lines.append(
                "Some features unavailable; confirm before real experiments."
            )
        else:
            r.summary_lines.append(
                "Full real-hardware support detected."
            )
            r.summary_lines.append(
                "Explicit operator confirmation required before any actuation."
            )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_hostname() -> str:
        try:
            import socket
            return socket.gethostname()
        except Exception:
            return "UNKNOWN"

    @staticmethod
    def _run_cmd(
        cmd: List[str],
        capture_stderr: bool = False,
        timeout: int = 5,
    ) -> Optional[str]:
        """
        Run a read-only command and return combined stdout (and optionally
        stderr) as a string.  Returns None on failure.
        """
        try:
            stderr = subprocess.STDOUT if capture_stderr else subprocess.DEVNULL
            result = subprocess.run(
                cmd,
                capture_output=not capture_stderr,
                stderr=stderr if capture_stderr else subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture_stderr else subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            return result.stdout or ""
        except Exception:
            return None
