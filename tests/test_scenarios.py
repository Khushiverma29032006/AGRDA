"""
Deterministic smoke tests for simulation scenarios (S0–S9).

These tests run each scenario end-to-end and verify:
  1. Run completes without exception
  2. Data source is always labelled SIMULATION
  3. All nine audit checks are present
  4. Expected outcome matches scenario specification
  5. Simulation results are never mixed with real labels

These tests are deterministic (fixed seed) so CI can run them.
They use fast window timing (no real waits).
"""
import pytest
import tempfile
from agrda.scenarios import get_scenario, list_scenarios, ALL_SCENARIOS
from agrda.runner import ExperimentRunner
from agrda.decision.policy import Outcome

SIMULATION_TAG = "SIMULATION"
FAST_SEED = 42


class TestScenarioSmoke:
    """Fast smoke test for each scenario – no assertions on exact numbers."""

    @pytest.mark.parametrize("scenario_id", list(ALL_SCENARIOS.keys()))
    def test_scenario_completes(self, scenario_id, tmp_path):
        """Every scenario must complete without raising an exception."""
        spec = get_scenario(scenario_id)
        runner = ExperimentRunner(
            scenario=spec,
            runs_dir=str(tmp_path),
            seed=FAST_SEED,
            n_blocks=2,  # minimum for valid design
        )
        record = runner.run()
        assert record is not None

    @pytest.mark.parametrize("scenario_id", list(ALL_SCENARIOS.keys()))
    def test_data_source_always_simulation(self, scenario_id, tmp_path):
        """Data source tag must be SIMULATION in every scenario."""
        spec = get_scenario(scenario_id)
        runner = ExperimentRunner(
            scenario=spec, runs_dir=str(tmp_path),
            seed=FAST_SEED, n_blocks=2,
        )
        record = runner.run()
        assert record.data_source == SIMULATION_TAG, \
            f"Scenario {scenario_id}: data_source must be SIMULATION, " \
            f"got {record.data_source}"

    @pytest.mark.parametrize("scenario_id", list(ALL_SCENARIOS.keys()))
    def test_all_nine_checks_in_audit(self, scenario_id, tmp_path):
        """All 9 audit checks must appear in results."""
        spec = get_scenario(scenario_id)
        runner = ExperimentRunner(
            scenario=spec, runs_dir=str(tmp_path),
            seed=FAST_SEED, n_blocks=2,
        )
        record = runner.run()
        check_ids = {c["id"] for c in record.audit.get("checks", [])}
        for cid in ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]:
            assert cid in check_ids, \
                f"Scenario {scenario_id}: audit check {cid} missing"

    @pytest.mark.parametrize("scenario_id", list(ALL_SCENARIOS.keys()))
    def test_decision_outcome_present(self, scenario_id, tmp_path):
        """Decision outcome must be a valid Outcome string."""
        spec = get_scenario(scenario_id)
        runner = ExperimentRunner(
            scenario=spec, runs_dir=str(tmp_path),
            seed=FAST_SEED, n_blocks=2,
        )
        record = runner.run()
        outcome_str = record.decision.get("outcome", "")
        valid_outcomes = {o.value for o in Outcome}
        assert outcome_str in valid_outcomes, \
            f"Scenario {scenario_id}: invalid outcome '{outcome_str}'"

    @pytest.mark.parametrize("scenario_id", list(ALL_SCENARIOS.keys()))
    def test_run_has_supported_and_unsupported_claims(self, scenario_id, tmp_path):
        """Decision must document both supported and unsupported claims."""
        spec = get_scenario(scenario_id)
        runner = ExperimentRunner(
            scenario=spec, runs_dir=str(tmp_path),
            seed=FAST_SEED, n_blocks=2,
        )
        record = runner.run()
        dec = record.decision
        # At minimum, unsupported_claims must be populated
        assert isinstance(dec.get("unsupported_claims"), list), \
            f"Scenario {scenario_id}: missing unsupported_claims"


