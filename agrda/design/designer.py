"""
Experiment designer – creates and validates blocked randomised designs.

Key design decisions (documented as implementation choices):
  1. One-at-a-time (OAT) design for K ≤ 3 suspects.
     We throttle one suspect per window while others run at baseline.
     This allows identification of main effects but not interactions.
  2. For interaction estimation we need factorial designs, but with
     K=3 and 4 doses each (4^3 = 64 cells), a full factorial is large.
     We do NOT implement factorial by default; we flag interaction
     estimates as NOT IDENTIFIED and refuse to report them.
  3. Design-matrix rank is checked; experiments are rejected if
     the primary model cannot be fit.
  4. Random seed stored and schedule is replayable.
  5. Jitter applied to inter-window gaps to reduce phase alignment.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np

from .models import (
    AssignedWindow, BlockSpec, DoseLevel, ExperimentDesign,
    WindowSpec, DEFAULT_DOSE_LEVELS,
)


class ExperimentDesigner:
    """
    Creates blocked randomised designs for AGRDA experiments.

    Parameters
    ----------
    suspect_names : list of suspect identifiers (e.g. ['suspect_1', 'suspect_2'])
    dose_levels   : DoseLevel list; defaults to the 4-level set
    n_blocks      : repetitions of each dose per suspect (≥3 recommended)
    window_spec   : warmup / measure / washout timing
    seed          : random seed for reproducibility
    """

    def __init__(
        self,
        suspect_names: List[str],
        dose_levels: Optional[List[DoseLevel]] = None,
        n_blocks: int = 4,
        window_spec: Optional[WindowSpec] = None,
        seed: int = 42,
        scenario_label: str = "UNKNOWN",
    ) -> None:
        if not suspect_names:
            raise ValueError("At least one suspect required")
        if n_blocks < 2:
            raise ValueError("n_blocks must be ≥ 2 for meaningful estimates")

        self.suspect_names = suspect_names
        self.dose_levels = dose_levels or DEFAULT_DOSE_LEVELS
        self.n_blocks = n_blocks
        self.window_spec = window_spec or WindowSpec()
        self.seed = seed
        self.scenario_label = scenario_label

    def create_design(self) -> ExperimentDesign:
        """
        Generate and validate the experiment design.

        Returns an ExperimentDesign with:
          - Globally unique design_id (hash of config + seed)
          - Randomised blocked schedule for each suspect (OAT)
          - Jittered inter-window gaps
          - Design-matrix rank check
        """
        rng = random.Random(self.seed)
        np_rng = np.random.default_rng(self.seed)

        design_id = self._make_design_id()
        blocks: List[BlockSpec] = []
        global_window_id = 0

        now = datetime.now(timezone.utc).isoformat()

        # For each suspect, create n_blocks blocks of dose assignments
        for suspect in self.suspect_names:
            for block_num in range(self.n_blocks):
                block_id = len(blocks)
                block = BlockSpec(
                    block_id=block_id,
                    suspect_name=suspect,
                )
                # Shuffle dose levels within this block
                shuffled_doses = self.dose_levels.copy()
                rng.shuffle(shuffled_doses)

                for dose_level in shuffled_doses:
                    jitter = float(
                        np_rng.uniform(
                            -self.window_spec.jitter_s,
                            self.window_spec.jitter_s,
                        )
                    )
                    window = AssignedWindow(
                        block_id=block_id,
                        window_id=global_window_id,
                        suspect_name=suspect,
                        dose_level=dose_level,
                        jitter_s=jitter,
                        assigned_at=now,
                    )
                    block.windows.append(window)
                    global_window_id += 1

                blocks.append(block)

        design = ExperimentDesign(
            design_id=design_id,
            scenario_label=self.scenario_label,
            seed=self.seed,
            suspect_names=self.suspect_names,
            dose_levels=self.dose_levels,
            n_blocks=self.n_blocks,
            window_spec=self.window_spec,
            blocks=blocks,
            created_at=now,
        )

        # Validate design matrix rank
        self._validate_rank(design)

        return design

    # ------------------------------------------------------------------
    # Rank validation
    # ------------------------------------------------------------------

    def _validate_rank(self, design: ExperimentDesign) -> None:
        """
        Check that the design matrix supports the primary linear model.

        Primary model: Y ~ intercept + beta_k * D_k + gamma_k * D_k^2
        for each suspect k, plus block effects.

        We do NOT check interaction identifiability for OAT designs
        (they are NOT identified) — we just mark rank_ok and add notes.
        """
        windows = design.all_windows
        if not windows:
            design.rank_ok = False
            design.rank_notes.append("No windows in design")
            return

        # Build design matrix for one suspect
        suspect = design.suspect_names[0]
        rows = []
        for w in windows:
            if w.suspect_name != suspect:
                continue
            d = w.dose_level.dose
            block = w.block_id
            rows.append([1.0, d, d * d, block])  # intercept + D + D^2 + block

        if len(rows) < 3:
            design.rank_ok = False
            design.rank_notes.append(
                f"Too few windows ({len(rows)}) for rank check"
            )
            return

        import numpy as np
        X = np.array(rows, dtype=float)
        rank = np.linalg.matrix_rank(X)
        full_rank = X.shape[1]
        design.rank_ok = (rank >= full_rank)
        design.rank_notes.append(
            f"Design matrix: {X.shape[0]} rows × {X.shape[1]} cols, "
            f"rank={rank} (full rank={full_rank})"
        )

        if len(design.suspect_names) > 1:
            design.rank_notes.append(
                "IMPLEMENTATION DECISION: OAT design used for K>1 suspects. "
                "Interaction effects are NOT identified and will NOT be reported."
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_design_id(self) -> str:
        """Deterministic ID from config + seed."""
        config_str = json.dumps({
            "suspects": sorted(self.suspect_names),
            "doses": [d.dose for d in self.dose_levels],
            "n_blocks": self.n_blocks,
            "seed": self.seed,
            "scenario": self.scenario_label,
        }, sort_keys=True)
        return hashlib.sha256(config_str.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Design summary
    # ------------------------------------------------------------------

    @staticmethod
    def summarise(design: ExperimentDesign) -> str:
        lines = [
            f"Design ID  : {design.design_id}",
            f"Scenario   : {design.scenario_label}",
            f"Seed       : {design.seed}",
            f"Suspects   : {design.suspect_names}",
            f"Doses      : {[d.name for d in design.dose_levels]}",
            f"Blocks     : {design.n_blocks}",
            f"Windows    : {design.n_total_windows}",
            f"Window     : {design.window_spec.warmup_s}s warmup / "
            f"{design.window_spec.measure_s}s measure / "
            f"{design.window_spec.washout_s}s washout",
            f"Rank OK    : {design.rank_ok}",
        ]
        for note in design.rank_notes:
            lines.append(f"  Rank note: {note}")
        return "\n".join(lines)
