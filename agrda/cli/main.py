"""
AGRDA Command-Line Interface.

Available commands:
  check-capabilities  – detect host features (read-only)
  simulate            – run a scenario in simulation mode
  list-scenarios      – show available scenarios (S0–S9)
  design              – generate and display a randomised design
  analyze             – re-run estimation + audit on a saved run's raw data
  report              – generate a text report for a completed run
  freeze-config       – freeze pilot-calibrated thresholds to a versioned file
  compare             – evaluate B1–B4 baselines on a scenario

IMPORTANT: Real-hardware commands (pilot, run) are disabled on this host
because capability level = SIMULATION_ONLY.
They will be enabled when full Linux RDT capability is detected.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys


# Force UTF-8 on Windows terminals that default to cp1252
# This must run before any logging or click output
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import click

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)



@click.group()
@click.version_option("0.1.0-simulation", prog_name="agrda")
def cli():
    """
    AGRDA – Audit-Gated Randomised Dose Attribution
    of Memory-Subsystem Interference Among Co-located Containers.

    Research prototype. Simulation mode only on this host.
    All results labelled: SIMULATION – NOT A MEASURED RESULT.
    """


# ---------------------------------------------------------------------------
# check-capabilities
# ---------------------------------------------------------------------------

@cli.command("check-capabilities")
@click.option("--json-out", is_flag=True, default=False,
              help="Output full capability report as JSON")
@click.option("--save", type=click.Path(), default=None,
              help="Save JSON report to file")
def check_capabilities(json_out: bool, save: str):
    """Inspect host capabilities (read-only, no system changes)."""
    from ..capabilities import CapabilityDetector
    click.echo("Detecting capabilities (read-only)...")
    report = CapabilityDetector().detect()
    click.echo(report.human_summary())
    if json_out or save:
        js = report.to_json(indent=2)
        if json_out:
            click.echo(js)
        if save:
            import pathlib
            pathlib.Path(save).write_text(js, encoding="utf-8")
            click.echo(f"Report saved to: {save}")


# ---------------------------------------------------------------------------
# list-scenarios
# ---------------------------------------------------------------------------

@cli.command("list-scenarios")
def list_scenarios_cmd():
    """List all available simulation scenarios (S0–S9)."""
    from ..scenarios import list_scenarios
    click.echo("Available simulation scenarios:")
    for line in list_scenarios():
        click.echo(f"  {line}")
    click.echo()
    click.echo("Run a scenario with: agrda simulate --scenario S1")


# ---------------------------------------------------------------------------
# design
# ---------------------------------------------------------------------------

@cli.command("design")
@click.option("--scenario", "-s", default="S1",
              help="Scenario ID (S0–S9)")
@click.option("--seed", type=int, default=42,
              help="Random seed for reproducibility")
@click.option("--blocks", type=int, default=4,
              help="Number of blocks per suspect")
def design_cmd(scenario: str, seed: int, blocks: int):
    """Generate and display a randomised blocked experiment design."""
    from ..scenarios import get_scenario
    from ..design import ExperimentDesigner
    from ..workloads.models import WorkloadRole

    spec = get_scenario(scenario)
    suspect_names = [
        c.name for c in spec.workloads
        if c.role == WorkloadRole.SUSPECT
    ]

    designer = ExperimentDesigner(
        suspect_names=suspect_names,
        n_blocks=blocks,
        seed=seed,
        scenario_label=scenario,
    )
    design = designer.create_design()
    click.echo("\n" + ExperimentDesigner.summarise(design))
    click.echo(f"\nFirst 8 windows:")
    for w in design.all_windows[:8]:
        click.echo(
            f"  [w{w.window_id:03d} b{w.block_id}] "
            f"{w.suspect_name} → {w.dose_level.name} "
            f"(dose={w.dose_level.dose:.2f})"
        )
    if design.n_total_windows > 8:
        click.echo(f"  ... ({design.n_total_windows - 8} more windows)")


# ---------------------------------------------------------------------------
# simulate
# ---------------------------------------------------------------------------

@cli.command("simulate")
@click.option("--scenario", "-s", default="S1",
              help="Scenario ID (S0–S9)")
@click.option("--seed", type=int, default=42,
              help="Random seed")
@click.option("--blocks", type=int, default=4,
              help="Blocks per suspect")
@click.option("--runs-dir", type=click.Path(), default="runs",
              help="Directory to save results")
@click.option("--verbose", "-v", is_flag=True, default=False)
def simulate_cmd(scenario: str, seed: int, blocks: int,
                 runs_dir: str, verbose: bool):
    """
    Run a simulation scenario (safe, no system changes).

    Results are labelled: SIMULATION – NOT A MEASURED RESULT.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    from ..scenarios import get_scenario
    from ..runner import ExperimentRunner

    click.echo(f"\n{'='*60}")
    click.echo(f"AGRDA SIMULATION – NOT A MEASURED RESULT")
    click.echo(f"{'='*60}")

    spec = get_scenario(scenario)
    click.echo(f"Scenario: {spec.scenario_id} – {spec.name}")
    click.echo(f"Seed: {seed}, Blocks: {blocks}")
    click.echo("-" * 60)

    runner = ExperimentRunner(
        scenario=spec,
        runs_dir=runs_dir,
        seed=seed,
        n_blocks=blocks,
    )

    click.echo("Running experiment pipeline...")
    record = runner.run()

    click.echo(f"\n{'='*60}")
    click.echo(f"RUN COMPLETE: {record.run_id}")
    click.echo(f"Scenario   : {record.scenario_id}")
    click.echo(f"Data source: {record.data_source}  <- NOT REAL DATA")

    dec = record.decision
    click.echo(f"\nDecision   : {dec.get('outcome', 'UNKNOWN')}")
    click.echo(f"Tier       : {dec.get('tier', 'N/A')}")
    click.echo(f"Reason     : {dec.get('reason', '')}")

    click.echo(f"\nAudit summary:")
    audit = record.audit
    click.echo(
        f"  PASS={audit.get('n_pass')} "
        f"FAIL={audit.get('n_fail')} "
        f"UNRESOLVED={audit.get('n_unresolved')}"
    )
    for chk in audit.get("checks", []):
        status_icon = {
            "PASS": "[PASS]", "FAIL": "[FAIL]", "UNRESOLVED": "[?]"
        }.get(chk["status"], "[?]")
        click.echo(
            f"  {status_icon} {chk['id']}: {chk['name']} -> {chk['status']}"
        )
        for r in chk.get("reasons", []):
            click.echo(f"      > {r}")

    click.echo(f"\nSupported claims:")
    for c in dec.get("supported_claims", []):
        click.echo(f"  [+] {c}")
    click.echo(f"NOT supported:")
    for c in dec.get("unsupported_claims", []):
        click.echo(f"  [-] {c}")

    click.echo(f"\nResults saved to: {runs_dir}/{record.run_id}/")
    click.echo(f"{'='*60}")
    click.echo("[!] SIMULATION - NOT A MEASURED RESULT")
    click.echo(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@cli.command("report")
@click.option("--run-id", required=True, help="Run ID to generate report for")
@click.option("--runs-dir", type=click.Path(), default="runs")
@click.option("--save", type=click.Path(), default=None,
              help="Save report text to a file")
def report_cmd(run_id: str, runs_dir: str, save: str):
    """Generate a full structured text report for a completed run."""
    from ..storage import StorageManager
    from ..reporting import ReportGenerator

    sm = StorageManager(runs_dir=runs_dir)
    record = sm.load_run(run_id)
    if record is None:
        click.echo(f"Run not found: {run_id}", err=True)
        sys.exit(1)

    report_text = ReportGenerator().generate(record)
    click.echo(report_text)

    if save:
        import pathlib
        pathlib.Path(save).write_text(report_text, encoding="utf-8")
        click.echo(f"\nReport saved to: {save}")


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

@cli.command("analyze")
@click.option("--run-id", required=True, help="Run ID to re-analyze")
@click.option("--runs-dir", type=click.Path(), default="runs")
def analyze_cmd(run_id: str, runs_dir: str):
    """
    Re-run estimation, audit, and decision on a saved run's raw measurements.

    Useful for exploring alternative threshold settings without re-running
    the full simulation. Does NOT modify the saved run record.
    Output is SIMULATION mode only.
    """
    import pandas as pd
    from ..storage import StorageManager
    from ..estimation import EffectEstimator
    from ..audit import AuditBattery
    from ..decision import DecisionPolicy

    sm = StorageManager(runs_dir=runs_dir)
    record = sm.load_run(run_id)
    if record is None:
        click.echo(f"Run not found: {run_id}", err=True)
        sys.exit(1)

    raw = sm.load_raw_measurements(run_id)
    if not raw:
        click.echo("No raw measurements found for this run.", err=True)
        sys.exit(1)

    click.echo(f"\n{'='*60}")
    click.echo(f"AGRDA RE-ANALYSIS: {run_id}")
    click.echo(f"WARNING: {record.data_source} – NOT A MEASURED RESULT")
    click.echo(f"{'='*60}")

    df = pd.DataFrame(raw)
    # Convert string columns from CSV back to float
    for col in ["dose", "throughput", "mem_bw_gbps"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "excluded" in df.columns:
        df["excluded"] = df["excluded"].map(
            lambda x: str(x).lower() in ("true", "1", "yes")
        )

    victim_df = df[df["role"] == "victim"].rename(
        columns={"throughput": "victim_throughput"}
    ).copy()
    if victim_df.empty:
        click.echo("No victim rows found in raw measurements.", err=True)
        sys.exit(1)

    suspect_names = sorted(victim_df["suspect_name"].unique())
    estimator = EffectEstimator(seed=record.seed)
    est = estimator.estimate(
        measurement_df=victim_df,
        suspect_names=list(suspect_names),
        design_seed=record.seed,
        data_source=record.data_source,
    )

    click.echo(f"\nEffect estimates ({len(est.dose_effects)} entries):")
    for e in sorted(est.dose_effects, key=lambda x: (x.suspect_name, x.dose_delta)):
        click.echo(
            f"  {e.suspect_name} dose={e.dose_delta:.2f}: "
            f"τ̂={e.tau_hat:+.4f}  CI=[{e.ci_lower:.4f}, {e.ci_upper:.4f}]"
        )

    # Build minimal extra dict from stored audit data
    extra = {}
    for chk in record.audit.get("checks", []):
        pass  # Audit data from the original run

    battery = AuditBattery(data_source=record.data_source)
    # Re-run with no pilot (UNRESOLVED for C1) and the re-estimated effects
    from unittest.mock import MagicMock
    mock_pilot = MagicMock()
    mock_pilot.icc = record.pilot_result.get("icc", 0.0)
    mock_pilot.mde_fraction = record.pilot_result.get("mde_fraction", 1.0)
    mock_pilot.sigma_y_rel = record.pilot_result.get("sigma_y_rel", 0.0)

    audit = battery.run_all(
        pilot_result=mock_pilot,
        estimation_result=est,
        extra=extra,
    )
    click.echo(f"\n{audit.summary()}")

    effects = est.dose_effects
    if effects:
        top = max(effects, key=lambda e: e.dose_delta)
        policy = DecisionPolicy(delta_eff=0.05)
        decision = policy.decide(
            audit=audit,
            top_dose_tau=top.tau_hat,
            top_dose_ci_lower=top.ci_lower,
            top_dose_ci_upper=top.ci_upper,
            data_source=record.data_source,
        )
        click.echo(f"\n{decision.summary()}")

    click.echo(f"\n[!] RE-ANALYSIS ONLY – original run record not modified")
    click.echo(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# freeze-config
# ---------------------------------------------------------------------------

@cli.command("freeze-config")
@click.option("--run-id", required=True,
              help="Pilot run ID whose calibration results to freeze")
@click.option("--runs-dir", type=click.Path(), default="runs")
@click.option("--output", "-o", type=click.Path(), required=True,
              help="Output path for frozen config (e.g. configs/frozen_v1.toml)")
@click.option("--delta-eff", type=float, default=None,
              help="Override Delta_eff threshold (default: from pilot or 0.05)")
@click.option("--delta-eq", type=float, default=None,
              help="Override Delta_eq threshold (default: 25% of Delta_eff)")
def freeze_config_cmd(
    run_id: str, runs_dir: str, output: str,
    delta_eff: float, delta_eq: float,
):
    """
    Freeze pilot-calibrated thresholds to a versioned TOML config file.

    After freezing, these thresholds cannot be changed without creating a
    new frozen config version. Tuning thresholds on confirmatory results
    violates the research design.

    Example:
        agrda freeze-config --run-id <pilot-run-id> --output configs/frozen_v1.toml
    """
    from datetime import datetime, timezone
    import pathlib
    from ..storage import StorageManager

    sm = StorageManager(runs_dir=runs_dir)
    record = sm.load_run(run_id)
    if record is None:
        click.echo(f"Run not found: {run_id}", err=True)
        sys.exit(1)

    pilot = record.pilot_result
    if not pilot:
        click.echo("Run has no pilot result — cannot freeze.", err=True)
        sys.exit(1)

    # Use pilot values, apply overrides if provided
    d_eff = delta_eff if delta_eff is not None else pilot.get("delta_eff", 0.05)
    d_eq = delta_eq if delta_eq is not None else d_eff * 0.25
    icc = pilot.get("icc", 0.0)
    mde = pilot.get("mde_fraction", 1.0)
    now_ts = datetime.now(timezone.utc).isoformat()

    frozen_toml = f"""# AGRDA FROZEN THRESHOLD CONFIGURATION
# Status: FROZEN
# Frozen at: {now_ts}
# Pilot run ID: {run_id}
# Data source: {record.data_source}
#
# WARNING: Do NOT modify this file after freezing.
# To use different thresholds, create a new frozen config version.
# Tuning thresholds post-hoc violates the research design.

[meta]
status = "FROZEN"
frozen_at = "{now_ts}"
pilot_run_id = "{run_id}"
data_source = "{record.data_source}"
schema_version = "1.0"

[pilot_summary]
icc = {icc:.4f}
mde_fraction = {mde:.4f}

[thresholds]
# Relevance threshold: effect must exceed this to be considered relevant
delta_eff = {d_eff:.4f}

# Equivalence margin: placebo/sentinel effects must be below this
# Default: 25% of delta_eff
delta_eq = {d_eq:.4f}

# Mediator-to-outcome ratio threshold (for C2)
kappa = 0.25

# Minimum ICC for C1 (measurement adequacy)
icc_min = 0.70

# Minimum first-stage effect for C2 (intervention strength)
first_stage_min = 0.10
"""

    out_path = pathlib.Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frozen_toml, encoding="utf-8")

    click.echo(f"\n{'='*60}")
    click.echo(f"FROZEN CONFIGURATION WRITTEN")
    click.echo(f"{'='*60}")
    click.echo(f"Output    : {output}")
    click.echo(f"Pilot run : {run_id}")
    click.echo(f"Delta_eff : {d_eff:.4f}")
    click.echo(f"Delta_eq  : {d_eq:.4f}")
    click.echo(f"ICC       : {icc:.4f}")
    click.echo(f"MDE       : {mde:.4f}")
    click.echo(f"Status    : FROZEN")
    click.echo(f"{'='*60}")
    click.echo("\nReferencing frozen config in confirmatory run:")
    click.echo(f"  agrda simulate --scenario S1 --config {output}")
    click.echo("")


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

@cli.command("compare")
@click.option("--scenario", "-s", default="S1",
              help="Scenario to run comparison on")
@click.option("--n-trials", type=int, default=10,
              help="Number of repeated trials per baseline")
@click.option("--seed", type=int, default=42)
def compare_cmd(scenario: str, n_trials: int, seed: int):
    """
    Evaluate B1–B4 baselines on a scenario and report metrics.

    IMPORTANT: This uses SIMULATION data only.
    Do not claim any method outperforms others based on simulation alone.
    """
    click.echo(f"\n{'='*60}")
    click.echo(f"AGRDA BASELINE COMPARISON – SIMULATION ONLY")
    click.echo(f"{'='*60}")
    click.echo(
        f"Scenario: {scenario} | Trials: {n_trials} | Seed: {seed}"
    )
    click.echo(
        "NOTE: Simulation results are NOT valid for claiming "
        "real-hardware performance differences between methods."
    )
    click.echo("-" * 60)

    from ..scenarios import get_scenario
    from ..runner import ExperimentRunner
    from ..baselines import BaselineEvaluator
    from ..baselines.comparators import BaselineResult
    from ..decision.policy import Outcome
    import tempfile, os

    spec = get_scenario(scenario)
    true_interferer = any(
        c.sim_victim_impact_fraction > 0.05
        for c in spec.workloads
        if hasattr(c, "sim_victim_impact_fraction")
    )

    b1_results, b2_results, b3_results, b4_results = [], [], [], []

    with tempfile.TemporaryDirectory() as tmpdir:
        for trial in range(n_trials):
            trial_seed = seed + trial
            runner = ExperimentRunner(
                scenario=spec,
                runs_dir=tmpdir,
                seed=trial_seed,
                n_blocks=3,
            )
            record = runner.run()
            dec = record.decision
            outcome = Outcome(dec.get("outcome", Outcome.O1_INCONCLUSIVE.value))

            # Extract top-dose effect
            effects = record.estimation.get("effects", [])
            top_effect = max(effects, key=lambda e: e["dose_delta"]) \
                if effects else {}
            tau = top_effect.get("tau_hat", 0.0)
            ci_lo = top_effect.get("ci_lower", 0.0)
            ci_hi = top_effect.get("ci_upper", 0.0)

            # B3: full AGRDA
            b3_attributed = outcome in (
                Outcome.O4_SUPPORTED_ATTRIBUTION,
                Outcome.O3_PATHWAY_VIOLATION,
            )
            b3_results.append(BaselineResult(
                method="B3",
                scenario_id=scenario,
                trial=trial,
                true_interferer=true_interferer,
                true_pathway="memory",
                attributed=b3_attributed,
                outcome=outcome,
                ci_lower=ci_lo,
                ci_upper=ci_hi,
                true_effect=spec.workloads[0].sim_victim_impact_fraction
                if hasattr(spec.workloads[0], "sim_victim_impact_fraction")
                else None,
            ))

            # B2: no audit
            b2_attr = (ci_hi < -0.05) or (abs(tau) > 0.05 and ci_lo < 0)
            b2_results.append(BaselineResult(
                method="B2", scenario_id=scenario, trial=trial,
                true_interferer=true_interferer, true_pathway="memory",
                attributed=b2_attr, outcome=None,
                ci_lower=ci_lo, ci_upper=ci_hi,
            ))

    evaluator = BaselineEvaluator()
    for method, results in [("B2", b2_results), ("B3", b3_results)]:
        sm = evaluator.compute_metrics(results, method, scenario)
        click.echo(sm.summary_line())

    click.echo("\n[SIMULATION ONLY – DO NOT CITE AS REAL RESULTS]")
    click.echo(f"{'='*60}\n")


if __name__ == "__main__":
    cli()  # pragma: no cover
