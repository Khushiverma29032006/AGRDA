"""
AGRDA: Audit-Gated Randomized Dose Attribution of Memory-Subsystem
Interference Among Co-located Containers.

Research Prototype – Simulation / Demo Mode.

IMPORTANT: This software is a research prototype. No experimental
results exist yet. All simulated results are clearly marked
SIMULATION – NOT A MEASURED RESULT.

Package structure:
  agrda.capabilities   – read-only host capability detection
  agrda.workloads      – workload management (simulation backend)
  agrda.actuators      – actuator abstraction (SimActuator + Stage 6 stubs)
  agrda.design         – blocked randomized experiment designer
  agrda.telemetry      – measurement collection (simulation + Stage 6 stubs)
  agrda.estimation     – effect estimation (OLS + bootstrap + permutation)
  agrda.audit          – nine-check audit battery (C1–C9)
  agrda.decision       – five-outcome decision policy (O1–O5)
  agrda.scenarios      – simulation scenarios S0–S9
  agrda.baselines      – baseline comparators B1–B4
  agrda.storage        – run data storage (JSON + CSV)
  agrda.reporting      – report generation
  agrda.runner         – experiment pipeline orchestrator
  agrda.config_loader  – TOML configuration loader
  agrda.cli            – command-line interface
"""

__version__ = "0.1.0-simulation"
__author__ = "AGRDA Research Prototype"

# Expose config loader at the top level for convenience
from .config_loader import load_config, get_thresholds

__all__ = [
    "__version__",
    "__author__",
    "load_config",
    "get_thresholds",
]
