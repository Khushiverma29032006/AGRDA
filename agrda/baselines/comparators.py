"""
Baseline comparators B1–B4.

B1: Passive correlation – attribute interference based on observed
    correlation between suspect traffic and victim performance.
    No intervention.

B2: Randomised intervention, no audit – use AGRDA intervention
    but apply the five-outcome policy WITHOUT any validity checks.
    Just uses the raw dose-response estimate.

B3: Full AGRDA – randomised intervention + all nine checks.
    This is the full method.

B4: Partial audit – B2 + only placebo (C3) and negative sentinel
    (C4) checks. Evaluates whether a cheaper audit battery matches B3.

Evaluation metrics:
  - Culprit identification accuracy (true positive rate)
  - False attribution rate (FAR)
  - False certification rate (FCR) – certifying a non-interferer
  - False rejection rate (FRR) – rejecting a true interferer
  - Inconclusive rate
  - Confidence interval coverage

IMPORTANT: Do NOT claim any baseline outperforms others until
actual evaluation results are computed. Report only computed metrics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import stats

from ..audit.models import AuditBatteryResult, CheckStatus
from ..decision.policy import DecisionPolicy, DecisionResult, Outcome


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class BaselineResult:
    """
    Result for one scenario trial under one baseline method.
    """
    method: str              # "B1", "B2", "B3", "B4"
    scenario_id: str
    trial: int

    # Ground truth (from scenario)
    true_interferer: bool    # Is the suspect actually causing interference?
    true_pathway: str        # "memory", "other", "none"

    # Method output
    attributed: bool         # Did this method accuse the suspect?
    outcome: Optional[Outcome] = None
    ci_lower: Optional[float] = None
    ci_upper: Optional[float] = None
    true_effect: Optional[float] = None   # ground-truth effect size

    notes: List[str] = field(default_factory=list)


@dataclass
class EvaluationSummary:
    """
    Aggregated evaluation metrics across trials.
    """
    method: str
    scenario_id: str
    n_trials: int

    # Classification metrics (with 95% Wilson CIs)
    tpr: float = 0.0          # true positive rate (sensitivity)
    far: float = 0.0          # false attribution rate (= 1 - specificity)
    fcr: float = 0.0          # false certification rate
    frr: float = 0.0          # false rejection rate
    inconclusive_rate: float = 0.0

    # CI coverage (fraction of trials where true effect falls in CI)
    ci_coverage: float = 0.0

    # CI widths (lower = more precise)
    mean_ci_width: float = 0.0

    # Wilson CI for each rate
    tpr_ci: Tuple[float, float] = (0.0, 1.0)
    far_ci: Tuple[float, float] = (0.0, 1.0)
    fcr_ci: Tuple[float, float] = (0.0, 1.0)

    notes: List[str] = field(default_factory=list)

    def summary_line(self) -> str:
        return (
            f"{self.method} | {self.scenario_id} | n={self.n_trials} | "
            f"TPR={self.tpr:.2f} FAR={self.far:.2f} FCR={self.fcr:.2f} "
            f"FRR={self.frr:.2f} Inconcl={self.inconclusive_rate:.2f} "
            f"CI-cov={self.ci_coverage:.2f}"
        )


# ---------------------------------------------------------------------------
# Baseline implementations
# ---------------------------------------------------------------------------

class B1PassiveCorrelation:
    """
    B1: Passive correlation-based attribution.

    Attributes a suspect when the Pearson correlation between
    suspect traffic and victim throughput exceeds a threshold.
    No intervention; observational only.

    Implementation decision: We use the simulation's ground-truth
    correlation signal plus noise to mimic what a monitoring system
    would observe. The threshold is 0.3 (configurable).
    """

    def __init__(
        self,
        correlation_threshold: float = 0.30,
        seed: int = 42,
    ) -> None:
        self.correlation_threshold = correlation_threshold
        self._rng = np.random.default_rng(seed)

    def attribute(
        self,
        suspect_bw: np.ndarray,
        victim_throughput: np.ndarray,
    ) -> Tuple[bool, float]:
        """
        Return (attributed, correlation).
        """
        if len(suspect_bw) < 3 or len(victim_throughput) < 3:
            return False, 0.0
        r, p = stats.pearsonr(suspect_bw, -victim_throughput)
        # Negative victim throughput: more suspect BW → lower throughput
        attributed = r > self.correlation_threshold
        return attributed, float(r)


class B2RandomizedNoAudit:
    """
    B2: Randomised intervention, no validity audit.

    Uses AGRDA dose-response estimation but applies a simple threshold
    rule without running the nine-check audit battery.

    Attribution: top-dose effect CI does NOT contain zero and
    effect > delta_eff.
    """

    def __init__(self, delta_eff: float = 0.05) -> None:
        self.delta_eff = delta_eff

    def decide(
        self,
        tau_hat: float,
        ci_lower: float,
        ci_upper: float,
    ) -> Tuple[bool, Optional[Outcome]]:
        """
        Simple threshold decision without audit.
        Returns (attributed, outcome).
        """
        if ci_lower is None or ci_upper is None:
            return False, None

        # Effect must be relevant (negative = victim improved when throttled)
        # We treat tau < -delta_eff as evidence of interference
        if ci_upper < -self.delta_eff:
            return True, Outcome.O5_INTERVENTION_EFFECT_ONLY  # no audit
        elif ci_lower > -self.delta_eff and ci_upper < self.delta_eff:
            return False, Outcome.O2_NULL
        else:
            return False, Outcome.O1_INCONCLUSIVE


class B3FullAGRDA:
    """
    B3: Full AGRDA – all nine checks + five-outcome policy.
    """

    def __init__(self, delta_eff: float = 0.05) -> None:
        self.policy = DecisionPolicy(delta_eff=delta_eff)

    def decide(
        self,
        audit: AuditBatteryResult,
        tau_hat: float,
        ci_lower: float,
        ci_upper: float,
        data_source: str = "SIMULATION",
    ) -> DecisionResult:
        return self.policy.decide(
            audit=audit,
            top_dose_tau=tau_hat,
            top_dose_ci_lower=ci_lower,
            top_dose_ci_upper=ci_upper,
            data_source=data_source,
        )


class B4PartialAudit:
    """
    B4: B2 (randomised) + only C3 (placebo) and C4 (sentinel) checks.

    Tests whether the full 9-check audit battery is necessary or if
    a cheaper 2-check version is sufficient.
    """

    def __init__(self, delta_eff: float = 0.05) -> None:
        self.delta_eff = delta_eff
        # Use the full policy engine but only with C3, C4 checks active
        self.policy = DecisionPolicy(delta_eff=delta_eff)

    def decide(
        self,
        audit: AuditBatteryResult,
        tau_hat: float,
        ci_lower: float,
        ci_upper: float,
        data_source: str = "SIMULATION",
    ) -> DecisionResult:
        """
        Apply policy but treat C1,C2,C5,C6,C7,C8,C9 as UNRESOLVED.
        Only C3 and C4 are evaluated.
        """
        # Build a partial audit with non-C3/C4 checks forced to UNRESOLVED
        from ..audit.models import CheckResult
        partial_audit = AuditBatteryResult(data_source=data_source)
        for check in audit.checks:
            if check.check_id in ("C3", "C4"):
                partial_audit.checks.append(check)
            else:
                # Force to UNRESOLVED
                unresolved = CheckResult(
                    check_id=check.check_id,
                    name=check.name,
                    status=CheckStatus.UNRESOLVED,
                    data_source=data_source,
                    reasons=["B4 partial audit: check not evaluated"],
                    limitations=["B4 only evaluates C3 and C4"],
                )
                partial_audit.checks.append(unresolved)

        partial_audit.update_counts()
        return self.policy.decide(
            audit=partial_audit,
            top_dose_tau=tau_hat,
            top_dose_ci_lower=ci_lower,
            top_dose_ci_upper=ci_upper,
            data_source=data_source,
        )


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class BaselineEvaluator:
    """
    Evaluates B1–B4 on a set of trial results and computes metrics.
    """

    def compute_metrics(
        self,
        results: List[BaselineResult],
        method: str,
        scenario_id: str,
    ) -> EvaluationSummary:
        """
        Compute TPR, FAR, FCR, FRR, inconclusive rate, CI coverage.
        """
        summary = EvaluationSummary(
            method=method,
            scenario_id=scenario_id,
            n_trials=len(results),
        )
        if not results:
            summary.notes.append("No trials to evaluate")
            return summary

        # Classification
        tp = sum(1 for r in results if r.true_interferer and r.attributed)
        tn = sum(1 for r in results if not r.true_interferer and not r.attributed)
        fp = sum(1 for r in results if not r.true_interferer and r.attributed)
        fn = sum(1 for r in results if r.true_interferer and not r.attributed)
        inconcl = sum(
            1 for r in results
            if r.outcome == Outcome.O1_INCONCLUSIVE
        )

        n_pos = tp + fn
        n_neg = tn + fp
        n = len(results)

        summary.tpr = tp / n_pos if n_pos > 0 else float("nan")
        summary.far = fp / n_neg if n_neg > 0 else float("nan")
        summary.fcr = fp / n if n > 0 else float("nan")
        summary.frr = fn / n_pos if n_pos > 0 else float("nan")
        summary.inconclusive_rate = inconcl / n

        # Wilson CIs for proportions
        summary.tpr_ci = self._wilson_ci(tp, n_pos) if n_pos > 0 else (0.0, 1.0)
        summary.far_ci = self._wilson_ci(fp, n_neg) if n_neg > 0 else (0.0, 1.0)
        summary.fcr_ci = self._wilson_ci(fp, n)

        # CI coverage
        covered = [
            r for r in results
            if (r.ci_lower is not None and r.ci_upper is not None
                and r.true_effect is not None
                and r.ci_lower <= r.true_effect <= r.ci_upper)
        ]
        with_ci = [
            r for r in results
            if r.ci_lower is not None and r.true_effect is not None
        ]
        summary.ci_coverage = (
            len(covered) / len(with_ci) if with_ci else float("nan")
        )

        # CI width
        widths = [
            r.ci_upper - r.ci_lower
            for r in results
            if r.ci_lower is not None and r.ci_upper is not None
        ]
        summary.mean_ci_width = float(np.mean(widths)) if widths else float("nan")

        return summary

    @staticmethod
    def _wilson_ci(k: int, n: int, alpha: float = 0.05) -> Tuple[float, float]:
        """Wilson score confidence interval for a proportion."""
        if n == 0:
            return (0.0, 1.0)
        z = stats.norm.ppf(1 - alpha / 2)
        p_hat = k / n
        denom = 1 + z ** 2 / n
        centre = (p_hat + z ** 2 / (2 * n)) / denom
        margin = z * math.sqrt(p_hat * (1 - p_hat) / n + z ** 2 / (4 * n ** 2)) / denom
        return (max(0.0, centre - margin), min(1.0, centre + margin))
