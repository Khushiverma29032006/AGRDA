"""
Tests for the experiment design module.

Covers:
  - Dose randomisation and seed replay
  - Block balance
  - Design-matrix rank validation
  - Window count correctness
  - Jitter bounds
"""
import pytest
from agrda.design import ExperimentDesigner, WindowSpec
from agrda.design.models import DEFAULT_DOSE_LEVELS, DoseLevel


class TestExperimentDesigner:

    def setup_method(self):
        self.suspects = ["suspect_1", "suspect_2"]
        self.seed = 42
        self.n_blocks = 4

    def _make_designer(self, **kwargs):
        return ExperimentDesigner(
            suspect_names=kwargs.get("suspect_names", self.suspects),
            n_blocks=kwargs.get("n_blocks", self.n_blocks),
            seed=kwargs.get("seed", self.seed),
            scenario_label="TEST",
        )

    # --- basic structure ---

    def test_creates_design(self):
        design = self._make_designer().create_design()
        assert design is not None
        assert design.design_id != ""

    def test_window_count_correct(self):
        """n_blocks × n_doses × n_suspects windows expected."""
        n_doses = len(DEFAULT_DOSE_LEVELS)
        design = self._make_designer().create_design()
        expected = self.n_blocks * n_doses * len(self.suspects)
        assert design.n_total_windows == expected

    def test_single_suspect_works(self):
        d = ExperimentDesigner(
            suspect_names=["s1"], n_blocks=3, seed=10, scenario_label="T"
        ).create_design()
        assert d.n_total_windows == 3 * len(DEFAULT_DOSE_LEVELS)

    def test_block_contains_all_doses(self):
        """Every block must contain every dose level exactly once."""
        design = ExperimentDesigner(
            suspect_names=["s1"], n_blocks=4, seed=7, scenario_label="T"
        ).create_design()
        for block in design.blocks:
            doses_in_block = sorted([w.dose_level.dose for w in block.windows])
            expected = sorted(d.dose for d in DEFAULT_DOSE_LEVELS)
            assert doses_in_block == expected, (
                f"Block {block.block_id} missing doses: {doses_in_block}"
            )

    # --- seed replay ---

    def test_seed_replay_produces_identical_design(self):
        """Same seed must produce identical window order."""
        d1 = self._make_designer().create_design()
        d2 = self._make_designer().create_design()
        order1 = [(w.suspect_name, w.dose_level.dose)
                  for w in d1.all_windows]
        order2 = [(w.suspect_name, w.dose_level.dose)
                  for w in d2.all_windows]
        assert order1 == order2

    def test_different_seed_produces_different_order(self):
        d1 = ExperimentDesigner(
            suspect_names=["s1"], n_blocks=4, seed=1, scenario_label="T"
        ).create_design()
        d2 = ExperimentDesigner(
            suspect_names=["s1"], n_blocks=4, seed=99, scenario_label="T"
        ).create_design()
        order1 = [w.dose_level.name for w in d1.all_windows]
        order2 = [w.dose_level.name for w in d2.all_windows]
        # Very unlikely to be the same with different seeds
        assert order1 != order2

    # --- design ID determinism ---

    def test_design_id_deterministic(self):
        d1 = self._make_designer().create_design()
        d2 = self._make_designer().create_design()
        assert d1.design_id == d2.design_id

    def test_different_seed_different_id(self):
        d1 = ExperimentDesigner(
            suspect_names=["s1"], n_blocks=4, seed=1, scenario_label="T"
        ).create_design()
        d2 = ExperimentDesigner(
            suspect_names=["s1"], n_blocks=4, seed=2, scenario_label="T"
        ).create_design()
        assert d1.design_id != d2.design_id

    # --- rank check ---

    def test_rank_ok_for_valid_design(self):
        design = self._make_designer().create_design()
        # Use == not `is` — NumPy booleans (np.True_) are not `is True`
        assert design.rank_ok == True

    def test_rank_notes_populated(self):
        design = self._make_designer().create_design()
        assert len(design.rank_notes) > 0

    # --- OAT interaction note ---

    def test_multi_suspect_oat_note(self):
        """OAT with multiple suspects must note interaction non-identifiability."""
        design = ExperimentDesigner(
            suspect_names=["s1", "s2", "s3"], n_blocks=4,
            seed=42, scenario_label="T"
        ).create_design()
        combined = " ".join(design.rank_notes)
        assert "interaction" in combined.lower() or "OAT" in combined

    # --- jitter bounds ---

    def test_jitter_within_bounds(self):
        ws = WindowSpec(jitter_s=2.0)
        design = ExperimentDesigner(
            suspect_names=["s1"], n_blocks=3, seed=5,
            window_spec=ws, scenario_label="T"
        ).create_design()
        for w in design.all_windows:
            assert -2.0 <= w.jitter_s <= 2.0

    # --- validation ---

    def test_zero_suspects_raises(self):
        with pytest.raises(ValueError, match="suspect"):
            ExperimentDesigner(suspect_names=[], n_blocks=4, seed=1, scenario_label="T")

    def test_one_block_raises(self):
        with pytest.raises(ValueError, match="n_blocks"):
            ExperimentDesigner(suspect_names=["s1"], n_blocks=1, seed=1, scenario_label="T")

    # --- all_windows property ---

    def test_all_windows_flat_matches_blocks(self):
        design = self._make_designer().create_design()
        total_from_blocks = sum(len(b.windows) for b in design.blocks)
        assert len(design.all_windows) == total_from_blocks
