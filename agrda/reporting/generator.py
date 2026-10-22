"""
Report generator for AGRDA experiment runs.

Generates:
  - Text summary report (always watermarked SIMULATION if simulated)
  - Tabular audit battery output
  - Dose-effect table
  - Decision rationale

Rules:
  - Never fabricate or display empty templates as findings.
  - Always include the watermark on simulated results.
  - Clearly separate observed results from model-based estimates.
  - State what the outcome claims AND does not claim.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from ..storage.manager import RunRecord

WATERMARK = "⚠️  SIMULATION – NOT A MEASURED RESULT"
LINE = "=" * 70
THIN = "-" * 70


class ReportGenerator:
    """
    Generates a structured text report for a completed AGRDA run.
    """

    def generate(self, record: RunRecord) -> str:
        """Return a full text report for the given RunRecord."""
        sections = [
            self._header(record),
            self._scenario_section(record),
            self._design_section(record),
            self._pilot_section(record),
            self._estimation_section(record),
            self._audit_section(record),
            self._decision_section(record),
            self._footer(record),
        ]
        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _header(self, r: RunRecord) -> str:
        return "\n".join([
            LINE,
            "  AGRDA EXPERIMENT REPORT",
            f"  {WATERMARK}" if r.data_source == "SIMULATION" else "",
            LINE,
            f"  Run ID    : {r.run_id}",
            f"  Timestamp : {r.timestamp}",
            f"  Schema    : {r.schema_version}",
            f"  Data Src  : {r.data_source}",
            LINE,
        ])

    def _scenario_section(self, r: RunRecord) -> str:
        lines = [
            "SCENARIO",
            THIN,
            f"  ID   : {r.scenario_id}",
            f"  Name : {r.scenario_name}",
            f"  Seed : {r.seed}",
        ]
        for note in r.notes:
            lines.append(f"  Note : {note}")
        return "\n".join(lines)

    def _design_section(self, r: RunRecord) -> str:
        lines = [
            "EXPERIMENT DESIGN",
            THIN,
            f"  Design ID : {r.design_id}",
            f"  Windows   : {r.n_windows_total} total, "
            f"{r.n_windows_excluded} excluded",
        ]
        if r.design_summary:
            for line in r.design_summary.splitlines():
                lines.append(f"  {line}")
        return "\n".join(lines)

    def _pilot_section(self, r: RunRecord) -> str:
        p = r.pilot_result
        if not p:
            return "PILOT CALIBRATION\n" + THIN + "\n  Not available."
        lines = [
            "PILOT CALIBRATION",
            THIN,
            f"  ICC          : {p.get('icc', 'N/A'):.4f}" if isinstance(p.get('icc'), float) else f"  ICC : {p.get('icc')}",
            f"  σ_Y (rel)    : {p.get('sigma_y_rel', 'N/A'):.4f}" if isinstance(p.get('sigma_y_rel'), float) else "",
            f"  MDE (frac)   : {p.get('mde_fraction', 'N/A'):.4f}" if isinstance(p.get('mde_fraction'), float) else "",
            f"  Δ_eff thresh : {p.get('delta_eff', 'N/A')} (initial value from document)",
            "  Notes:",
        ]
        for note in p.get("notes", []):
            lines.append(f"    • {note}")
        return "\n".join(l for l in lines if l)

    def _estimation_section(self, r: RunRecord) -> str:
        est = r.estimation
        effects = est.get("effects", [])
        if not effects:
            return "EFFECT ESTIMATES\n" + THIN + "\n  No estimates available."

        lines = [
            "EFFECT ESTIMATES",
            THIN,
            "  (Negative tau = victim throughput improved when suspect throttled)",
            "",
            f"  {'Suspect':<14} {'Dose':>6} {'τ̂':>8} {'CI Lower':>10} {'CI Upper':>10} {'p-perm':>8}",
            f"  {'-'*14} {'-'*6} {'-'*8} {'-'*10} {'-'*10} {'-'*8}",
        ]
        for e in sorted(effects, key=lambda x: (x['suspect'], x['dose_delta'])):
            p_str = f"{e.get('perm_p', 'N/A'):.3f}" if isinstance(e.get('perm_p'), float) else "N/A"
            lines.append(
                f"  {e['suspect']:<14} {e['dose_delta']:>6.2f} "
                f"{e['tau_hat']:>8.4f} {e['ci_lower']:>10.4f} "
                f"{e['ci_upper']:>10.4f} {p_str:>8}"
            )
        lines.append("")
        lines.append(
            "  IMPORTANT: These are model-based estimates from simulation data."
        )
        lines.append(
            "  Do not extrapolate dose effects to full removal without a "
            "justified model and uncertainty interval."
        )
        return "\n".join(lines)

    def _audit_section(self, r: RunRecord) -> str:
        audit = r.audit
        checks = audit.get("checks", [])
        lines = [
            "AUDIT BATTERY (C1–C9)",
            THIN,
            f"  PASS={audit.get('n_pass', '?')}  "
            f"FAIL={audit.get('n_fail', '?')}  "
            f"UNRESOLVED={audit.get('n_unresolved', '?')}",
            "",
        ]
        icons = {"PASS": "✓", "FAIL": "✗", "UNRESOLVED": "?"}
        for chk in checks:
            icon = icons.get(chk["status"], "?")
            lines.append(
                f"  {icon} [{chk['status']:10s}] {chk['id']}: {chk['name']}"
            )
            for reason in chk.get("reasons", []):
                lines.append(f"      ↳ REASON: {reason}")
            for ev in chk.get("evidence", [])[:2]:  # limit to 2 evidence lines
                lines.append(f"      ↳ Evidence: {ev}")

        lines.append("")
        lines.append(
            "  NOTE: UNRESOLVED checks are NOT treated as PASS."
        )
        lines.append(
            "  In simulation mode, many checks are UNRESOLVED because "
            "real hardware counters are unavailable."
        )
        return "\n".join(lines)

    def _decision_section(self, r: RunRecord) -> str:
        dec = r.decision
        tier_str = f" ({dec.get('tier')})" if dec.get('tier') else ""
        lines = [
            "DECISION",
            THIN,
            f"  OUTCOME: {dec.get('outcome', 'UNKNOWN')}{tier_str}",
            f"  REASON : {dec.get('reason', '')}",
            "",
            "  WHAT THIS OUTCOME SUPPORTS:",
        ]
        for c in dec.get("supported_claims", []):
            lines.append(f"    ✓ {c}")

        lines.append("")
        lines.append("  WHAT THIS OUTCOME DOES NOT SUPPORT:")
        for c in dec.get("unsupported_claims", []):
            lines.append(f"    ✗ {c}")

        lines.append("")
        lines.append("  DECISION TRACE:")
        for t in dec.get("trace", []):
            lines.append(f"    → {t}")

        return "\n".join(lines)

    def _footer(self, r: RunRecord) -> str:
        lines = [
            LINE,
            "  REPRODUCIBILITY",
            THIN,
            f"  To reproduce this run:",
            f"    python -m agrda simulate --scenario {r.scenario_id} --seed {r.seed}",
            "",
        ]
        if r.data_source == "SIMULATION":
            lines.extend([
                f"  {WATERMARK}",
                "  All numbers in this report come from a synthetic simulation.",
                "  They do not represent real hardware measurements.",
                "  Real experiments require a dedicated bare-metal Linux host",
                "  with cgroup v2 and Intel RDT/resctrl support.",
            ])
        lines.append(LINE)
        return "\n".join(lines)
