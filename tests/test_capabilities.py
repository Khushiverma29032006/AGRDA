"""
Tests for capabilities module.

All tests are read-only and never change system settings.
"""
import pytest
from agrda.capabilities import CapabilityDetector, CapabilityLevel


def test_detect_returns_report():
    """CapabilityDetector.detect() must return a CapabilityReport."""
    detector = CapabilityDetector()
    report = detector.detect()
    assert report is not None
    assert report.report_timestamp != ""
    assert report.hostname != ""


def test_windows_is_simulation_only():
    """On Windows, level must be SIMULATION_ONLY."""
    import platform
    if platform.system().lower() == "windows":
        detector = CapabilityDetector()
        report = detector.detect()
        assert report.level == CapabilityLevel.SIMULATION_ONLY.value


def test_unsupported_requirements_listed_on_windows():
    """Windows must list missing Linux features as unsupported."""
    import platform
    if platform.system().lower() == "windows":
        detector = CapabilityDetector()
        report = detector.detect()
        # At least cgroup, RDT, perf must be in unsupported
        combined = " ".join(report.unsupported_requirements).lower()
        assert "cgroup" in combined or "rdt" in combined or "linux" in combined


def test_capability_report_serialises_to_json():
    """Report must be JSON-serialisable."""
    import json
    detector = CapabilityDetector()
    report = detector.detect()
    js = report.to_json()
    parsed = json.loads(js)
    assert "level" in parsed
    assert "os_type" in parsed


def test_human_summary_contains_level():
    """Human summary must contain the capability level string."""
    detector = CapabilityDetector()
    report = detector.detect()
    summary = report.human_summary()
    assert report.level in summary
    assert "LEVEL" in summary
