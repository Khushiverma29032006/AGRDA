"""
pytest conftest – shared fixtures and configuration.

All tests run in simulation mode only. No real cgroup or resctrl
operations are performed.
"""
import pytest
import tempfile
import os


@pytest.fixture
def tmp_runs_dir(tmp_path):
    """Temporary directory for run storage in tests."""
    return str(tmp_path / "runs")


@pytest.fixture
def sim_scenario_s1():
    from agrda.scenarios import get_scenario
    return get_scenario("S1")


@pytest.fixture
def sim_scenario_s0():
    from agrda.scenarios import get_scenario
    return get_scenario("S0")


@pytest.fixture
def default_battery():
    from agrda.audit import AuditBattery
    return AuditBattery(data_source="SIMULATION")


@pytest.fixture
def default_policy():
    from agrda.decision import DecisionPolicy
    return DecisionPolicy(delta_eff=0.05)
