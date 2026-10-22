"""
Pilot calibration module.

Estimates:
  - settling time (how long after dose change until signal stabilises)
  - baseline variability sigma_Y
  - intraclass correlation coefficient (ICC) for baseline reliability
  - minimum detectable effect (MDE) at given power/alpha

Thresholds produced by pilot are written to a versioned config
before confirmatory runs (freeze-config step).

All defaults from the AGRDA document are labelled as initial values.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PilotResult:
    """
    Output of the pilot calibration.

    All values are estimated from simulation (or real pilot data).
    Treat as configurable initial estimates, not universal truths.
    """
    # Baseline variability
    sigma_y_abs: float = 0.0      # std dev of baseline throughput (ops/s)
    sigma_y_rel: float = 0.0      # relative std dev (fraction of mean)
    baseline_mean: float = 0.0

    # ICC – intraclass correlation coefficient
    # ICC near 1.0 = reliable repeated measurements
    # ICC near 0.0 = dominated by noise
    icc: float = 0.0
    icc_notes: List[str] = field(default_factory=list)

    # Settling time estimate (seconds)
    settling_time_s: float = 10.0  # document default: 10s (initial only)
    settling_notes: List[str] = field(default_factory=list)

    # MDE (minimum detectable effect) as fraction of baseline
    mde_fraction: float = 0.0
    mde_at_power: float = 0.80    # 80% power
    mde_at_alpha: float = 0.05
    mde_notes: List[str] = field(default_factory=list)

    # Recommended window spec based on pilot
    recommended_warmup_s: float = 10.0    # document: 10s
    recommended_measure_s: float = 30.0   # document: 30s
    recommended_washout_s: float = 20.0   # document: 20s

    # Configurable thresholds (initial values from document)
    delta_eff: float = 0.05   # 5% of baseline – relevance threshold
    delta_eq: float = 0.0125  # 25% of Delta_eff – equivalence margin
    kappa: float = 0.25       # mediator-to-outcome ratio threshold

    timestamp: str = ""
    data_source: str = "UNKNOWN"
    notes: List[str] = field(default_factory=list)


class PilotCalibrator:
    """
    Runs a lightweight pilot to estimate measurement characteristics.

    Uses the simulation backend to generate baseline windows and
    computes sigma_Y, ICC, MDE.

    In real-hardware mode, this would drive actual workloads — but
    that path is not enabled on this host.
    """

    def __init__(
        self,
        n_baseline_windows: int = 20,
        n_groups: int = 4,       # for ICC calculation
        target_power: float = 0.80,
        target_alpha: float = 0.05,
        seed: int = 42,
    ) -> None:
        self.n_baseline_windows = n_baseline_windows
        self.n_groups = n_groups
        self.target_power = target_power
        self.target_alpha = target_alpha
        self._rng = np.random.default_rng(seed)

    def calibrate_from_measurements(
        self,
        baseline_throughputs: List[float],
        data_source: str = "SIMULATION",
    ) -> PilotResult:
        """
        Compute pilot statistics from a list of baseline throughput values.

        Parameters
        ----------
        baseline_throughputs : list of ops/s values at dose=0
        data_source          : "SIMULATION" or "REAL"
        """
        result = PilotResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            data_source=data_source,
        )

        arr = np.array(baseline_throughputs, dtype=float)
        if len(arr) < 4:
            result.notes.append("Too few baseline windows for reliable estimates")
            return result

        # --- Baseline stats ---
        result.baseline_mean = float(np.mean(arr))
        result.sigma_y_abs = float(np.std(arr, ddof=1))
        result.sigma_y_rel = result.sigma_y_abs / max(result.baseline_mean, 1e-9)

        # --- ICC via one-way ANOVA decomposition ---
        result.icc = self._compute_icc(arr, n_groups=self.n_groups)

        # --- MDE ---
        result.mde_fraction = self._compute_mde(
            sigma=result.sigma_y_rel,
            n=len(arr),
            power=self.target_power,
            alpha=self.target_alpha,
        )
        result.mde_at_power = self.target_power
        result.mde_at_alpha = self.target_alpha

        # --- Settling time (simulation: fixed at 10s initial) ---
        result.settling_time_s = 10.0
        result.settling_notes.append(
            "Settling time = 10s (document initial default); "
            "replace with real pilot estimates on Linux hardware"
        )

        # --- Recommended window spec ---
        settle = max(result.settling_time_s, 5.0)
        result.recommended_warmup_s = settle
        result.recommended_measure_s = max(30.0, 15 / max(result.sigma_y_rel, 0.01))
        result.recommended_washout_s = settle * 2.0

        # Initial threshold values from document
        result.delta_eff = 0.05
        result.delta_eq = 0.05 * 0.25   # 25% of Delta_eff
        result.kappa = 0.25

        result.notes.append(
            "Thresholds delta_eff=0.05, delta_eq=0.0125, kappa=0.25 are "
            "initial values from the AGRDA document. Freeze via freeze-config "
            "before confirmatory runs."
        )

        return result

    # ------------------------------------------------------------------
    # ICC
    # ------------------------------------------------------------------

    def _compute_icc(self, arr: np.ndarray, n_groups: int) -> float:
        """
        One-way ICC(1,1) using ANOVA decomposition.

        Splits arr into n_groups groups and computes:
          ICC = (MS_between - MS_within) / (MS_between + (k-1)*MS_within)
        where k = items per group.

        Returns value in [-1, 1]; negative values clamped to 0.
        """
        n = len(arr)
        k = n // n_groups
        if k < 2:
            return 0.0

        groups = [arr[i * k:(i + 1) * k] for i in range(n_groups)]
        group_means = np.array([g.mean() for g in groups])
        grand_mean = arr[:n_groups * k].mean()

        # Between-group SS
        ss_between = k * np.sum((group_means - grand_mean) ** 2)
        df_between = n_groups - 1

        # Within-group SS
        ss_within = sum(np.sum((g - g.mean()) ** 2) for g in groups)
        df_within = n_groups * (k - 1)

        if df_between == 0 or df_within == 0:
            return 0.0

        ms_between = ss_between / df_between
        ms_within = ss_within / df_within

        denom = ms_between + (k - 1) * ms_within
        if denom == 0:
            return 0.0

        icc = (ms_between - ms_within) / denom
        return float(np.clip(icc, 0.0, 1.0))

    # ------------------------------------------------------------------
    # MDE
    # ------------------------------------------------------------------

    def _compute_mde(
        self,
        sigma: float,
        n: int,
        power: float = 0.80,
        alpha: float = 0.05,
    ) -> float:
        """
        MDE for a two-sided t-test with given power and alpha.

        Formula (Normal approximation):
          MDE = (z_alpha/2 + z_beta) * sigma * sqrt(2/n)

        where z_alpha/2 = quantile at 1 - alpha/2
              z_beta    = quantile at power

        This is an approximation assuming equal-size groups.
        For blocked designs the actual MDE will be lower (blocking reduces sigma).

        Implementation decision: We use the simple two-sample approximation
        as an initial estimate. A more accurate formula accounting for block
        structure would require knowing the intra-block correlation.
        """
        from scipy.stats import norm
        z_alpha = norm.ppf(1 - alpha / 2)
        z_beta = norm.ppf(power)
        if n < 2:
            return float("inf")
        mde = (z_alpha + z_beta) * sigma * np.sqrt(2.0 / n)
        return float(mde)
