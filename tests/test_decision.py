"""
Tests for the five-outcome decision policy (O1–O5).

Covers every branch and precedence rule from Section 7.6 of the
AGRDA document:
  1. Design gate FAIL → O1
  2. No effect estimate → O1
  3. CI ambiguous → O1
  4. Null effect (CI inside ±Delta_eff) → O2
  5. Pathway violation (C3/C4/C5 FAIL) → O3
  6. All checks pass → O4 (tier1/tier2)
  7. Checks UNRESOLVED but no FAIL → O5

Rules verified:
  - UNRESOLVED never treated as PASS
  - Tier1 vs Tier2 distinction for O4
  - Each outcome states supported and unsupported claims
"""
import pytest
from agrda.decision import DecisionPolicy, Outcome
from agrda.audit.models import (
    AuditBatteryResult, CheckResult, CheckStatus,
)


def _make_audit(
    c1="PASS", c2="PASS", c3="PASS", c4="PASS", c5="UNRESOLVED",
    c6="UNRESOLVED", c7="PASS", c8="PASS", c9="PASS",
) -> AuditBatteryResult:
    """Build an AuditBatteryResult from simple status strings."""
    check_map = {
        "C1": ("C1", "Measurement Adequacy", c1),
        "C2": ("C2", "Intervention Strength", c2),
        "C3": ("C3", "Placebo Actuation", c3),
        "C4": ("C4", "Sentinel", c4),
        "C5": ("C5", "Alternative Pathways", c5),
        "C6": ("C6", "Alternative Actuator", c6),
        "C7": ("C7", "Dose-Response Consistency", c7),
        "C8": ("C8", "Carry-Over", c8),
        "C9": ("C9", "Phase Balance", c9),
    }
    battery = AuditBatteryResult(data_source="SIMULATION")
    for cid, (check_id, name, status) in check_map.items():
        battery.checks.append(CheckResult(
            check_id=check_id,
            name=name,
            status=CheckStatus[status],
            reasons=[] if status != "FAIL" else [f"{check_id} failed in test"],
            data_source="SIMULATION",
        ))
    battery.update_counts()
    return battery


DELTA = 0.05   # Delta_eff

# Typical relevant effect: tau=-0.15, CI=[-0.20, -0.10]
RELEVANT_TAU = -0.15
RELEVANT_CI_LO = -0.20
RELEVANT_CI_HI = -0.10

# Null effect: tau=-0.01, CI=[-0.02, 0.01]
NULL_TAU = -0.01
NULL_CI_LO = -0.02
NULL_CI_HI = 0.01