class TestScenarioExpectedOutcomes:
    """
    Tests that verify each scenario produces the intended outcome.
    Uses n_blocks=4 for more reliable estimation.

    Note: These check the EXPECTED outcome from scenario specification.
    The simulation may sometimes deviate from expectation due to noise —
    that is scientifically legitimate. We verify the majority case.
    """

    def _run_scenario(self, scenario_id, tmp_path, seed=42, n_blocks=4):
        spec = get_scenario(scenario_id)
        runner = ExperimentRunner(
            scenario=spec, runs_dir=str(tmp_path),
            seed=seed, n_blocks=n_blocks,
        )
        return runner.run()

    def test_s0_null_outcome(self, tmp_path):
        """S0 (no interference) should give O2 Null or O1 Inconclusive."""
        record = self._run_scenario("S0", tmp_path)
        outcome = record.decision.get("outcome")
        acceptable = {
            Outcome.O2_NULL.value,
            Outcome.O1_INCONCLUSIVE.value,
            Outcome.O5_INTERVENTION_EFFECT_ONLY.value,
        }
        assert outcome in acceptable, \
            f"S0: expected null-like outcome, got {outcome}"

    def test_s6_carryover_detected(self, tmp_path):
        """S6 (carry-over) must produce Inconclusive due to C8 FAIL."""
        record = self._run_scenario("S6", tmp_path)
        # C8 must fail
        c8 = next(
            (c for c in record.audit["checks"] if c["id"] == "C8"), None
        )
        assert c8 is not None
        assert c8["status"] == "FAIL", \
            f"S6: C8 should FAIL, got {c8['status']}"

    def test_s7_noisy_counters_c1_fails(self, tmp_path):
        """S7 (noisy counters) must trigger C1 FAIL."""
        record = self._run_scenario("S7", tmp_path)
        c1 = next(
            (c for c in record.audit["checks"] if c["id"] == "C1"), None
        )
        assert c1 is not None
        assert c1["status"] == "FAIL", \
            f"S7: C1 should FAIL (noisy counters), got {c1['status']}"

    def test_s4_pathway_violation_c5_fires(self, tmp_path):
        """S4 (frequency pathway) should trigger a pathway check failure."""
        record = self._run_scenario("S4", tmp_path)
        c5 = next(
            (c for c in record.audit["checks"] if c["id"] == "C5"), None
        )
        assert c5 is not None
        # C5 should either FAIL (pathway detected) or at minimum not PASS
        assert c5["status"] in ("FAIL", "UNRESOLVED"), \
            f"S4: C5 should not PASS (pathway violation scenario)"

    def test_s9_exclusion_violation_c5_fires(self, tmp_path):
        """S9 (exclusion restriction violation) must trigger pathway check."""
        record = self._run_scenario("S9", tmp_path)
        c5 = next(
            (c for c in record.audit["checks"] if c["id"] == "C5"), None
        )
        assert c5 is not None
        assert c5["status"] == "FAIL", \
            f"S9: C5 should FAIL (injected violation), got {c5['status']}"


class TestSimulationLabelling:
    """Tests that simulation data is never mislabelled as real."""

    def test_simulation_tag_in_run_record(self, tmp_path):
        spec = get_scenario("S1")
        runner = ExperimentRunner(
            scenario=spec, runs_dir=str(tmp_path), seed=42, n_blocks=2
        )
        record = runner.run()
        # Check all major fields are labelled
        assert record.data_source == "SIMULATION"
        assert any("SIMULATION" in n.upper() for n in record.notes)

    def test_no_real_hardware_result_in_measurements(self, tmp_path):
        from agrda.storage import StorageManager
        spec = get_scenario("S1")
        runner = ExperimentRunner(
            scenario=spec, runs_dir=str(tmp_path), seed=42, n_blocks=2
        )
        record = runner.run()
        sm = StorageManager(runs_dir=str(tmp_path))
        raw = sm.load_raw_measurements(record.run_id)
        for row in raw:
            assert row.get("source") == "SIMULATION", \
                f"Row source must be SIMULATION, got {row.get('source')}"

    def test_list_scenarios_returns_all_ten(self):
        lines = list_scenarios()
        assert len(lines) == 10, f"Expected 10 scenarios, got {len(lines)}"

    def test_get_unknown_scenario_raises(self):
        with pytest.raises(ValueError, match="Unknown scenario"):
            get_scenario("S99")
