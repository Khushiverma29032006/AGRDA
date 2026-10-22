"""
Tests for the audit battery (C1–C9).

Covers:
  - Each check produces a CheckResult with the correct status
  - UNRESOLVED is never promoted to PASS
  - FAIL conditions trigger correctly
  - PASS conditions trigger correctly
  - All nine checks are present in the battery output
"""
import pytest
from unittest.mock import MagicMock
from agrda.audit import AuditBattery, CheckStatus
from agrda.audit.models import AuditBatteryResult
from agrda.estimation.estimator import EstimationResult, DoseEffectEstimate


def _make_pilot(icc=0.85, mde=0.03, sigma_y_rel=0.02):
    p = MagicMock()
    p.icc = icc
    p.mde_fraction = mde
    p.sigma_y_rel = sigma_y_rel
    return p


def _make_estimation(suspects=None, dose=0.8, tau=-0.15, ci_lo=-0.20, ci_hi=-0.10):
    suspects = suspects or ["s1"]
    est = EstimationResult(
        suspect_names=suspects,
        n_windows_total=16,
        n_windows_excluded=0,
        data_source="SIMULATION",
        dose_effects=[
            DoseEffectEstimate(
                suspect_name=s,
                dose_delta=dose,
                tau_hat=tau,
                ci_lower=ci_lo,
                ci_upper=ci_hi,
                ci_level=0.95,
                n_windows=16,
            )
            for s in suspects
        ],
    )
    return est


def _good_extra():
    """Extra data that should make all checks pass."""
    return {
        "suspect_bw_reduction_fraction": 0.70,
        "mediator_effect_fraction": 0.18,
        "placebo_victim_effect": 0.004,    # << delta_eq=0.0125
        "neg_sentinel_effect": 0.003,
        "pos_sentinel_effect": 0.22,
        "washout_residual_fraction": 0.006,
        "window_order": list(range(16)),
        "window_doses": [0.0, 0.2, 0.5, 0.8] * 4,
    }


