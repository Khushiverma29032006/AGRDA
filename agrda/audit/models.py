"""
Audit check result models.

CheckStatus values:
  PASS       – check passed with evidence meeting the threshold.
  FAIL       – check failed; evidence conclusively violates threshold.
  UNRESOLVED – check could not be performed (feature unavailable,
               insufficient data, etc.).
               NEVER promoted to PASS. This is a hard rule.

Each CheckResult stores structured evidence so it can be
reproduced, inspected, and logged.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class CheckStatus(enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNRESOLVED = "UNRESOLVED"


@dataclass
class CheckResult:
    """
    Result of one audit check (C1–C9).

    Attributes
    ----------
    check_id      : "C1" … "C9"
    name          : human-readable name
    status        : PASS / FAIL / UNRESOLVED
    metric_values : key→value dict of measured quantities
    thresholds    : key→value dict of thresholds used
    evidence      : textual evidence supporting the status
    reasons       : list of reasons (for FAIL or UNRESOLVED)
    limitations   : known limitations of this check on this platform
    data_source   : "SIMULATION" or "REAL"
    """
    check_id: str
    name: str
    status: CheckStatus = CheckStatus.UNRESOLVED
    metric_values: Dict[str, Any] = field(default_factory=dict)
    thresholds: Dict[str, Any] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    data_source: str = "UNKNOWN"

    def is_pass(self) -> bool:
        return self.status == CheckStatus.PASS

    def is_fail(self) -> bool:
        return self.status == CheckStatus.FAIL

    def is_unresolved(self) -> bool:
        return self.status == CheckStatus.UNRESOLVED

    def summary_line(self) -> str:
        return f"[{self.status.value:10s}] {self.check_id}: {self.name}"


@dataclass
class AuditBatteryResult:
    """
    Aggregated results of all C1–C9 checks.
    """
    checks: List[CheckResult] = field(default_factory=list)
    data_source: str = "UNKNOWN"
    n_pass: int = 0
    n_fail: int = 0
    n_unresolved: int = 0

    def update_counts(self) -> None:
        self.n_pass = sum(1 for c in self.checks if c.is_pass())
        self.n_fail = sum(1 for c in self.checks if c.is_fail())
        self.n_unresolved = sum(1 for c in self.checks if c.is_unresolved())

    def get(self, check_id: str) -> Optional[CheckResult]:
        for c in self.checks:
            if c.check_id == check_id:
                return c
        return None

    def summary(self) -> str:
        self.update_counts()
        lines = [
            f"Audit Battery ({self.data_source}): "
            f"PASS={self.n_pass} FAIL={self.n_fail} "
            f"UNRESOLVED={self.n_unresolved}",
        ]
        for c in self.checks:
            lines.append(f"  {c.summary_line()}")
        return "\n".join(lines)
