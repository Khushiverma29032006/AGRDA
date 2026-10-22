"""
Telemetry module – measurement collection interface.

Available collectors:
  TelemetryCollector    – simulation backend (all platforms)
  PerfTelemetryCollector – perf_event hardware counters (Stage 6, Linux)
  MBMTelemetryCollector  – Intel RDT MBM bandwidth monitoring (Stage 6, Linux)
"""
from .models import MetricLabel, MeasurementRecord, TelemetryWindow
from .collector import TelemetryCollector
from .perf_collector import PerfTelemetryCollector, PerfWindow, PerfCounterReading
from .mbm_collector import MBMTelemetryCollector, MBMWindow, MBMReading

__all__ = [
    # Simulation
    "MetricLabel", "MeasurementRecord", "TelemetryWindow",
    "TelemetryCollector",
    # Stage 6 stubs
    "PerfTelemetryCollector", "PerfWindow", "PerfCounterReading",
    "MBMTelemetryCollector", "MBMWindow", "MBMReading",
]