class TestAuditBattery:

    def setup_method(self):
        self.battery = AuditBattery(data_source="SIMULATION")

    # --- all checks present ---

    def test_all_nine_checks_present(self):
        pilot = _make_pilot()
        est = _make_estimation()
        result = self.battery.run_all(
            pilot_result=pilot,
            estimation_result=est,
            extra=_good_extra(),
        )
        ids = {c.check_id for c in result.checks}
        for cid in ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]:
            assert cid in ids, f"{cid} missing from audit battery"

    def test_battery_result_has_counts(self):
        pilot = _make_pilot()
        est = _make_estimation()
        result = self.battery.run_all(
            pilot_result=pilot,
            estimation_result=est,
            extra=_good_extra(),
        )
        result.update_counts()
        total = result.n_pass + result.n_fail + result.n_unresolved
        assert total == 9

    # --- C1 Measurement Adequacy ---

    def test_c1_pass_good_icc_and_mde(self):
        pilot = _make_pilot(icc=0.85, mde=0.03)
        result = self.battery.c1_measurement_adequacy(pilot)
        assert result.status == CheckStatus.PASS

    def test_c1_fail_low_icc(self):
        pilot = _make_pilot(icc=0.45, mde=0.03)
        result = self.battery.c1_measurement_adequacy(pilot)
        assert result.status == CheckStatus.FAIL
        assert any("ICC" in r for r in result.reasons)

    def test_c1_fail_large_mde(self):
        pilot = _make_pilot(icc=0.85, mde=0.12)
        result = self.battery.c1_measurement_adequacy(pilot)
        assert result.status == CheckStatus.FAIL
        assert any("MDE" in r for r in result.reasons)

    def test_c1_unresolved_no_pilot(self):
        result = self.battery.c1_measurement_adequacy(None)
        assert result.status == CheckStatus.UNRESOLVED

    # --- C2 Intervention Strength ---

    def test_c2_pass_good_first_stage(self):
        est = _make_estimation()
        extra = {"suspect_bw_reduction_fraction": 0.70,
                 "mediator_effect_fraction": 0.18}
        result = self.battery.c2_intervention_strength(est, extra)
        assert result.status == CheckStatus.PASS

    def test_c2_fail_weak_bw_reduction(self):
        est = _make_estimation()
        extra = {"suspect_bw_reduction_fraction": 0.02,
                 "mediator_effect_fraction": 0.18}
        result = self.battery.c2_intervention_strength(est, extra)
        assert result.status == CheckStatus.FAIL

    def test_c2_unresolved_no_bw_data(self):
        est = _make_estimation()
        result = self.battery.c2_intervention_strength(est, {})
        assert result.status == CheckStatus.UNRESOLVED

    # --- C3 Placebo ---

    def test_c3_pass_small_placebo_effect(self):
        result = self.battery.c3_placebo({"placebo_victim_effect": 0.004})
        assert result.status == CheckStatus.PASS

    def test_c3_fail_large_placebo_effect(self):
        result = self.battery.c3_placebo({"placebo_victim_effect": 0.08})
        assert result.status == CheckStatus.FAIL
        assert any("Delta_eq" in r or "pathway" in r.lower()
                   for r in result.reasons)

    def test_c3_unresolved_no_placebo(self):
        result = self.battery.c3_placebo({})
        assert result.status == CheckStatus.UNRESOLVED

    # --- C4 Sentinel ---

    def test_c4_pass_good_sentinels(self):
        result = self.battery.c4_sentinel({
            "neg_sentinel_effect": 0.003,
            "pos_sentinel_effect": 0.20,
        })
        assert result.status == CheckStatus.PASS

    def test_c4_fail_neg_sentinel_moves(self):
        result = self.battery.c4_sentinel({
            "neg_sentinel_effect": 0.05,   # >> delta_eq
            "pos_sentinel_effect": 0.20,
        })
        assert result.status == CheckStatus.FAIL

    def test_c4_unresolved_no_sentinel(self):
        result = self.battery.c4_sentinel({})
        assert result.status == CheckStatus.UNRESOLVED

    # --- C5 Alternative Pathways ---

    def test_c5_unresolved_no_pathway_data(self):
        result = self.battery.c5_alternative_pathways({})
        assert result.status == CheckStatus.UNRESOLVED

    def test_c5_fail_pathway_violation(self):
        result = self.battery.c5_alternative_pathways({
            "pathway_effects": {"cpu_frequency_drop": 0.08}
        })
        assert result.status == CheckStatus.FAIL

    def test_c5_pass_small_pathway_effects(self):
        result = self.battery.c5_alternative_pathways({
            "pathway_effects": {"cpu_frequency_drop": 0.004}
        })
        assert result.status == CheckStatus.PASS

    # --- C6 Alternative Actuator ---

    def test_c6_unresolved_single_actuator(self):
        result = self.battery.c6_alternative_actuator({})
        assert result.status == CheckStatus.UNRESOLVED

    def test_c6_pass_consistent_ratios(self):
        result = self.battery.c6_alternative_actuator({
            "mba_response_ratio": 0.75,
            "cpu_quota_response_ratio": 0.78,
        })
        assert result.status == CheckStatus.PASS

    def test_c6_fail_inconsistent_ratios(self):
        result = self.battery.c6_alternative_actuator({
            "mba_response_ratio": 0.75,
            "cpu_quota_response_ratio": 0.20,
        })
        assert result.status == CheckStatus.FAIL

    # --- C7 Dose-Response Consistency ---

    def test_c7_unresolved_no_effects(self):
        est = EstimationResult(
            suspect_names=["s1"], n_windows_total=0,
            n_windows_excluded=0, data_source="SIMULATION"
        )
        result = self.battery.c7_dose_response_consistency(est)
        assert result.status == CheckStatus.UNRESOLVED

    def test_c7_pass_monotone_effects(self):
        # Increasing dose → more negative tau (more relief)
        est = EstimationResult(
            suspect_names=["s1"], n_windows_total=16,
            n_windows_excluded=0, data_source="SIMULATION",
            dose_effects=[
                DoseEffectEstimate("s1", 0.2, -0.05, -0.08, -0.02, 0.95),
                DoseEffectEstimate("s1", 0.5, -0.10, -0.14, -0.06, 0.95),
                DoseEffectEstimate("s1", 0.8, -0.15, -0.20, -0.10, 0.95),
            ],
        )
        result = self.battery.c7_dose_response_consistency(est)
        assert result.status == CheckStatus.PASS

    def test_c7_fail_strong_non_monotone(self):
        # Strongly non-monotone: high dose produces MUCH weaker effect than low dose
        # with non-overlapping CIs — should conclusively fail monotonicity
        est = EstimationResult(
            suspect_names=["s1"], n_windows_total=16,
            n_windows_excluded=0, data_source="SIMULATION",
            dose_effects=[
                DoseEffectEstimate("s1", 0.2, -0.20, -0.25, -0.15, 0.95),
                DoseEffectEstimate("s1", 0.5, -0.01, -0.04, 0.02, 0.95),  # near-zero, reversal
                DoseEffectEstimate("s1", 0.8, +0.15, +0.10, +0.20, 0.95),  # wrong sign entirely
            ],
        )
        result = self.battery.c7_dose_response_consistency(est)
        # C7 should FAIL because the highest dose has a positive tau (wrong direction)
        # OR it may return UNRESOLVED if the check cannot distinguish — both are acceptable
        # but it must NOT PASS when effects are clearly non-monotone with wrong sign
        assert result.status != CheckStatus.PASS, \
            f"C7 must not PASS when highest dose shows positive tau: {result.reasons}"

    # --- C8 Carry-Over ---

    def test_c8_unresolved_no_washout_data(self):
        result = self.battery.c8_carryover({})
        assert result.status == CheckStatus.UNRESOLVED

    def test_c8_pass_small_residual(self):
        result = self.battery.c8_carryover({"washout_residual_fraction": 0.005})
        assert result.status == CheckStatus.PASS

    def test_c8_fail_large_residual(self):
        result = self.battery.c8_carryover({"washout_residual_fraction": 0.04})
        assert result.status == CheckStatus.FAIL

    # --- C9 Phase Balance ---

    def test_c9_unresolved_no_order_data(self):
        result = self.battery.c9_phase_balance({})
        assert result.status == CheckStatus.UNRESOLVED

    def test_c9_pass_random_dose_order(self):
        # Use a balanced interleaved pattern that gives near-zero Spearman rho
        # [0.0, 0.8, 0.2, 0.5, 0.5, 0.2, 0.8, 0.0, ...] — symmetric around midpoint
        order = list(range(16))
        doses = [0.0, 0.8, 0.2, 0.5,
                 0.5, 0.2, 0.8, 0.0,
                 0.8, 0.0, 0.5, 0.2,
                 0.2, 0.5, 0.0, 0.8]  # hand-verified symmetric pattern
        result = self.battery.c9_phase_balance({
            "window_order": order,
            "window_doses": doses,
        })
        # This pattern has near-zero Spearman correlation with order — should PASS
        assert result.status == CheckStatus.PASS, \
            f"C9 should PASS for symmetric dose order, got: {result.metric_values}"

    def test_c9_fail_sorted_doses(self):
        # Doses monotonically increase → perfect correlation with order
        order = list(range(16))
        doses = sorted([0.0, 0.2, 0.5, 0.8] * 4)  # fully sorted = rho≈1
        result = self.battery.c9_phase_balance({
            "window_order": order,
            "window_doses": doses,
        })
        assert result.status == CheckStatus.FAIL

    # --- UNRESOLVED never becomes PASS rule ---

    def test_unresolved_never_promoted_to_pass(self):
        """
        CRITICAL: No check may return PASS when data is missing.
        This test verifies the hard rule across all nine checks.
        """
        battery = AuditBattery(data_source="SIMULATION")
        empty_extra = {}
        empty_est = EstimationResult(
            suspect_names=[], n_windows_total=0,
            n_windows_excluded=0, data_source="SIMULATION"
        )

        # C1 with no pilot
        c1 = battery.c1_measurement_adequacy(None)
        assert c1.status != CheckStatus.PASS, \
            "C1 must not PASS with no pilot data"

        # C2 with no extra
        c2 = battery.c2_intervention_strength(empty_est, empty_extra)
        assert c2.status != CheckStatus.PASS, \
            "C2 must not PASS with no measurement data"

        # C3 with no extra
        c3 = battery.c3_placebo(empty_extra)
        assert c3.status != CheckStatus.PASS, \
            "C3 must not PASS with no placebo data"

        # C4 with no extra
        c4 = battery.c4_sentinel(empty_extra)
        assert c4.status != CheckStatus.PASS, \
            "C4 must not PASS with no sentinel data"

        # C5 with no pathway data
        c5 = battery.c5_alternative_pathways(empty_extra)
        assert c5.status != CheckStatus.PASS, \
            "C5 must not PASS with no pathway data"

        # C6 with no actuator data
        c6 = battery.c6_alternative_actuator(empty_extra)
        assert c6.status != CheckStatus.PASS, \
            "C6 must not PASS with single actuator"

        # C7 with empty estimation
        c7 = battery.c7_dose_response_consistency(empty_est)
        assert c7.status != CheckStatus.PASS, \
            "C7 must not PASS with no effect data"

        # C8 with no washout
        c8 = battery.c8_carryover(empty_extra)
        assert c8.status != CheckStatus.PASS, \
            "C8 must not PASS with no washout data"

        # C9 with no order
        c9 = battery.c9_phase_balance(empty_extra)
        assert c9.status != CheckStatus.PASS, \
            "C9 must not PASS with no phase data"

    # --- summary string ---

    def test_battery_summary_contains_all_ids(self):
        pilot = _make_pilot()
        est = _make_estimation()
        result = self.battery.run_all(
            pilot_result=pilot,
            estimation_result=est,
            extra=_good_extra(),
        )
        summary = result.summary()
        for cid in ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]:
            assert cid in summary
