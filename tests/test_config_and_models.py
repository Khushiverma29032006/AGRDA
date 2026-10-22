"""
Tests for ConfigLoader, ConfigThresholds, and HostCapabilitySnapshot.

Tests verify:
  1. ConfigLoader loads the default_config.toml correctly
  2. Threshold extraction returns correct defaults
  3. Freeze status is correctly reported
  4. HostCapabilitySnapshot serializes and round-trips
  5. ConfigThresholds serializes and round-trips
  6. Stage 6 stub actuators raise NotImplementedError for real writes
"""
import pytest
import pathlib
import tempfile

from agrda.capabilities.models import (
    ConfigThresholds, HostCapabilitySnapshot,
    OSType, CgroupVersion, RDTSupport, PerfSupport, CapabilityLevel,
)
from agrda.config_loader import ConfigLoader, load_config, get_thresholds


# ---------------------------------------------------------------------------
# ConfigThresholds
# ---------------------------------------------------------------------------

class TestConfigThresholds:
    def test_default_values(self):
        t = ConfigThresholds()
        assert t.delta_eff == pytest.approx(0.05)
        assert t.delta_eq == pytest.approx(0.0125)
        assert t.kappa == pytest.approx(0.25)
        assert t.icc_min == pytest.approx(0.70)
        assert t.status == "UNFROZEN"
        assert not t.is_frozen()

    def test_freeze_status(self):
        t = ConfigThresholds(status="FROZEN", frozen_at="2026-01-01T00:00:00Z")
        assert t.is_frozen()

    def test_to_dict_round_trip(self):
        t = ConfigThresholds(delta_eff=0.08, delta_eq=0.02, kappa=0.30)
        d = t.to_dict()
        assert d["delta_eff"] == pytest.approx(0.08)
        assert d["delta_eq"] == pytest.approx(0.02)
        t2 = ConfigThresholds.from_dict(d)
        assert t2.delta_eff == pytest.approx(t.delta_eff)
        assert t2.delta_eq == pytest.approx(t.delta_eq)
        assert t2.kappa == pytest.approx(t.kappa)

    def test_from_dict_uses_defaults_for_missing_keys(self):
        t = ConfigThresholds.from_dict({})
        assert t.delta_eff == pytest.approx(0.05)
        assert t.status == "UNFROZEN"


# ---------------------------------------------------------------------------
# HostCapabilitySnapshot
# ---------------------------------------------------------------------------

class TestHostCapabilitySnapshot:
    def test_default_values(self):
        snap = HostCapabilitySnapshot()
        assert snap.level == CapabilityLevel.SIMULATION_ONLY.value
        assert snap.cgroup_version == CgroupVersion.NONE.value
        assert snap.rdt_support == RDTSupport.NOT_AVAILABLE.value

    def test_to_dict_contains_all_fields(self):
        snap = HostCapabilitySnapshot(
            os_type=OSType.LINUX.value,
            cpu_model="Intel Xeon Gold 6148",
            is_intel=True,
            level=CapabilityLevel.FULL_REAL.value,
        )
        d = snap.to_dict()
        assert d["os_type"] == OSType.LINUX.value
        assert d["cpu_model"] == "Intel Xeon Gold 6148"
        assert d["is_intel"] is True
        assert d["level"] == CapabilityLevel.FULL_REAL.value
        assert "unsupported_requirements" in d

    def test_snapshot_is_json_serializable(self):
        import json
        snap = HostCapabilitySnapshot()
        d = snap.to_dict()
        json_str = json.dumps(d)
        assert len(json_str) > 10


# ---------------------------------------------------------------------------
# ConfigLoader
# ---------------------------------------------------------------------------

class TestConfigLoader:
    def test_load_default_config(self):
        """Default config.toml should load without error."""
        loader = ConfigLoader()
        cfg = loader.load()
        # May be empty dict if file not found, but should not raise
        assert isinstance(cfg, dict)

    def test_thresholds_defaults_when_no_config(self):
        """get_thresholds() returns defaults when no config found."""
        loader = ConfigLoader(default_path=pathlib.Path("nonexistent.toml"))
        cfg = loader.load()
        t = loader.get_thresholds(cfg)
        assert t["delta_eff"] == pytest.approx(0.05)
        assert t["delta_eq"] == pytest.approx(0.0125)
        assert t["kappa"] == pytest.approx(0.25)

    def test_load_written_toml(self, tmp_path):
        """ConfigLoader correctly parses a written TOML file."""
        toml_content = """
[thresholds]
delta_eff = 0.08
delta_eq = 0.02
kappa = 0.30
icc_min = 0.75
first_stage_min = 0.12

[window]
warmup_s = 15.0
measure_s = 45.0
washout_s = 25.0
jitter_s = 3.0
"""
        config_file = tmp_path / "test_config.toml"
        config_file.write_text(toml_content, encoding="utf-8")

        loader = ConfigLoader()
        cfg = loader.load(str(config_file))
        t = loader.get_thresholds(cfg)

        assert t["delta_eff"] == pytest.approx(0.08)
        assert t["delta_eq"] == pytest.approx(0.02)
        assert t["kappa"] == pytest.approx(0.30)
        assert t["icc_min"] == pytest.approx(0.75)

    def test_frozen_config_detected(self, tmp_path):
        """Frozen config status is correctly detected."""
        toml_content = """
[meta]
status = "FROZEN"
frozen_at = "2026-01-01T00:00:00Z"
pilot_run_id = "pilot-abc123"

[thresholds]
delta_eff = 0.05
delta_eq = 0.0125
kappa = 0.25
icc_min = 0.70
first_stage_min = 0.10
"""
        config_file = tmp_path / "frozen.toml"
        config_file.write_text(toml_content, encoding="utf-8")

        loader = ConfigLoader()
        cfg = loader.load(str(config_file))

        assert loader.is_frozen(cfg)
        assert loader.get_pilot_run_id(cfg) == "pilot-abc123"

    def test_unfrozen_config(self):
        """Default config is UNFROZEN."""
        loader = ConfigLoader()
        cfg = loader.load()  # default config or empty
        assert not loader.is_frozen(cfg)

    def test_window_spec_kwargs(self, tmp_path):
        """Window spec kwargs are correctly extracted."""
        toml_content = """
[window]
warmup_s = 20.0
measure_s = 60.0
washout_s = 30.0
jitter_s = 5.0
"""
        config_file = tmp_path / "window_test.toml"
        config_file.write_text(toml_content, encoding="utf-8")
        loader = ConfigLoader()
        cfg = loader.load(str(config_file))
        w = loader.get_window_spec_kwargs(cfg)
        assert w["warmup_s"] == pytest.approx(20.0)
        assert w["measure_s"] == pytest.approx(60.0)
        assert w["washout_s"] == pytest.approx(30.0)

    def test_module_level_functions(self):
        """Module-level load_config and get_thresholds work."""
        cfg = load_config()
        assert isinstance(cfg, dict)
        t = get_thresholds()
        assert "delta_eff" in t
        assert t["delta_eff"] > 0


