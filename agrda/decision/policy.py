"""
Five-outcome decision policy (Section 7.6 of AGRDA document).

Outcomes and precedence:
  O1 Inconclusive  – design gate fails, ambiguous CI, or required
                     consistency checks fail.
  O2 Null          – all design gates pass, top-dose effect CI wholly
                     within (-Delta_eff, +Delta_eff).
  O3 Pathway violation detected – relevant effect established AND
                     C3, C4, or C5 FAILS conclusively.
  O4 Supported memory-specific attribution – relevant effect,
                     all design gates pass, required pathway and
                     consistency checks pass.
                     Tier 1: two-actuator comparison possible (C6 pass).
                     Tier 2: single actuator only.
  O5 Intervention effect only – relevant effect, design gates pass,
                     no check FAILS, but ≥1 pathway/consistency check
                     UNRESOLVED.

Precedence (higher number = evaluated later, but O1 has highest
override priority):
  1. If any required DESIGN GATE fails → O1.
  2. If CI is ambiguous → O1.
  3. If pathway violation (C3/C4/C5 FAIL) AND effect established → O3.
  4. If effect established AND all required checks PASS → O4.
  5. If effect established AND gates pass AND ≥1 check UNRESOLVED → O5.
  6. If gates pass AND CI wholly inside (-Delta_eff, +Delta_eff) → O2.
  7. Otherwise → O1.

RULES:
  - Never promote UNRESOLVED to PASS.
  - State WHY the outcome was selected and WHAT it does/doesn't support.
  - UNRESOLVED checks do not prevent O4 if all required checks that CAN
    be evaluated PASS — BUT the report must disclose what is unresolved.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import List, Optional

from ..audit.models import AuditBatteryResult, CheckStatus


class Outcome(enum.Enum):
    O1_INCONCLUSIVE = "O1_inconclusive"
    O2_NULL = "O2_null"
    O3_PATHWAY_VIOLATION = "O3_pathway_violation"
    O4_SUPPORTED_ATTRIBUTION = "O4_supported_attribution"
    O5_INTERVENTION_EFFECT_ONLY = "O5_intervention_effect_only"


@dataclass
class DecisionResult:
    """
    Output of the five-outcome decision policy.
    """
    outcome: Outcome
    tier: Optional[str] = None    # "tier1" or "tier2" for O4

    # What the outcome claims and does NOT claim
    supported_claims: List[str] = field(default_factory=list)
    unsupported_claims: List[str] = field(default_factory=list)

    # Why this outcome was selected (precedence trace)
    reason: str = ""
    decision_trace: List[str] = field(default_factory=list)

    # Underlying audit and effect info
    n_checks_pass: int = 0
    n_checks_fail: int = 0
    n_checks_unresolved: int = 0
    top_dose_tau: Optional[float] = None
    top_dose_ci_lower: Optional[float] = None
    top_dose_ci_upper: Optional[float] = None
    delta_eff: float = 0.05

    data_source: str = "UNKNOWN"

    def summary(self) -> str:
        lines = [
            f"Outcome    : {self.outcome.value}",
            f"Tier       : {self.tier or 'N/A'}",
            f"Reason     : {self.reason}",
            "",
            "Supported claims:",
        ]
        for c in self.supported_claims:
            lines.append(f"  ✓ {c}")
        lines.append("NOT supported:")
        for c in self.unsupported_claims:
            lines.append(f"  ✗ {c}")
        lines.append("")
        lines.append("Decision trace:")
        for t in self.decision_trace:
            lines.append(f"  → {t}")
        return "\n".join(lines)


class DecisionPolicy:
    """
    Applies the five-outcome policy given audit battery and effect estimates.

    Parameters
    ----------
    delta_eff : relevance threshold
    """

    # Required design gates (must not FAIL for any outcome except O1)
    DESIGN_GATES = {"C1", "C2"}

    # Pathway checks (FAIL → O3)
    PATHWAY_CHECKS = {"C3", "C4", "C5"}

    # Consistency checks
    CONSISTENCY_CHECKS = {"C7", "C8", "C9"}

    def __init__(self, delta_eff: float = 0.05) -> None:
        self.delta_eff = delta_eff

    def decide(
        self,
        audit: AuditBatteryResult,
        top_dose_tau: Optional[float],
        top_dose_ci_lower: Optional[float],
        top_dose_ci_upper: Optional[float],
        data_source: str = "SIMULATION",
    ) -> DecisionResult:
        """
        Apply the five-outcome policy.

        Parameters
        ----------
        audit           : AuditBatteryResult from AuditBattery.run_all()
        top_dose_tau    : point estimate at highest dose (fraction of baseline)
        top_dose_ci_*   : CI bounds for top dose effect
        data_source     : "SIMULATION" or "REAL"
        """
        audit.update_counts()
        trace: List[str] = []

        result = DecisionResult(
            outcome=Outcome.O1_INCONCLUSIVE,
            n_checks_pass=audit.n_pass,
            n_checks_fail=audit.n_fail,
            n_checks_unresolved=audit.n_unresolved,
            top_dose_tau=top_dose_tau,
            top_dose_ci_lower=top_dose_ci_lower,
            top_dose_ci_upper=top_dose_ci_upper,
            delta_eff=self.delta_eff,
            data_source=data_source,
        )

        # --- STEP 1: Check design gates ---
        for gate_id in self.DESIGN_GATES:
            check = audit.get(gate_id)
            if check is None:
                trace.append(
                    f"Design gate {gate_id} not in audit — INCONCLUSIVE"
                )
                result.reason = f"Required design gate {gate_id} missing"
                result.decision_trace = trace
                self._set_o1_claims(result)
                return result
            if check.is_fail():
                trace.append(
                    f"Design gate {gate_id} FAILED — O1 Inconclusive"
                )
                result.reason = (
                    f"Design gate {gate_id} ({check.name}) failed: "
                    + "; ".join(check.reasons)
                )
                result.decision_trace = trace
                self._set_o1_claims(result)
                return result
            trace.append(f"Design gate {gate_id}: {check.status.value}")

        # --- STEP 2: Check effect estimate available ---
        if top_dose_tau is None or top_dose_ci_lower is None:
            trace.append("No effect estimate available — O1 Inconclusive")
            result.reason = "Effect estimate not available"
            result.decision_trace = trace
            self._set_o1_claims(result)
            return result

        # --- STEP 3: Is the effect relevant? ---
        # Relevant = CI does NOT wholly fall in (-Delta_eff, +Delta_eff)
        # i.e., at least some part of CI exceeds threshold
        ci_lo = top_dose_ci_lower
        ci_hi = top_dose_ci_upper if top_dose_ci_upper is not None \
            else top_dose_tau

        effect_relevant = not (
            -self.delta_eff <= ci_lo and ci_hi <= self.delta_eff
        )
        effect_null = (
            -self.delta_eff <= ci_lo and ci_hi <= self.delta_eff
        )
        ci_ambiguous = (
            ci_lo < -self.delta_eff and ci_hi > self.delta_eff
        ) or (
            # CI straddles zero and threshold, unclear
            ci_lo < 0 and ci_hi > self.delta_eff
        )

        trace.append(
            f"Effect CI=[{ci_lo:.4f}, {ci_hi:.4f}], Delta_eff={self.delta_eff:.4f}"
        )
        trace.append(
            f"Effect relevant={effect_relevant}, null={effect_null}, "
            f"ambiguous={ci_ambiguous}"
        )

        if ci_ambiguous:
            trace.append("CI ambiguous — O1 Inconclusive")
            result.reason = (
                f"CI [{ci_lo:.4f}, {ci_hi:.4f}] is ambiguous relative to "
                f"Delta_eff={self.delta_eff:.4f}"
            )
            result.decision_trace = trace
            self._set_o1_claims(result)
            return result

        # --- STEP 4: Null outcome ---
        if effect_null:
            trace.append("Effect null — O2 Null")
            result.outcome = Outcome.O2_NULL
            result.reason = (
                f"CI [{ci_lo:.4f}, {ci_hi:.4f}] wholly within "
                f"(-{self.delta_eff:.4f}, +{self.delta_eff:.4f}) — "
                "no relevant effect detected"
            )
            result.supported_claims = [
                "No memory-specific attribution supported at this dose level",
                "Suspect appears not to meaningfully interfere at tested doses",
            ]
            result.unsupported_claims = [
                "Absence of effect under intervention does not prove suspect "
                "contributes zero interference at all doses",
            ]
            result.decision_trace = trace
            return result

        # --- STEP 5: Effect relevant — check pathway checks ---
        pathway_conclusive_fail = any(
            audit.get(cid) is not None and audit.get(cid).is_fail()
            for cid in self.PATHWAY_CHECKS
        )

        if pathway_conclusive_fail:
            failing = [
                cid for cid in self.PATHWAY_CHECKS
                if audit.get(cid) is not None and audit.get(cid).is_fail()
            ]
            trace.append(
                f"Pathway checks FAILED: {failing} — O3 Pathway Violation"
            )
            result.outcome = Outcome.O3_PATHWAY_VIOLATION
            result.reason = (
                f"Relevant intervention effect established but pathway "
                f"check(s) {failing} FAILED conclusively"
            )
            result.supported_claims = [
                "Intervention affects victim performance",
                f"Pathway violation detected via: {failing}",
                "Memory-specific attribution NOT supported",
            ]
            result.unsupported_claims = [
                "Memory pathway is not the sole explanation for the effect",
                "AGRDA audit does NOT support memory-specific attribution here",
            ]
            result.decision_trace = trace
            return result

        # --- STEP 6: Check consistency checks ---
        consistency_unresolved = any(
            audit.get(cid) is not None and audit.get(cid).is_unresolved()
            for cid in self.CONSISTENCY_CHECKS
        )
        pathway_unresolved = any(
            audit.get(cid) is not None and audit.get(cid).is_unresolved()
            for cid in self.PATHWAY_CHECKS
        )
        any_unresolved = consistency_unresolved or pathway_unresolved

        # Check if C6 (alternative actuator) passes for tier determination
        c6 = audit.get("C6")
        c6_pass = c6 is not None and c6.is_pass()

        all_required_pass = not any_unresolved and not pathway_conclusive_fail

        if not any_unresolved:
            # O4: Supported attribution
            tier = "tier1" if c6_pass else "tier2"
            trace.append(
                f"All pathway/consistency checks pass — O4 Supported "
                f"Attribution ({tier})"
            )
            result.outcome = Outcome.O4_SUPPORTED_ATTRIBUTION
            result.tier = tier
            result.reason = (
                "Relevant effect established; all evaluable design gates, "
                "pathway, and consistency checks PASS. "
                f"C6 (alternative actuator): {'PASS' if c6_pass else 'UNRESOLVED → tier2'}."
            )
            result.supported_claims = [
                "Relevant intervention effect on victim performance established",
                "Placebo and sentinel checks consistent with memory pathway",
                "Dose-response consistent with memory interference",
                "CONDITIONAL memory-specific attribution SUPPORTED "
                "(subject to stated assumptions and limitations)",
            ]
            result.unsupported_claims = [
                "Exclusion restriction is not proven by passing audit",
                "Total contribution cannot be extrapolated from dose effects "
                "without a justified model",
                "Memory-specific attribution is not guaranteed; it is "
                "conditionally supported given the audit evidence",
            ]
            result.decision_trace = trace
            return result
        else:
            # O5: Intervention effect only
            unresolved_ids = [
                cid for cid in (self.PATHWAY_CHECKS | self.CONSISTENCY_CHECKS)
                if audit.get(cid) is not None and audit.get(cid).is_unresolved()
            ]
            trace.append(
                f"Checks UNRESOLVED: {unresolved_ids} — O5 Intervention Effect Only"
            )
            result.outcome = Outcome.O5_INTERVENTION_EFFECT_ONLY
            result.reason = (
                "Relevant effect established; design gates pass; "
                f"no check FAILS conclusively; but "
                f"{unresolved_ids} UNRESOLVED"
            )
            result.supported_claims = [
                "Relevant intervention effect on victim performance established",
                "No conclusive pathway violation detected",
            ]
            result.unsupported_claims = [
                f"Memory-specific attribution NOT supported (checks {unresolved_ids} unresolved)",
                "UNRESOLVED checks are NOT treated as passing",
                "Full audit cannot be completed on this platform",
            ]
            result.decision_trace = trace
            return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_o1_claims(self, result: DecisionResult) -> None:
        result.outcome = Outcome.O1_INCONCLUSIVE
        result.supported_claims = []
        result.unsupported_claims = [
            "No attribution claim (memory-specific or otherwise) is supported",
            "Experiment must be redesigned or re-run before conclusions can "
            "be drawn",
        ]