class TestDecisionPolicy:

    def setup_method(self):
        self.policy = DecisionPolicy(delta_eff=DELTA)

    # ------------------------------------------------------------------
    # O1 – Inconclusive
    # ------------------------------------------------------------------

    def test_o1_when_c1_fails(self):
        """Design gate C1 FAIL → O1 regardless of effect."""
        audit = _make_audit(c1="FAIL")
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O1_INCONCLUSIVE

    def test_o1_when_c2_fails(self):
        """Design gate C2 FAIL → O1."""
        audit = _make_audit(c2="FAIL")
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O1_INCONCLUSIVE

    def test_o1_when_no_effect_estimate(self):
        """Missing effect estimate → O1."""
        audit = _make_audit()
        result = self.policy.decide(audit, None, None, None)
        assert result.outcome == Outcome.O1_INCONCLUSIVE

    def test_o1_when_ci_ambiguous(self):
        """CI spanning both below and above Delta_eff → O1."""
        audit = _make_audit()
        # CI goes from -0.10 to +0.10, ambiguous
        result = self.policy.decide(audit, 0.0, -0.10, 0.10)
        assert result.outcome == Outcome.O1_INCONCLUSIVE

    def test_o1_has_no_attribution_claims(self):
        """O1 must make no attribution claims."""
        audit = _make_audit(c1="FAIL")
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O1_INCONCLUSIVE
        assert len(result.supported_claims) == 0 or all(
            "attribution" not in c.lower() for c in result.supported_claims
        )

    # ------------------------------------------------------------------
    # O2 – Null
    # ------------------------------------------------------------------

    def test_o2_null_effect(self):
        """CI wholly within ±Delta_eff → O2."""
        audit = _make_audit()
        result = self.policy.decide(audit, NULL_TAU, NULL_CI_LO, NULL_CI_HI)
        assert result.outcome == Outcome.O2_NULL

    def test_o2_supported_claims_deny_attribution(self):
        """O2 must state suspect is not meaningfully interfering."""
        audit = _make_audit()
        result = self.policy.decide(audit, NULL_TAU, NULL_CI_LO, NULL_CI_HI)
        assert result.outcome == Outcome.O2_NULL
        combined = " ".join(result.supported_claims).lower()
        assert "null" in combined or "no relevant effect" in combined \
            or "not" in combined

    # ------------------------------------------------------------------
    # O3 – Pathway Violation
    # ------------------------------------------------------------------

    def test_o3_when_c3_fails(self):
        """Relevant effect + C3 FAIL → O3."""
        audit = _make_audit(c3="FAIL")
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O3_PATHWAY_VIOLATION

    def test_o3_when_c4_fails(self):
        """Relevant effect + C4 FAIL → O3."""
        audit = _make_audit(c4="FAIL")
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O3_PATHWAY_VIOLATION

    def test_o3_when_c5_fails(self):
        """Relevant effect + C5 FAIL → O3."""
        audit = _make_audit(c5="FAIL")
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O3_PATHWAY_VIOLATION

    def test_o3_claims_memory_attribution_not_supported(self):
        """O3 must explicitly state memory-specific attribution is NOT supported."""
        audit = _make_audit(c3="FAIL")
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O3_PATHWAY_VIOLATION
        unsupported_text = " ".join(result.unsupported_claims).lower()
        assert "memory" in unsupported_text or "pathway" in unsupported_text

    # ------------------------------------------------------------------
    # O4 – Supported Attribution
    # ------------------------------------------------------------------

    def test_o4_when_all_checks_pass(self):
        """All checks PASS (some UNRESOLVED) → O4."""
        audit = _make_audit(
            c1="PASS", c2="PASS", c3="PASS", c4="PASS",
            c5="PASS", c6="UNRESOLVED", c7="PASS", c8="PASS", c9="PASS",
        )
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O4_SUPPORTED_ATTRIBUTION

    def test_o4_tier2_when_c6_unresolved(self):
        """C6 UNRESOLVED → tier2 for O4 (single actuator)."""
        audit = _make_audit(
            c1="PASS", c2="PASS", c3="PASS", c4="PASS",
            c5="PASS", c6="UNRESOLVED", c7="PASS", c8="PASS", c9="PASS",
        )
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O4_SUPPORTED_ATTRIBUTION
        assert result.tier == "tier2"

    def test_o4_tier1_when_c6_passes(self):
        """C6 PASS → tier1 for O4 (both actuators)."""
        audit = _make_audit(
            c1="PASS", c2="PASS", c3="PASS", c4="PASS",
            c5="PASS", c6="PASS", c7="PASS", c8="PASS", c9="PASS",
        )
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O4_SUPPORTED_ATTRIBUTION
        assert result.tier == "tier1"

    def test_o4_claims_conditional_not_proven(self):
        """O4 must state attribution is conditional, not proven."""
        audit = _make_audit(
            c1="PASS", c2="PASS", c3="PASS", c4="PASS",
            c5="PASS", c6="UNRESOLVED", c7="PASS", c8="PASS", c9="PASS",
        )
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        unsupported_text = " ".join(result.unsupported_claims).lower()
        # Must explicitly say exclusion restriction is not proven
        assert "exclusion" in unsupported_text or "not guaranteed" in unsupported_text \
            or "conditional" in unsupported_text

    # ------------------------------------------------------------------
    # O5 – Intervention Effect Only
    # ------------------------------------------------------------------

    def test_o5_when_pathway_checks_unresolved(self):
        """Relevant effect + pathway checks UNRESOLVED (not FAIL) → O5."""
        # Default _make_audit has C5=UNRESOLVED, C6=UNRESOLVED
        audit = _make_audit()  # C5=UNRESOLVED, C6=UNRESOLVED by default
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O5_INTERVENTION_EFFECT_ONLY

    def test_o5_claims_do_not_include_memory_attribution(self):
        """O5 must NOT claim memory-specific attribution."""
        audit = _make_audit()  # UNRESOLVED pathway checks
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O5_INTERVENTION_EFFECT_ONLY
        unsupported_text = " ".join(result.unsupported_claims).lower()
        assert "memory-specific attribution" in unsupported_text \
            or "not supported" in unsupported_text

    # ------------------------------------------------------------------
    # Precedence rules
    # ------------------------------------------------------------------

    def test_design_gate_overrides_pathway_violation(self):
        """C1 FAIL overrides C3 FAIL – design gate has highest precedence."""
        audit = _make_audit(c1="FAIL", c3="FAIL")
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O1_INCONCLUSIVE

    def test_pathway_violation_overrides_o5(self):
        """O3 (pathway violation) takes precedence over O5."""
        audit = _make_audit(c3="FAIL", c5="UNRESOLVED", c6="UNRESOLVED")
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.outcome == Outcome.O3_PATHWAY_VIOLATION

    def test_null_result_has_no_tier(self):
        audit = _make_audit()
        result = self.policy.decide(audit, NULL_TAU, NULL_CI_LO, NULL_CI_HI)
        assert result.outcome == Outcome.O2_NULL
        assert result.tier is None

    # ------------------------------------------------------------------
    # Decision trace
    # ------------------------------------------------------------------

    def test_decision_trace_non_empty(self):
        """All outcomes must produce a non-empty decision trace."""
        audit = _make_audit()
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert len(result.decision_trace) > 0

    def test_decision_reason_non_empty(self):
        audit = _make_audit()
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        assert result.reason != ""

    # ------------------------------------------------------------------
    # Summary output
    # ------------------------------------------------------------------

    def test_summary_contains_outcome(self):
        audit = _make_audit()
        result = self.policy.decide(
            audit, RELEVANT_TAU, RELEVANT_CI_LO, RELEVANT_CI_HI
        )
        summary = result.summary()
        assert result.outcome.value in summary
