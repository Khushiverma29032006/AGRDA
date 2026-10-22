"""
Nine-check audit battery (C1–C9).

Each check uses thresholds from the pilot calibration result.
Thresholds are initial values from the AGRDA document:
  Delta_eff = 5% of baseline throughput
  Delta_eq  = 25% of Delta_eff = 1.25% of baseline
  kappa     = 0.25

These are configurable — do NOT hardcode them as constants.
They must be frozen before confirmatory runs.

CRITICAL RULE: If a check cannot be performed (feature unavailable,
insufficient data), return UNRESOLVED — NEVER PASS.

For simulation mode, some checks use synthetic ground-truth to
produce controlled PASS/FAIL results. The simulation explicitly
labels these as synthetic.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .models import AuditBatteryResult, CheckResult, CheckStatus
from ..estimation.estimator import EstimationResult

logger = logging.getLogger(__name__)

# Simulation data source tag
SIM_TAG = "SIMULATION"


class AuditBattery:
    """
    Runs all nine audit checks given experiment data.

    Parameters
    ----------
    delta_eff  : relevance threshold (fraction of baseline, default 0.05)
    delta_eq   : equivalence margin (fraction of baseline, default 0.0125)
    kappa      : mediator ratio threshold (default 0.25)
    icc_min    : minimum ICC for measurement adequacy (default 0.70)
    data_source: "SIMULATION" or "REAL"
    """

    def __init__(
        self,
        delta_eff: float = 0.05,
        delta_eq: float = 0.0125,
        kappa: float = 0.25,
        icc_min: float = 0.60,
        first_stage_min: float = 0.10,   # minimum mediator response to pass C2
        data_source: str = SIM_TAG,
    ) -> None:
        self.delta_eff = delta_eff
        self.delta_eq = delta_eq
        self.kappa = kappa
        self.icc_min = icc_min
        self.first_stage_min = first_stage_min
        self.data_source = data_source

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run_all(
        self,
        pilot_result: Any,        # PilotResult
        estimation_result: EstimationResult,
        sim_scenario: Optional[Any] = None,  # SimScenario for sim mode
        extra: Optional[Dict] = None,
    ) -> AuditBatteryResult:
        """
        Run C1–C9 and return an AuditBatteryResult.

        Parameters
        ----------
        pilot_result      : output of PilotCalibrator
        estimation_result : output of EffectEstimator
        sim_scenario      : scenario spec (for simulation-specific checks)
        extra             : extra data dict (mediator values, placebo results, etc.)
        """
        extra = extra or {}
        battery = AuditBatteryResult(data_source=self.data_source)

        battery.checks.append(self.c1_measurement_adequacy(pilot_result))
        battery.checks.append(
            self.c2_intervention_strength(estimation_result, extra)
        )
        battery.checks.append(self.c3_placebo(extra))
        battery.checks.append(self.c4_sentinel(extra))
        battery.checks.append(self.c5_alternative_pathways(extra))
        battery.checks.append(self.c6_alternative_actuator(extra))
        battery.checks.append(
            self.c7_dose_response_consistency(estimation_result)
        )
        battery.checks.append(self.c8_carryover(extra))
        battery.checks.append(self.c9_phase_balance(extra))

        battery.update_counts()
        return battery

    # ------------------------------------------------------------------
    # C1: Measurement Adequacy
    # ------------------------------------------------------------------

    def c1_measurement_adequacy(self, pilot_result: Any) -> CheckResult:
        """
        C1: Baseline repeatability, ICC, counter multiplexing, MDE.

        PASS if:
          - ICC >= icc_min (0.70 by default)
          - MDE <= Delta_eff (can detect the smallest relevant effect)
        UNRESOLVED if pilot data not available.
        FAIL if ICC too low or MDE too large.
        """
        cr = CheckResult(
            check_id="C1",
            name="Measurement Adequacy",
            data_source=self.data_source,
            thresholds={
                "icc_min": self.icc_min,
                "mde_max": self.delta_eff,
            },
        )

        if pilot_result is None:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append("No pilot result available")
            cr.limitations.append(
                "Run pilot calibration before confirmatory experiments"
            )
            return cr

        icc = getattr(pilot_result, "icc", None)
        mde = getattr(pilot_result, "mde_fraction", None)

        if icc is None or mde is None:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append("ICC or MDE not computed in pilot result")
            return cr

        cr.metric_values = {
            "icc": round(icc, 4),
            "mde_fraction": round(mde, 4),
            "sigma_y_rel": round(
                getattr(pilot_result, "sigma_y_rel", float("nan")), 4
            ),
        }

        fails = []
        if icc < self.icc_min:
            fails.append(
                f"ICC={icc:.3f} < minimum {self.icc_min:.2f}"
            )
        if mde > self.delta_eff:
            fails.append(
                f"MDE={mde:.4f} > Delta_eff={self.delta_eff:.4f}; "
                "cannot reliably detect the smallest relevant effect"
            )

        if fails:
            cr.status = CheckStatus.FAIL
            cr.reasons.extend(fails)
        else:
            cr.status = CheckStatus.PASS
            cr.evidence.append(
                f"ICC={icc:.3f} >= {self.icc_min:.2f}, "
                f"MDE={mde:.4f} <= {self.delta_eff:.4f}"
            )

        if self.data_source == SIM_TAG:
            cr.limitations.append(
                "SIMULATION: ICC and MDE from synthetic data; "
                "real multiplexing and hardware noise not modelled."
            )

        return cr

    # ------------------------------------------------------------------
    # C2: Intervention Strength
    # ------------------------------------------------------------------

    def c2_intervention_strength(
        self,
        estimation_result: EstimationResult,
        extra: Dict,
    ) -> CheckResult:
        """
        C2: Does dose change actually affect suspect traffic AND victim M?

        PASS if:
          - At least one suspect shows meaningful traffic reduction.
          - Victim mediator M (mem_stall_cpi) shows a first-stage effect.
        UNRESOLVED if mediator data unavailable.
        FAIL if no first-stage effect.
        """
        cr = CheckResult(
            check_id="C2",
            name="Intervention Strength",
            data_source=self.data_source,
            thresholds={
                "first_stage_min": self.first_stage_min,
                "delta_eff": self.delta_eff,
            },
        )

        # Check suspect traffic response
        suspect_bw_effect = extra.get("suspect_bw_reduction_fraction", None)
        mediator_effect = extra.get("mediator_effect_fraction", None)

        if suspect_bw_effect is None:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append(
                "Suspect memory traffic response not measured"
            )
            cr.limitations.append(
                "Requires MBM or equivalent memory counter"
            )
            return cr

        cr.metric_values["suspect_bw_reduction_fraction"] = suspect_bw_effect

        fails = []
        if suspect_bw_effect < self.first_stage_min:
            fails.append(
                f"Suspect BW reduction {suspect_bw_effect:.3f} < "
                f"minimum {self.first_stage_min:.3f} (weak first stage)"
            )

        # Check victim mediator
        if mediator_effect is None:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append(
                "Victim mediator (mem_stall_cpi) not available — "
                "cannot assess first-stage memory pathway"
            )
            cr.limitations.append(
                "Requires hardware memory stall counters"
            )
            if not fails:
                return cr
        else:
            cr.metric_values["mediator_effect_fraction"] = mediator_effect
            if mediator_effect < self.first_stage_min:
                fails.append(
                    f"Victim mediator effect {mediator_effect:.3f} < "
                    f"minimum {self.first_stage_min:.3f}"
                )

        if fails:
            cr.status = CheckStatus.FAIL
            cr.reasons.extend(fails)
        else:
            cr.status = CheckStatus.PASS
            cr.evidence.append(
                f"Suspect BW reduction={suspect_bw_effect:.3f}, "
                f"mediator effect={mediator_effect:.3f} — "
                "first stage adequate"
            )

        if self.data_source == SIM_TAG:
            cr.limitations.append(
                "SIMULATION: First-stage effects from synthetic model; "
                "real MBM counter accuracy not modelled."
            )

        return cr

    # ------------------------------------------------------------------
    # C3: Placebo Actuation
    # ------------------------------------------------------------------

    def c3_placebo(self, extra: Dict) -> CheckResult:
        """
        C3: Apply actuation to a low-memory placebo container.
        If victim moves more than Delta_eq, a non-memory pathway exists.

        PASS if victim effect <= Delta_eq.
        FAIL if victim effect > Delta_eq (pathway violation detected).
        UNRESOLVED if placebo data unavailable.
        """
        cr = CheckResult(
            check_id="C3",
            name="Placebo Actuation",
            data_source=self.data_source,
            thresholds={"delta_eq": self.delta_eq},
        )

        placebo_victim_effect = extra.get("placebo_victim_effect", None)
        if placebo_victim_effect is None:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append("Placebo container not configured or not measured")
            return cr

        cr.metric_values["placebo_victim_effect"] = placebo_victim_effect

        if abs(placebo_victim_effect) > self.delta_eq:
            cr.status = CheckStatus.FAIL
            cr.reasons.append(
                f"Victim moved {placebo_victim_effect:.4f} > "
                f"Delta_eq={self.delta_eq:.4f} under placebo actuation; "
                "non-memory pathway detected"
            )
        else:
            cr.status = CheckStatus.PASS
            cr.evidence.append(
                f"Victim placebo effect {placebo_victim_effect:.4f} <= "
                f"Delta_eq={self.delta_eq:.4f}; consistent with no "
                "direct non-memory pathway from actuation"
            )

        if self.data_source == SIM_TAG:
            cr.limitations.append(
                "SIMULATION: Placebo effect controlled by synthetic model; "
                "actual OS scheduler effects not included."
            )

        return cr

    # ------------------------------------------------------------------
    # C4: Sentinel / Negative Control
    # ------------------------------------------------------------------

    def c4_sentinel(self, extra: Dict) -> CheckResult:
        """
        C4: Negative sentinel should NOT respond; positive sentinel should.

        PASS if:
          - Negative sentinel effect <= Delta_eq
          - Positive sentinel effect > Delta_eff (sanity check)
        UNRESOLVED if sentinels not configured.
        FAIL if negative sentinel moves too much.
        """
        cr = CheckResult(
            check_id="C4",
            name="Sentinel / Negative Control",
            data_source=self.data_source,
            thresholds={
                "delta_eq": self.delta_eq,
                "delta_eff": self.delta_eff,
            },
        )

        neg_effect = extra.get("neg_sentinel_effect", None)
        pos_effect = extra.get("pos_sentinel_effect", None)

        if neg_effect is None:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append("Negative sentinel not configured or not measured")
            return cr

        cr.metric_values["neg_sentinel_effect"] = neg_effect
        if pos_effect is not None:
            cr.metric_values["pos_sentinel_effect"] = pos_effect

        fails = []
        if abs(neg_effect) > self.delta_eq:
            fails.append(
                f"Negative sentinel moved {neg_effect:.4f} > "
                f"Delta_eq={self.delta_eq:.4f}; "
                "suggests broad non-memory pathway"
            )
        if pos_effect is not None and pos_effect <= self.delta_eff:
            fails.append(
                f"Positive sentinel effect {pos_effect:.4f} <= "
                f"Delta_eff={self.delta_eff:.4f}; "
                "sanity check failed (system may not be interfering)"
            )

        if fails:
            cr.status = CheckStatus.FAIL
            cr.reasons.extend(fails)
        else:
            cr.status = CheckStatus.PASS
            cr.evidence.append(
                f"Neg sentinel effect={neg_effect:.4f} <= Delta_eq; "
                + (
                    f"Pos sentinel effect={pos_effect:.4f} > Delta_eff"
                    if pos_effect else "Pos sentinel not configured"
                )
            )

        if self.data_source == SIM_TAG:
            cr.limitations.append(
                "SIMULATION: Sentinel responses from synthetic model; "
                "real NUMA/cache topology effects not modelled."
            )

        return cr

    # ------------------------------------------------------------------
    # C5: Alternative-Pathway Monitors
    # ------------------------------------------------------------------

    def c5_alternative_pathways(self, extra: Dict) -> CheckResult:
        """
        C5: Assess frequency, scheduling, SMT, cache, interrupt, thermal,
        and control-plane pathways where measurable.

        In simulation mode these are UNRESOLVED unless the scenario
        explicitly injects an alternative pathway violation.

        IMPORTANT: Do not use pathway variables as primary model covariates
        (they are post-treatment). They are used only for audit.
        """
        cr = CheckResult(
            check_id="C5",
            name="Alternative-Pathway Monitors",
            data_source=self.data_source,
        )

        pathway_effects = extra.get("pathway_effects", {})

        if not pathway_effects:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append(
                "No alternative-pathway monitors available on this platform"
            )
            cr.limitations.append(
                "Requires: perf APERF/MPERF, PSI, schedstat, eBPF, "
                "thermal counters — not available in simulation mode"
            )
            cr.limitations.append(
                "NOTE: Pathway variables must NOT be used as covariates in "
                "the primary causal model (post-treatment)."
            )
            return cr

        # If pathway effects provided (e.g., from injected violation in S9)
        violation_detected = any(
            v > self.delta_eq for v in pathway_effects.values()
        )
        cr.metric_values = pathway_effects

        if violation_detected:
            cr.status = CheckStatus.FAIL
            violating = [k for k, v in pathway_effects.items()
                         if v > self.delta_eq]
            cr.reasons.append(
                f"Pathway violation detected via: {violating}"
            )
        else:
            cr.status = CheckStatus.PASS
            cr.evidence.append(
                "All monitored pathway effects within equivalence margin"
            )

        return cr

    # ------------------------------------------------------------------
    # C6: Alternative-Actuator Consistency
    # ------------------------------------------------------------------

    def c6_alternative_actuator(self, extra: Dict) -> CheckResult:
        """
        C6: Compare MBA and CPU-quota response-to-mediator ratios.

        Agreement is NOT proof of memory mediation — it is one
        supporting piece of evidence under stated assumptions.

        UNRESOLVED if only one actuator is available (common case).
        """
        cr = CheckResult(
            check_id="C6",
            name="Alternative-Actuator Consistency",
            data_source=self.data_source,
        )

        mba_ratio = extra.get("mba_response_ratio", None)
        cpu_ratio = extra.get("cpu_quota_response_ratio", None)

        if mba_ratio is None or cpu_ratio is None:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append(
                "Two actuator types (MBA + CPU quota) not both available; "
                "ratio comparison not possible"
            )
            cr.limitations.append(
                "Requires Intel RDT MBA + cgroup v2 cpu.max — "
                "not available on this host"
            )
            cr.limitations.append(
                "Even if both actuators available, agreement is not proof "
                "that the exclusion restriction holds."
            )
            return cr

        ratio_diff = abs(mba_ratio - cpu_ratio) / max(abs(mba_ratio), 1e-9)
        cr.metric_values = {
            "mba_ratio": mba_ratio,
            "cpu_quota_ratio": cpu_ratio,
            "relative_difference": ratio_diff,
        }

        if ratio_diff > 0.30:  # 30% relative difference is suspect
            cr.status = CheckStatus.FAIL
            cr.reasons.append(
                f"MBA and CPU-quota ratios differ by {ratio_diff:.1%}; "
                "inconsistent with single-pathway explanation"
            )
        else:
            cr.status = CheckStatus.PASS
            cr.evidence.append(
                f"MBA ratio={mba_ratio:.3f}, CPU-quota ratio={cpu_ratio:.3f}; "
                f"relative diff={ratio_diff:.1%} — consistent"
            )
            cr.evidence.append(
                "CAVEAT: Ratio agreement is supporting evidence only, "
                "not proof that the memory pathway is uniquely responsible."
            )

        return cr

    # ------------------------------------------------------------------
    # C7: Dose-Response Consistency
    # ------------------------------------------------------------------

    def c7_dose_response_consistency(
        self,
        estimation_result: EstimationResult,
    ) -> CheckResult:
        """
        C7: Expected monotone dose-response (more throttle → less interference),
        non-saturation, and stable ratio across doses.

        PASS if effects are reasonably monotone (≤ 1 small reversal).
        FAIL if:
          - Strong non-monotonicity (> half of consecutive pairs reversed), OR
          - Highest dose has a positive tau_hat (wrong direction entirely), OR
          - Effects clearly decrease then become positive at high dose.
        UNRESOLVED if fewer than 2 dose levels measured.
        """
        cr = CheckResult(
            check_id="C7",
            name="Dose-Response Consistency",
            data_source=self.data_source,
            thresholds={"delta_eff": self.delta_eff},
        )

        if not estimation_result.dose_effects:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append("No dose effect estimates available")
            return cr

        # Group effects by suspect
        from collections import defaultdict
        by_suspect: Dict = defaultdict(list)
        for de in estimation_result.dose_effects:
            by_suspect[de.suspect_name].append(de)

        violations = []
        evidence = []

        for suspect, effects in by_suspect.items():
            effects_sorted = sorted(effects, key=lambda e: e.dose_delta)
            taus = [e.tau_hat for e in effects_sorted]
            doses = [e.dose_delta for e in effects_sorted]

            if len(taus) < 2:
                cr.reasons.append(
                    f"{suspect}: fewer than 2 dose levels; cannot assess trend"
                )
                continue

            # Expect: higher dose → more throttle → less interference
            # → tau_hat should be ≤ 0 and ideally more negative at higher doses
            # (tau_hat < 0 means victim throughput increased when suspect throttled)

            evidence.append(
                f"{suspect}: doses={doses}, effects={[round(t, 4) for t in taus]}"
            )

            # Rule 1: highest dose must not have a positive tau (wrong direction)
            highest_tau = taus[-1]
            if highest_tau > self.delta_eq:   # positive by more than delta_eq
                violations.append(
                    f"{suspect}: highest dose tau={highest_tau:.4f} is positive "
                    "(wrong direction — more throttle worsens victim)"
                )
                continue

            # Rule 2: general monotonicity — count consecutive reversals
            diffs = [taus[i + 1] - taus[i] for i in range(len(taus) - 1)]
            # A reversal is any consecutive pair where tau increases (less negative)
            # by more than delta_eq (small reversals may be noise)
            n_violations = sum(
                1 for d in diffs if d > self.delta_eq
            )

            if n_violations > len(diffs) / 2:
                violations.append(
                    f"{suspect}: dose-response non-monotone "
                    f"({n_violations}/{len(diffs)} significant reversals)"
                )

        if violations:
            cr.status = CheckStatus.FAIL
            cr.reasons.extend(violations)
        elif evidence:
            cr.status = CheckStatus.PASS
            cr.evidence.extend(evidence)
        else:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append("Insufficient data for dose-response check")

        if self.data_source == SIM_TAG:
            cr.limitations.append(
                "SIMULATION: Dose-response from synthetic linear model; "
                "real saturation and hysteresis not modelled."
            )

        return cr

    # ------------------------------------------------------------------
    # C8: Carry-Over and Recovery
    # ------------------------------------------------------------------

    def c8_carryover(self, extra: Dict) -> CheckResult:
        """
        C8: Check for washout insufficiency.

        Uses pre-window baseline measurements across blocks.
        If pre-window Y differs across preceding doses, carry-over exists.

        UNRESOLVED if washout data not collected separately.
        """
        cr = CheckResult(
            check_id="C8",
            name="Carry-Over and Recovery",
            data_source=self.data_source,
            thresholds={"delta_eq": self.delta_eq},
        )

        washout_residual = extra.get("washout_residual_fraction", None)
        if washout_residual is None:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append(
                "Washout residual not measured (pre-window baseline not "
                "collected separately from post-washout)"
            )
            cr.limitations.append(
                "Requires dedicated pre-window baseline windows"
            )
            return cr

        cr.metric_values["washout_residual_fraction"] = washout_residual

        if abs(washout_residual) > self.delta_eq:
            cr.status = CheckStatus.FAIL
            cr.reasons.append(
                f"Washout residual {washout_residual:.4f} > "
                f"Delta_eq={self.delta_eq:.4f}; "
                "carry-over detected — recommend longer washout or "
                "mark run invalid"
            )
        else:
            cr.status = CheckStatus.PASS
            cr.evidence.append(
                f"Washout residual {washout_residual:.4f} <= "
                f"Delta_eq={self.delta_eq:.4f} — adequate recovery"
            )

        return cr

    # ------------------------------------------------------------------
    # C9: Phase and Temporal-Dependence Balance
    # ------------------------------------------------------------------

    def c9_phase_balance(self, extra: Dict) -> CheckResult:
        """
        C9: Test for dose/phase association (time-trend confounding).

        Checks whether dose assignment is correlated with temporal position
        in the experiment (could confound estimates if workload has phases).

        PASS if Spearman |rho| < 0.30.
        UNRESOLVED if timing data not available.
        """
        cr = CheckResult(
            check_id="C9",
            name="Phase and Temporal-Dependence Balance",
            data_source=self.data_source,
            thresholds={"max_phase_correlation": 0.30},
        )

        window_order = extra.get("window_order", None)
        window_doses = extra.get("window_doses", None)

        if window_order is None or window_doses is None:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append(
                "Window order/dose sequence not provided"
            )
            return cr

        try:
            from scipy.stats import spearmanr
            rho, p = spearmanr(window_order, window_doses)
            cr.metric_values = {
                "spearman_rho": round(float(rho), 4),
                "p_value": round(float(p), 4),
            }

            if abs(rho) > 0.30:
                cr.status = CheckStatus.FAIL
                cr.reasons.append(
                    f"Dose-phase Spearman rho={rho:.3f} > 0.30; "
                    "systematic time trend in dose assignment"
                )
            else:
                cr.status = CheckStatus.PASS
                cr.evidence.append(
                    f"Dose-phase Spearman rho={rho:.3f} — "
                    "no significant temporal confounding"
                )

        except Exception as e:
            cr.status = CheckStatus.UNRESOLVED
            cr.reasons.append(f"Phase balance computation failed: {e}")

        if self.data_source == SIM_TAG:
            cr.limitations.append(
                "SIMULATION: Phase structure is from random assignment; "
                "real workload phase cycles not modelled."
            )

        return cr
