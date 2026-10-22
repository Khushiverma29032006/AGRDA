"""
Actuator module – all actuator backends.

Available actuators:
  SimActuator      – in-memory simulation (default on all platforms)
  MBAActuator      – Intel RDT MBA via resctrl (Stage 6 stub, Linux only)
  CpuQuotaActuator – cgroup v2 cpu.max (Stage 6 stub, Linux only)

Watchdog:
  Watchdog – background thread that rolls back all doses on timeout or error.

IMPORTANT: MBA and CPU-quota are DIFFERENT actuators with DIFFERENT causal
pathways. Never substitute one for the other.
"""
from .interface import (
    ActuatorInterface, ActuatorType, ActuatorResult, ActuatorLogEntry,
)
from .sim_actuator import SimActuator
from .watchdog import Watchdog
from .mba_actuator import MBAActuator
from .cpuquota_actuator import CpuQuotaActuator

__all__ = [
    "ActuatorInterface", "ActuatorType", "ActuatorResult", "ActuatorLogEntry",
    "SimActuator", "Watchdog",
    "MBAActuator", "CpuQuotaActuator",
]