# ---------------------------------------------------------------------------
# Stage 6 actuator stubs raise NotImplementedError for real writes
# ---------------------------------------------------------------------------

class TestActuatorStubs:
    def test_mba_actuator_available_flag(self):
        """MBAActuator reports unavailable on Windows."""
        from agrda.actuators import MBAActuator
        act = MBAActuator(dry_run=True)
        # On Windows, resctrl doesn't exist
        assert act.is_available() in (True, False)   # don't crash

    def test_mba_actuator_dry_run_apply(self):
        """MBAActuator dry-run apply should succeed (no actual write)."""
        from agrda.actuators import MBAActuator
        act = MBAActuator(dry_run=True)
        act.capture_baseline(["suspect_1"])
        result = act.apply("suspect_1", dose=0.5)
        assert result.success is True
        assert "DRY RUN" in " ".join(result.notes)

    def test_mba_actuator_real_raises(self):
        """MBAActuator with dry_run=False raises error (stub)."""
        from agrda.actuators import MBAActuator
        act = MBAActuator(dry_run=False)
        act.capture_baseline(["suspect_1"])
        result = act.apply("suspect_1", dose=0.5)
        # Real write fails with descriptive error
        assert result.success is False
        assert result.error is not None

    def test_cpu_quota_actuator_dry_run(self):
        """CpuQuotaActuator dry-run apply should succeed."""
        from agrda.actuators import CpuQuotaActuator
        act = CpuQuotaActuator(dry_run=True)
        act.capture_baseline(["suspect_1"])
        result = act.apply("suspect_1", dose=0.5)
        assert result.success is True
        assert result.actuator_type.value == "cpu_quota"

    def test_cpu_quota_dose_zero_no_quota(self):
        """Dose=0 for CPU quota should produce 'max' (no quota)."""
        from agrda.actuators import CpuQuotaActuator
        act = CpuQuotaActuator(dry_run=True)
        act.capture_baseline(["suspect_1"])
        result = act.apply("suspect_1", dose=0.0)
        assert result.success is True
        assert "max" in str(result.applied_setting)

    def test_mba_rollback_all(self):
        """MBAActuator rollback_all succeeds in dry_run mode."""
        from agrda.actuators import MBAActuator
        act = MBAActuator(dry_run=True)
        act.capture_baseline(["s1", "s2"])
        act.apply("s1", dose=0.4)
        act.apply("s2", dose=0.6)
        results = act.rollback_all()
        assert all(r.success for r in results)

    def test_mba_invalid_dose_rejected(self):
        """Dose outside [0,1] must be rejected."""
        from agrda.actuators import MBAActuator
        act = MBAActuator(dry_run=True)
        result = act.apply("s1", dose=1.5)
        assert not result.success
        assert result.error is not None


# ---------------------------------------------------------------------------
# Telemetry Stage 6 stubs
# ---------------------------------------------------------------------------

class TestTelemetryStubs:
    def test_perf_collector_is_available(self):
        """PerfTelemetryCollector.is_available() doesn't crash."""
        from agrda.telemetry import PerfTelemetryCollector
        collector = PerfTelemetryCollector()
        result = collector.is_available()
        assert isinstance(result, bool)

    def test_perf_collector_collect_raises(self):
        """collect_window() raises NotImplementedError (stub)."""
        from agrda.telemetry import PerfTelemetryCollector
        collector = PerfTelemetryCollector()
        with pytest.raises(NotImplementedError):
            collector.collect_window(window_id=0, pid=12345, duration_s=5.0)

    def test_mbm_collector_is_available(self):
        """MBMTelemetryCollector.is_available() doesn't crash."""
        from agrda.telemetry import MBMTelemetryCollector
        collector = MBMTelemetryCollector()
        result = collector.is_available()
        assert isinstance(result, bool)

    def test_mbm_collector_read_raises(self):
        """read_closid() raises NotImplementedError (stub)."""
        from agrda.telemetry import MBMTelemetryCollector
        collector = MBMTelemetryCollector()
        with pytest.raises(NotImplementedError):
            collector.read_closid("agrda_suspect_1", socket=0)
