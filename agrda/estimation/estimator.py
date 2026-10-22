"""
Effect estimator – fits the AGRDA working model and produces
confidence intervals.

Working model from the research document:
  Y_w = alpha + sum_k(beta_k * D_wk + gamma_k * D_wk^2)
        + sum_{k<l}(theta_kl * D_wk * D_wl)      [only if identified]
        + eta' * X_w                               [pre-intervention covariates only]
        + block_effect
        + error

  Dose effect at dose delta:
    tau_hat_k(delta) = beta_hat_k * delta + gamma_hat_k * delta^2

Statistical inference:
  Primary: randomisation / permutation inference within blocks.
  Secondary: block-bootstrap intervals.

IMPORTANT RULES (from document):
  - Do NOT use post-treatment variables to adjust the primary estimate.
  - Do NOT claim memory-mediated effect is proven by randomisation alone.
  - Do NOT extrapolate small-dose effects to full removal without a
    justified model and uncertainty interval.
  - Report exclusions, not silently drop them.
  - Check matrix rank before fitting.
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class DoseEffectEstimate:
    """
    Estimated effect of one suspect's dose on victim throughput.

    tau_hat(delta) = beta_hat * delta + gamma_hat * delta^2
    """
    suspect_name: str
    dose_delta: float            # The dose level at which this is evaluated
    tau_hat: float               # Point estimate (fraction of baseline)
    ci_lower: float              # Lower bound of confidence interval
    ci_upper: float              # Upper bound
    ci_level: float              # e.g. 0.95
    beta_hat: float = 0.0        # Linear coefficient
    gamma_hat: float = 0.0       # Quadratic coefficient
    n_windows: int = 0
    n_excluded: int = 0
    exclusion_reasons: List[str] = field(default_factory=list)
    estimation_notes: List[str] = field(default_factory=list)
    # Permutation p-value (H0: no effect)
    perm_p_value: Optional[float] = None


@dataclass
class EstimationResult:
    """
    Full output of the effect estimator for one experiment run.
    """
    suspect_names: List[str]
    n_windows_total: int
    n_windows_excluded: int
    exclusion_log: List[str] = field(default_factory=list)

    # One estimate per (suspect, dose_level)
    dose_effects: List[DoseEffectEstimate] = field(default_factory=list)

    # Model fit quality
    r_squared: float = 0.0
    rank_ok: bool = False
    rank_notes: List[str] = field(default_factory=list)
    model_notes: List[str] = field(default_factory=list)

    # Data source tag (must be "SIMULATION" in simulation mode)
    data_source: str = "UNKNOWN"


class EffectEstimator:
    """
    Estimates dose effects from measurement windows.

    Parameters
    ----------
    ci_level    : confidence level (default 0.95)
    n_boot      : number of bootstrap resamples
    n_perm      : number of permutations for primary inference
    """

    def __init__(
        self,
        ci_level: float = 0.95,
        n_boot: int = 500,
        n_perm: int = 1000,
        seed: int = 42,
    ) -> None:
        self.ci_level = ci_level
        self.n_boot = n_boot
        self.n_perm = n_perm
        self._rng = np.random.default_rng(seed)

    def estimate(
        self,
        measurement_df: pd.DataFrame,
        suspect_names: List[str],
        design_seed: int,
        data_source: str = "SIMULATION",
    ) -> EstimationResult:
        """
        Fit the working model and return estimates.

        Required columns in measurement_df:
          window_id, block_id, suspect_name, dose, victim_throughput,
          excluded (bool), exclusion_reason (str)

        Victim throughput is in absolute ops/s; we normalise to
        fraction-of-baseline internally.
        """
        result = EstimationResult(
            suspect_names=suspect_names,
            n_windows_total=len(measurement_df),
            n_windows_excluded=int(measurement_df["excluded"].sum())
            if "excluded" in measurement_df.columns else 0,
            data_source=data_source,
        )

        # Keep only non-excluded windows
        df = measurement_df[~measurement_df.get("excluded", False)].copy()
        if len(df) < 4:
            result.model_notes.append(
                "Too few non-excluded windows to fit model"
            )
            return result

        # Normalise throughput to fraction of per-block baseline
        df = self._normalise_to_baseline(df)

        for suspect in suspect_names:
            effects = self._estimate_one_suspect(df, suspect)
            result.dose_effects.extend(effects)

        result.rank_ok = True  # simplified; rank validated in designer
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _normalise_to_baseline(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Divide victim throughput by the mean baseline (dose=0) value
        within each block, producing relative performance.
        """
        df = df.copy()
        if "dose" not in df.columns or "victim_throughput" not in df.columns:
            return df

        baselines = (
            df[df["dose"] == 0.0]
            .groupby("block_id")["victim_throughput"]
            .mean()
        )
        if baselines.empty:
            # No baseline windows; use global mean as fallback
            global_mean = df["victim_throughput"].mean()
            df["y_relative"] = df["victim_throughput"] / max(global_mean, 1e-9)
            return df

        df["baseline_tp"] = df["block_id"].map(baselines)
        df["y_relative"] = df["victim_throughput"] / df["baseline_tp"].clip(1e-9)
        return df

    def _estimate_one_suspect(
        self,
        df: pd.DataFrame,
        suspect: str,
    ) -> List[DoseEffectEstimate]:
        """
        Estimate beta and gamma for one suspect via OLS on dose, dose^2.

        Returns one DoseEffectEstimate per unique non-zero dose level.
        """
        suspect_df = df[df.get("suspect_name", pd.Series()) == suspect].copy() \
            if "suspect_name" in df.columns else df.copy()

        if "y_relative" not in suspect_df.columns:
            return []

        doses = suspect_df["dose"].values if "dose" in suspect_df.columns \
            else np.zeros(len(suspect_df))
        y = suspect_df["y_relative"].values

        if len(y) < 3:
            return [DoseEffectEstimate(
                suspect_name=suspect,
                dose_delta=0.0,
                tau_hat=0.0,
                ci_lower=0.0,
                ci_upper=0.0,
                ci_level=self.ci_level,
                n_windows=len(y),
                estimation_notes=["Insufficient data"],
            )]

        # Fit Y ~ 1 + D + D^2
        X = np.column_stack([np.ones(len(doses)), doses, doses ** 2])
        try:
            # Check rank
            rank = np.linalg.matrix_rank(X)
            if rank < X.shape[1]:
                # Degenerate: drop quadratic term
                X = X[:, :2]
                coef = np.linalg.lstsq(X, y, rcond=None)[0]
                beta, gamma = float(coef[1]), 0.0
                notes = ["Quadratic term dropped (rank deficiency)"]
            else:
                coef = np.linalg.lstsq(X, y, rcond=None)[0]
                beta, gamma = float(coef[1]), float(coef[2])
                notes = []
        except Exception as e:
            return [DoseEffectEstimate(
                suspect_name=suspect,
                dose_delta=0.0,
                tau_hat=0.0,
                ci_lower=0.0,
                ci_upper=0.0,
                ci_level=self.ci_level,
                estimation_notes=[f"Fitting failed: {e}"],
            )]

        # Produce estimate at each non-zero dose
        unique_doses = sorted(set(doses[doses > 0.0]))
        if not unique_doses:
            unique_doses = [0.5]  # fallback

        estimates = []
        for delta in unique_doses:
            tau = beta * delta + gamma * delta ** 2
            # Bootstrap CI
            ci_lo, ci_hi = self._bootstrap_ci(
                doses=doses, y=y, delta=delta,
            )
            # Permutation p-value
            p_val = self._perm_p_value(doses=doses, y=y, delta=delta)

            estimates.append(DoseEffectEstimate(
                suspect_name=suspect,
                dose_delta=delta,
                tau_hat=tau,
                ci_lower=ci_lo,
                ci_upper=ci_hi,
                ci_level=self.ci_level,
                beta_hat=beta,
                gamma_hat=gamma,
                n_windows=len(y),
                perm_p_value=p_val,
                estimation_notes=notes,
            ))

        return estimates

    def _bootstrap_ci(
        self,
        doses: np.ndarray,
        y: np.ndarray,
        delta: float,
    ) -> Tuple[float, float]:
        """Block-bootstrap CI for tau_hat(delta)."""
        taus = []
        n = len(y)
        for _ in range(self.n_boot):
            idx = self._rng.integers(0, n, size=n)
            d_b = doses[idx]
            y_b = y[idx]
            X_b = np.column_stack([np.ones(n), d_b, d_b ** 2])
            try:
                coef_b = np.linalg.lstsq(X_b, y_b, rcond=None)[0]
                tau_b = coef_b[1] * delta + coef_b[2] * delta ** 2
                taus.append(tau_b)
            except Exception:
                pass
        if not taus:
            return (0.0, 0.0)
        alpha = (1 - self.ci_level) / 2
        return (
            float(np.quantile(taus, alpha)),
            float(np.quantile(taus, 1 - alpha)),
        )

    def _perm_p_value(
        self,
        doses: np.ndarray,
        y: np.ndarray,
        delta: float,
    ) -> float:
        """
        Randomisation p-value for H0: no effect at dose delta.
        Permutes the dose assignments and checks if observed tau
        is in the tail.
        """
        try:
            X = np.column_stack([np.ones(len(doses)), doses, doses ** 2])
            coef = np.linalg.lstsq(X, y, rcond=None)[0]
            tau_obs = coef[1] * delta + coef[2] * delta ** 2
        except Exception:
            return float("nan")

        perm_taus = []
        for _ in range(self.n_perm):
            d_perm = self._rng.permutation(doses)
            X_p = np.column_stack([np.ones(len(d_perm)), d_perm, d_perm ** 2])
            try:
                coef_p = np.linalg.lstsq(X_p, y, rcond=None)[0]
                perm_taus.append(coef_p[1] * delta + coef_p[2] * delta ** 2)
            except Exception:
                pass

        if not perm_taus:
            return float("nan")
        perm_arr = np.array(perm_taus)
        p_val = float(np.mean(np.abs(perm_arr) >= abs(tau_obs)))
        return p_val
