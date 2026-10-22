"""
Experiment runner – orchestrates a complete AGRDA simulation run.

Ties together:
  WorkloadManager → SimWorkloadBackend
  ExperimentDesigner → AssignedWindow schedule
  SimActuator + Watchdog
  EffectEstimator
  AuditBattery
  DecisionPolicy
  StorageManager

Produces a RunRecord saved to disk, clearly labelled SIMULATION.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .capabilities import CapabilityDetector, CapabilityReport
from .workloads import WorkloadManager
from .workloads.models import WorkloadConfig, WorkloadRole
from .actuators import SimActuator, Watchdog
from .design import ExperimentDesign, ExperimentDesigner, WindowSpec
from .design.models import DEFAULT_DOSE_LEVELS
from .estimation import EffectEstimator, PilotCalibrator
from .audit import AuditBattery
from .decision import DecisionPolicy
from .scenarios import ScenarioSpec
from .storage import StorageManager, RunRecord

logger = logging.getLogger(__name__)

SIMULATION_TAG = "SIMULATION"


class ExperimentRunner:
    """
    Runs a complete AGRDA simulation experiment.

    Parameters
    ----------
    scenario      : ScenarioSpec to run
    runs_dir      : where to save results
    seed          : random seed (overrides scenario default)
    n_blocks      : how many blocks per suspect
    window_spec   : timing (uses pilot results when available)
    """

    def __init__(
        self,
        scenario: ScenarioSpec,
        runs_dir: str = "runs",
        seed: Optional[int] = None,
        n_blocks: int = 4,
        window_spec: Optional[WindowSpec] = None,
    ) -> None:
        self.scenario = scenario
        self.seed = seed if seed is not None else 42
        self.n_blocks = n_blocks
        self.window_spec = window_spec or WindowSpec(
            warmup_s=0.1,    # Fast for simulation (document: 10s for real)
            measure_s=0.5,
            washout_s=0.2,
        )
        self.storage = StorageManager(runs_dir=runs_dir)
        self._run_id = f"{scenario.scenario_id}_{uuid.uuid4().hex[:8]}"

    @property
    def run_id(self) -> str:
        return self._run_id

    def run(self) -> RunRecord:
        """
        Execute the full experiment pipeline and return a saved RunRecord.

        Steps:
          1. Detect capabilities (read-only)
          2. Build design
          3. Run pilot calibration
          4. Execute measurement windows
          5. Estimate effects
          6. Run audit battery
          7. Apply decision policy
          8. Save and return record
        """
        logger.info(
            "=== AGRDA Simulation Run: %s (%s) ===",
            self._run_id, self.scenario.scenario_id,
        )
        logger.info("DATA SOURCE: SIMULATION – NOT A MEASURED RESULT")

        # --- 1. Capabilities ---
        cap_report = CapabilityDetector().detect()

        # --- 2. Design ---
        suspect_names = [
            c.name for c in self.scenario.workloads
            if c.role == WorkloadRole.SUSPECT
        ]
        designer = ExperimentDesigner(
            suspect_names=suspect_names,
            dose_levels=DEFAULT_DOSE_LEVELS,
            n_blocks=self.n_blocks,
            window_spec=self.window_spec,
            seed=self.seed,
            scenario_label=self.scenario.scenario_id,
        )
        design = designer.create_design()
        logger.info("Design: %s", ExperimentDesigner.summarise(design))

        # --- 3. Pilot calibration ---
        pilot_result, pilot_bls = self._run_pilot()

        # --- 4. Measurement windows ---
        actuator = SimActuator(dry_run=True)
        actuator.capture_baseline(suspect_names)
        all_measurements: List[Dict] = []
        window_measurements: List[Dict] = []

        with WorkloadManager(
            configs=self.scenario.workloads,
            seed=self.seed,
            scenario_label=self.scenario.scenario_id,
        ) as wm:
            with Watchdog(actuator, heartbeat_interval=1.0, timeout=30.0) as wd:
                for window in design.all_windows:
                    wd.beat()  # keep watchdog alive

                    # Apply dose for this window's suspect; others at 0
                    doses: Dict[str, float] = {}
                    doses[window.suspect_name] = window.dose_level.dose

                    # Apply via actuator (logged)
                    act_result = actuator.apply(
                        window.suspect_name, window.dose_level.dose
                    )
                    all_measurements.append(asdict(act_result.to_log_entry("apply")))

                    # Measure
                    measurements = wm.measure_window(
                        window_id=window.window_id,
                        doses=doses,
                    )

                    # Convert to dict records
                    suspect_dose = window.dose_level.dose
                    for m in measurements:
                        # For victim rows: use the SUSPECT's dose (the treatment).
                        # For suspect rows: use their own dose.
                        # For other roles (placebo, sentinel): their own dose.
                        if m.role == "victim":
                            row_dose = suspect_dose
                        else:
                            row_dose = doses.get(m.workload_name, 0.0)
                        rec = {
                            "source": m.source,
                            "window_id": m.window_id,
                            "block_id": window.block_id,
                            "suspect_name": window.suspect_name,
                            "workload_name": m.workload_name,
                            "role": m.role,
                            "dose": row_dose,
                            "throughput": m.throughput,
                            "mem_bw_gbps": m.mem_bw_gbps,
                            "mem_stall_cpi": m.mem_stall_cpi,
                            "llc_miss_rate": m.llc_miss_rate,
                            "gt_memory_effect": m.gt_memory_effect,
                            "gt_other_effect": m.gt_other_effect,
                            "gt_total_effect": m.gt_total_effect,
                            "quality_ok": m.quality_ok,
                            "excluded": False,
                            "exclusion_reason": "",
                        }
                        window_measurements.append(rec)


                    # Rollback (simulate washout by resetting to baseline)
                    actuator.rollback(window.suspect_name)

        # --- 5. Estimation ---
        df = pd.DataFrame(window_measurements)
        victim_df = self._build_victim_df(df, suspect_names)
        estimator = EffectEstimator(seed=self.seed)
        est_result = estimator.estimate(
            measurement_df=victim_df,
            suspect_names=suspect_names,
            design_seed=self.seed,
            data_source=SIMULATION_TAG,
        )

        # --- 6. Audit battery ---
        extra_audit = self._build_audit_extra(
            df, suspect_names, design, victim_df
        )
        # S7 scenario: inject bad pilot (noisy counters)
        audit_pilot = pilot_result
        if self.scenario.expected_noisy_counters:
            import copy
            audit_pilot = copy.copy(pilot_result)
            audit_pilot.icc = 0.45         # below threshold
            audit_pilot.mde_fraction = 0.12  # above delta_eff
            audit_pilot.notes.append("S7 INJECTED: noisy counter simulation")

        battery = AuditBattery(data_source=SIMULATION_TAG)
        audit_result = battery.run_all(
            pilot_result=audit_pilot,
            estimation_result=est_result,
            extra=extra_audit,
        )
        logger.info("\n%s", audit_result.summary())

        # --- 7. Decision ---
        top_dose_tau, ci_lo, ci_hi = self._get_top_dose_effect(
            est_result, suspect_names
        )
        policy = DecisionPolicy(delta_eff=0.05)
        decision = policy.decide(
            audit=audit_result,
            top_dose_tau=top_dose_tau,
            top_dose_ci_lower=ci_lo,
            top_dose_ci_upper=ci_hi,
            data_source=SIMULATION_TAG,
        )
        logger.info("\n%s", decision.summary())

        # --- 8. Save ---
        record = RunRecord(
            run_id=self._run_id,
            scenario_id=self.scenario.scenario_id,
            scenario_name=self.scenario.name,
            seed=self.seed,
            data_source=SIMULATION_TAG,
            timestamp=datetime.now(timezone.utc).isoformat(),
            capability_report=cap_report.to_dict(),
            design_id=design.design_id,
            design_summary=ExperimentDesigner.summarise(design),
            pilot_result={
                "icc": pilot_result.icc,
                "sigma_y_rel": pilot_result.sigma_y_rel,
                "mde_fraction": pilot_result.mde_fraction,
                "delta_eff": pilot_result.delta_eff,
                "notes": pilot_result.notes,
            },
            actuator_log=all_measurements,
            n_windows_total=len(design.all_windows),
            n_windows_excluded=0,
            estimation={
                "n_effects": len(est_result.dose_effects),
                "effects": [
                    {
                        "suspect": e.suspect_name,
                        "dose_delta": e.dose_delta,
                        "tau_hat": e.tau_hat,
                        "ci_lower": e.ci_lower,
                        "ci_upper": e.ci_upper,
                        "perm_p": e.perm_p_value,
                    }
                    for e in est_result.dose_effects
                ],
            },
            audit={
                "n_pass": audit_result.n_pass,
                "n_fail": audit_result.n_fail,
                "n_unresolved": audit_result.n_unresolved,
                "checks": [
                    {
                        "id": c.check_id,
                        "name": c.name,
                        "status": c.status.value,
                        "reasons": c.reasons,
                        "evidence": c.evidence,
                    }
                    for c in audit_result.checks
                ],
            },
            decision={
                "outcome": decision.outcome.value,
                "tier": decision.tier,
                "reason": decision.reason,
                "supported_claims": decision.supported_claims,
                "unsupported_claims": decision.unsupported_claims,
                "trace": decision.decision_trace,
            },
            notes=[
                "DATA SOURCE: SIMULATION – NOT A MEASURED RESULT",
                f"Scenario: {self.scenario.scenario_id} – {self.scenario.name}",
            ],
        )

        run_dir = self.storage.save_run(record, raw_measurements=window_measurements)
        logger.info("Run saved to: %s", run_dir)
        return record

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_pilot(self):
        """
        Quick pilot calibration from simulation baseline.

        Implementation decision: For simulation, we generate n_groups
        independent baseline sessions (one RNG draw per session) to
        produce realistic between-session and within-session variance for
        the ICC calculation. On real hardware this would be separate
        measurement windows.
        """
        with WorkloadManager(
            configs=self.scenario.workloads,
            seed=self.seed,
            scenario_label=self.scenario.scenario_id,
        ) as wm:
            baselines = wm.capture_baseline(n_windows=20)

        victim_name = next(
            c.name for c in self.scenario.workloads
            if c.role == WorkloadRole.VICTIM
        )
        victim_bls_mean = baselines.get(victim_name, 1000.0)

        # Generate synthetic baseline samples with realistic between-session
        # and within-session structure for ICC estimation.
        rng = np.random.default_rng(self.seed + 999)
        N_SESSIONS = 5   # blocks / sessions
        N_PER_SESSION = 4  # windows per session
        sigma_between = victim_bls_mean * 0.015  # between-session SD
        sigma_within = victim_bls_mean * 0.018   # within-session SD

        bl_samples = []
        for _s in range(N_SESSIONS):
            session_mean = victim_bls_mean + rng.normal(0, sigma_between)
            for _w in range(N_PER_SESSION):
                bl_samples.append(
                    float(session_mean + rng.normal(0, sigma_within))
                )

        calibrator = PilotCalibrator(
            n_baseline_windows=len(bl_samples),
            n_groups=N_SESSIONS,
            seed=self.seed,
        )
        pilot = calibrator.calibrate_from_measurements(
            baseline_throughputs=bl_samples,
            data_source=SIMULATION_TAG,
        )
        pilot.notes.append(
            f"SIMULATION: ICC computed from {N_SESSIONS} synthetic sessions "
            f"x {N_PER_SESSION} windows (sigma_between={sigma_between:.1f}, "
            f"sigma_within={sigma_within:.1f})"
        )
        return pilot, baselines


    def _build_victim_df(
        self,
        df: pd.DataFrame,
        suspect_names: List[str],
    ) -> pd.DataFrame:
        """Build estimation dataframe with victim_throughput and dose."""
        victim_rows = df[df["role"] == "victim"].copy()
        # For each window, the dose is from the suspect being tested
        # victim_df needs: window_id, block_id, suspect_name, dose,
        #                  victim_throughput, excluded
        victim_df = victim_rows.rename(
            columns={"throughput": "victim_throughput"}
        )[["window_id", "block_id", "suspect_name", "dose",
           "victim_throughput", "excluded", "exclusion_reason"]].copy()
        return victim_df

    def _build_audit_extra(
        self,
        df: pd.DataFrame,
        suspect_names: List[str],
        design: ExperimentDesign,
        victim_df: pd.DataFrame,
    ) -> Dict:
        """
        Build the extra dict for AuditBattery from scenario spec and data.
        """
        extra = dict(self.scenario.extra_audit_data)

        # For S6 (carry-over): inject large washout residual
        if self.scenario.expected_carryover:
            extra["washout_residual_fraction"] = 0.04   # above delta_eq

        # Phase balance data from design
        windows = design.all_windows
        if windows:
            extra["window_order"] = [w.window_id for w in windows]
            extra["window_doses"] = [w.dose_level.dose for w in windows]

        return extra

    def _get_top_dose_effect(
        self,
        est_result,
        suspect_names: List[str],
    ):
        """Get the effect estimate at the highest dose for the first suspect."""
        effects = [
            e for e in est_result.dose_effects
            if e.suspect_name == suspect_names[0]
        ]
        if not effects:
            return None, None, None
        top = max(effects, key=lambda e: e.dose_delta)
        return top.tau_hat, top.ci_lower, top.ci_upper
