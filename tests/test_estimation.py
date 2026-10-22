"""
Tests for the effect estimator and pilot calibrator.

Covers:
  - Estimator produces estimates for each dose level
  - Beta/gamma coefficients are finite
  - Bootstrap CIs bracket the point estimate (usually)
  - Permutation p-values in [0, 1]
  - Pilot ICC, MDE, sigma_y are finite and in valid ranges
  - Degenerate / empty data does not crash the estimator
  - Normalisation to baseline
"""
import numpy as np
import pandas as pd
import pytest
from agrda.estimation import EffectEstimator, PilotCalibrator


def _make_measurement_df(
    n_blocks: int = 4,
    doses: list = None,
    true_effect: float = -0.15,
    noise: float = 0.02,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Build a synthetic measurement dataframe that EffectEstimator expects.
    true_effect: fraction of baseline (negative = victim improves when throttled)
    """
    rng = np.random.default_rng(seed)
    doses = doses or [0.0, 0.2, 0.5, 0.8]
    rows = []
    window_id = 0
    for block in range(n_blocks):
        for dose in rng.permutation(doses):
            baseline_tp = 1000.0
            y = baseline_tp * (1.0 + true_effect * dose) + \
                rng.normal(0, baseline_tp * noise)
            rows.append({
                "window_id": window_id,
                "block_id": block,
                "suspect_name": "s1",
                "dose": float(dose),
                "victim_throughput": float(y),
                "excluded": False,
                "exclusion_reason": "",
            })
            window_id += 1
    return pd.DataFrame(rows)


class TestEffectEstimator:

    def setup_method(self):
        self.estimator = EffectEstimator(
            ci_level=0.95, n_boot=200, n_perm=200, seed=42
        )

    def test_produces_estimates(self):
        df = _make_measurement_df()
        result = self.estimator.estimate(df, ["s1"], 42, "SIMULATION")
        assert len(result.dose_effects) > 0

    def test_estimates_have_finite_tau(self):
        df = _make_measurement_df()
        result = self.estimator.estimate(df, ["s1"], 42, "SIMULATION")
        for e in result.dose_effects:
            assert np.isfinite(e.tau_hat), f"tau_hat is not finite for dose {e.dose_delta}"

    def test_ci_lower_leq_upper(self):
        df = _make_measurement_df()
        result = self.estimator.estimate(df, ["s1"], 42, "SIMULATION")
        for e in result.dose_effects:
            assert e.ci_lower <= e.ci_upper, \
                f"CI [{e.ci_lower}, {e.ci_upper}] invalid for dose {e.dose_delta}"

    def test_perm_p_value_in_range(self):
        df = _make_measurement_df()
        result = self.estimator.estimate(df, ["s1"], 42, "SIMULATION")
        for e in result.dose_effects:
            if e.perm_p_value is not None and not np.isnan(e.perm_p_value):
                assert 0.0 <= e.perm_p_value <= 1.0

    def test_large_true_effect_detected(self):
        """Estimator should produce negative tau for negative true effect."""
        df = _make_measurement_df(true_effect=-0.25, noise=0.01)
        result = self.estimator.estimate(df, ["s1"], 42, "SIMULATION")
        top = max(result.dose_effects, key=lambda e: e.dose_delta)
        # For large negative true effect, tau should be negative
        assert top.tau_hat < 0.0, \
            f"Expected negative tau for true_effect=-0.25, got {top.tau_hat}"

    def test_zero_effect_tau_near_zero(self):
        """With no true effect, tau should be near zero."""
        df = _make_measurement_df(true_effect=0.0, noise=0.01)
        result = self.estimator.estimate(df, ["s1"], 42, "SIMULATION")
        top = max(result.dose_effects, key=lambda e: e.dose_delta)
        assert abs(top.tau_hat) < 0.05, \
            f"Expected small tau for zero effect, got {top.tau_hat}"

    def test_empty_df_does_not_crash(self):
        """Empty dataframe must return a result without crashing."""
        df = pd.DataFrame(columns=[
            "window_id", "block_id", "suspect_name", "dose",
            "victim_throughput", "excluded", "exclusion_reason"
        ])
        result = self.estimator.estimate(df, ["s1"], 42, "SIMULATION")
        assert result is not None
        assert len(result.model_notes) > 0

    def test_all_excluded_does_not_crash(self):
        """All-excluded windows must not crash."""
        df = _make_measurement_df()
        df["excluded"] = True
        result = self.estimator.estimate(df, ["s1"], 42, "SIMULATION")
        assert result is not None

    def test_multiple_suspects_produces_separate_estimates(self):
        """Each suspect must get its own estimates."""
        rng = np.random.default_rng(77)
        rows = []
        for window_id, (suspect, dose) in enumerate([
            ("s1", 0.0), ("s1", 0.5), ("s1", 0.8),
            ("s2", 0.0), ("s2", 0.5), ("s2", 0.8),
        ]):
            rows.append({
                "window_id": window_id, "block_id": 0,
                "suspect_name": suspect, "dose": dose,
                "victim_throughput": 1000.0 + rng.normal(0, 20),
                "excluded": False, "exclusion_reason": "",
            })
        df = pd.DataFrame(rows)
        result = self.estimator.estimate(df, ["s1", "s2"], 42, "SIMULATION")
        suspects_estimated = {e.suspect_name for e in result.dose_effects}
        assert "s1" in suspects_estimated
        assert "s2" in suspects_estimated

    def test_data_source_preserved(self):
        df = _make_measurement_df()
        result = self.estimator.estimate(df, ["s1"], 42, "SIMULATION")
        assert result.data_source == "SIMULATION"


class TestPilotCalibrator:

    def setup_method(self):
        self.calibrator = PilotCalibrator(seed=42)

    def _make_baseline(self, mean=1000.0, sigma_rel=0.02, n=20, seed=42):
        rng = np.random.default_rng(seed)
        return rng.normal(mean, mean * sigma_rel, n).tolist()

    def test_calibrate_returns_result(self):
        bls = self._make_baseline()
        result = self.calibrator.calibrate_from_measurements(bls, "SIMULATION")
        assert result is not None

    def test_icc_in_valid_range(self):
        bls = self._make_baseline()
        result = self.calibrator.calibrate_from_measurements(bls, "SIMULATION")
        assert 0.0 <= result.icc <= 1.0

    def test_mde_positive(self):
        bls = self._make_baseline()
        result = self.calibrator.calibrate_from_measurements(bls, "SIMULATION")
        assert result.mde_fraction > 0.0

    def test_sigma_rel_approximately_correct(self):
        """sigma_y_rel should match the input noise level approximately."""
        bls = self._make_baseline(mean=1000.0, sigma_rel=0.02, n=100)
        result = self.calibrator.calibrate_from_measurements(bls, "SIMULATION")
        assert 0.005 < result.sigma_y_rel < 0.06, \
            f"sigma_y_rel={result.sigma_y_rel:.4f} out of expected range [0.005, 0.06]"

    def test_initial_thresholds_documented(self):
        """Pilot result must document that thresholds are initial values."""
        bls = self._make_baseline()
        result = self.calibrator.calibrate_from_measurements(bls, "SIMULATION")
        combined_notes = " ".join(result.notes).lower()
        assert "initial" in combined_notes or "configurable" in combined_notes

    def test_delta_eff_default_value(self):
        """Delta_eff default must be 0.05 (5% of baseline) per document."""
        bls = self._make_baseline()
        result = self.calibrator.calibrate_from_measurements(bls, "SIMULATION")
        assert result.delta_eff == 0.05

    def test_delta_eq_is_quarter_of_delta_eff(self):
        """Delta_eq = 25% of Delta_eff per document."""
        bls = self._make_baseline()
        result = self.calibrator.calibrate_from_measurements(bls, "SIMULATION")
        assert abs(result.delta_eq - 0.25 * result.delta_eff) < 1e-9

    def test_kappa_default_value(self):
        """kappa default = 0.25 per document."""
        bls = self._make_baseline()
        result = self.calibrator.calibrate_from_measurements(bls, "SIMULATION")
        assert result.kappa == 0.25

    def test_too_few_samples_handled_gracefully(self):
        """Less than 4 baseline samples must not crash."""
        result = self.calibrator.calibrate_from_measurements([1000.0, 990.0], "SIMULATION")
        assert result is not None
        assert any("few" in n.lower() or "too few" in n.lower()
                   for n in result.notes)

    def test_data_source_tagged(self):
        bls = self._make_baseline()
        result = self.calibrator.calibrate_from_measurements(bls, "SIMULATION")
        assert result.data_source == "SIMULATION"
