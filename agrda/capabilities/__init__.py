"""Capability detection module for AGRDA."""
from .detector import CapabilityDetector, CapabilityReport
from .models import (
    OSType, CgroupVersion, RDTSupport, PerfSupport,
    CPUTopology, RDTDetails, EBPFSupport,
    CapabilityLevel,
    ConfigThresholds, HostCapabilitySnapshot,
)

__all__ = [
    "CapabilityDetector", "CapabilityReport",
    "OSType", "CgroupVersion", "RDTSupport", "PerfSupport",
    "CPUTopology", "RDTDetails", "EBPFSupport",
    "CapabilityLevel",
    "ConfigThresholds", "HostCapabilitySnapshot",
]
