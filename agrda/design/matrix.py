"""Blocked randomized schedules for simulation and experiment planning."""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

import numpy as np


DOSE_LEVELS = (0, 1, 2, 3)
PHASE_NAMES = ("warmup", "measurement", "washout")


@dataclass(frozen=True)
class DesignWindow:
    block_id: int
    window_id: int
    doses: Dict[str, int]
    phase_durations_s: Dict[str, float]
    gap_before_s: float = 0.0


@dataclass
class BlockedDesign:
    seed: int
    suspects: Tuple[str, ...]
    mode: str
    windows: List[DesignWindow]
    matrix_rank: int
    matrix_columns: int
    main_effects_identifiable: bool
    interaction_identifiable: bool
    notes: List[str] = field(default_factory=list)

    @property
    def all_windows(self) -> List[DesignWindow]:
        return self.windows


class BlockedRandomizationEngine:
    """Generate replayable blocked four-dose factorial or OAT schedules."""

    def __init__(
        self,
        suspects: Sequence[str] = ("S1", "S2"),
        *,
        n_blocks: int = 2,
        seed: int = 0,
        mode: str = "factorial",
        warmup_s: float = 10.0,
        measurement_s: float = 30.0,
        washout_s: float = 20.0,
        gap_s: float = 0.0,
        jitter_s: float = 0.0,
    ) -> None:
        self.suspects = tuple(suspects)
        if not self.suspects or len(set(self.suspects)) != len(self.suspects):
            raise ValueError("suspects must be non-empty and unique")
        if n_blocks < 1:
            raise ValueError("n_blocks must be at least 1")
        if mode not in ("factorial", "oat"):
            raise ValueError("mode must be 'factorial' or 'oat'")
        timings = (warmup_s, measurement_s, washout_s, gap_s, jitter_s)
        if any(not math.isfinite(value) or value < 0 for value in timings):
            raise ValueError("phase durations and gaps must be finite and non-negative")
        if measurement_s <= 0:
            raise ValueError("measurement_s must be greater than zero")
        if mode == "factorial" and len(DOSE_LEVELS) ** len(self.suspects) > 4096:
            raise ValueError("factorial design exceeds the 4096-cell limit")
        self.n_blocks = n_blocks
        self.seed = seed
        self.mode = mode
        self.phase_durations_s = {
            "warmup": float(warmup_s),
            "measurement": float(measurement_s),
            "washout": float(washout_s),
        }
        self.gap_s = float(gap_s)
        self.jitter_s = float(jitter_s)

    def create_design(self) -> BlockedDesign:
        rng = random.Random(self.seed)
        cells = self._factorial_cells() if self.mode == "factorial" else self._oat_cells()
        windows: List[DesignWindow] = []
        for block_id in range(self.n_blocks):
            block_cells = [dict(cell) for cell in cells]
            rng.shuffle(block_cells)
            for doses in block_cells:
                jitter = rng.uniform(-self.jitter_s, self.jitter_s)
                windows.append(DesignWindow(
                    block_id=block_id,
                    window_id=len(windows),
                    doses=doses,
                    phase_durations_s=dict(self.phase_durations_s),
                    gap_before_s=max(0.0, self.gap_s + jitter),
                ))

        matrix, main_columns = self._design_matrix(windows)
        rank = int(np.linalg.matrix_rank(matrix))
        main_rank = int(np.linalg.matrix_rank(matrix[:, :main_columns]))
        main_ok = main_rank == main_columns
        interaction_ok = rank == matrix.shape[1]
        notes = []
        if not main_ok:
            notes.append("Main-effect model is rank deficient")
        if self._pair_count() and not interaction_ok:
            notes.append("Pairwise interactions are not identifiable in this schedule")
        elif self._pair_count():
            notes.append("Pairwise interactions are identifiable by full matrix rank")
        else:
            notes.append("No pairwise interaction terms requested")
        return BlockedDesign(
            seed=self.seed,
            suspects=self.suspects,
            mode=self.mode,
            windows=windows,
            matrix_rank=rank,
            matrix_columns=matrix.shape[1],
            main_effects_identifiable=main_ok,
            interaction_identifiable=interaction_ok and bool(self._pair_count()),
            notes=notes,
        )

    def _factorial_cells(self) -> List[Dict[str, int]]:
        return [dict(zip(self.suspects, levels)) for levels in itertools.product(DOSE_LEVELS, repeat=len(self.suspects))]

    def _oat_cells(self) -> List[Dict[str, int]]:
        cells = []
        for suspect in self.suspects:
            for level in DOSE_LEVELS:
                cells.append({name: level if name == suspect else 0 for name in self.suspects})
        return cells

    def _pair_count(self) -> int:
        return len(self.suspects) * (len(self.suspects) - 1) // 2

    def _design_matrix(self, windows: Sequence[DesignWindow]) -> Tuple[np.ndarray, int]:
        block_ids = sorted({window.block_id for window in windows})
        rows = []
        for window in windows:
            row = [1.0]
            values = [float(window.doses[name]) for name in self.suspects]
            for value in values:
                row.extend((value, value * value))
            main_end = len(row)
            row.extend(values[left] * values[right]
                       for left in range(len(values))
                       for right in range(left + 1, len(values)))
            row.extend(float(window.block_id == block_id) for block_id in block_ids[1:])
            rows.append((row, main_end))
        matrix = np.asarray([row for row, _ in rows], dtype=float)
        main_columns = rows[0][1] + max(0, len(block_ids) - 1)
        if main_columns < matrix.shape[1]:
            main_matrix = np.column_stack((matrix[:, :rows[0][1]], matrix[:, rows[0][1] + self._pair_count():]))
            return np.column_stack((main_matrix, matrix[:, rows[0][1]:rows[0][1] + self._pair_count()])), main_matrix.shape[1]
        return matrix, main_columns