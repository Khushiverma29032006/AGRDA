"""
Data models for the experiment design.

DoseLevel    – a single named dose level (fractional throttle).
WindowSpec   – timing spec for one measurement window.
BlockSpec    – a block of randomised dose assignments for one suspect.
ExperimentDesign – full design for one experiment run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DoseLevel:
    """
    One dose level for one suspect.

    name    : human label, e.g. "baseline", "low", "medium", "high"
    dose    : fractional throttle [0.0 = baseline, 1.0 = maximum throttle]
    description: notes (e.g. "MBA 100%", "cpu.max 50%")
    """
    name: str
    dose: float
    description: str = ""


# Default 4-level dose set: baseline + 3 throttle levels
DEFAULT_DOSE_LEVELS = [
    DoseLevel("baseline", 0.0,  "No throttle — suspect at full speed"),
    DoseLevel("low",      0.20, "20% throttle"),
    DoseLevel("medium",   0.50, "50% throttle"),
    DoseLevel("high",     0.80, "80% throttle — near-maximum"),
]


@dataclass
class WindowSpec:
    """
    Timing specification for a single measurement window.

    All durations in seconds.
    Defaults from the AGRDA document: warmup=10, measure=30, washout=20.
    These are initial values only; replace with pilot settling-time results.
    """
    warmup_s: float = 10.0    # let system settle after dose change
    measure_s: float = 30.0   # collect measurements
    washout_s: float = 20.0   # recover before next dose
    # Jitter range applied to inter-window gaps (uniform ±jitter_s)
    jitter_s: float = 2.0

    @property
    def total_s(self) -> float:
        return self.warmup_s + self.measure_s + self.washout_s


@dataclass
class AssignedWindow:
    """
    One window in the randomised schedule.

    block_id    : which block this window belongs to
    window_id   : global sequential index
    suspect_name: which suspect is being throttled
    dose_level  : the DoseLevel applied
    jitter_s    : actual jitter (seconds) added to gap before this window
    assigned_at : creation timestamp (ISO 8601)
    """
    block_id: int
    window_id: int
    suspect_name: str
    dose_level: DoseLevel
    jitter_s: float = 0.0
    assigned_at: str = ""
    # Filled in during execution
    executed: bool = False
    excluded: bool = False
    exclusion_reason: str = ""
    actual_doses: Dict[str, float] = field(default_factory=dict)
    start_timestamp: str = ""
    end_timestamp: str = ""


@dataclass
class BlockSpec:
    """
    One block of randomised dose assignments for one suspect.

    Within a block, each dose level appears exactly once in random order.
    Blocking reduces the impact of slow drift (time trends) on estimates.
    """
    block_id: int
    suspect_name: str
    windows: List[AssignedWindow] = field(default_factory=list)


@dataclass
class ExperimentDesign:
    """
    Complete randomised blocked design for one experiment run.

    Stores the random seed and full assignment schedule so the run
    is replayable exactly.
    """
    design_id: str
    scenario_label: str
    seed: int
    suspect_names: List[str]
    dose_levels: List[DoseLevel]
    n_blocks: int              # how many repetitions of each dose per suspect
    window_spec: WindowSpec
    blocks: List[BlockSpec] = field(default_factory=list)
    design_notes: List[str] = field(default_factory=list)

    # Validation results
    rank_ok: bool = False
    rank_notes: List[str] = field(default_factory=list)

    # Metadata
    created_at: str = ""
    schema_version: str = "1.0"

    @property
    def all_windows(self) -> List[AssignedWindow]:
        windows = []
        for b in self.blocks:
            windows.extend(b.windows)
        return windows

    @property
    def n_total_windows(self) -> int:
        return sum(len(b.windows) for b in self.blocks)
