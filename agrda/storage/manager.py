"""
Storage manager – writes and reads experiment run records.

Storage format: JSON for metadata/results, CSV for raw measurements.
Each run gets its own directory under the runs/ folder.

Principles:
  - Raw measurements are immutable once written.
  - Processed values go to a separate file.
  - Missing values recorded explicitly (NaN in CSV, null in JSON).
  - Each run has a manifest with schema version and checksums.
  - Simulation runs are clearly labelled DATA_SOURCE=SIMULATION.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0"
SIMULATION_TAG = "SIMULATION"


@dataclass
class RunRecord:
    """
    Complete persistent record for one experiment run.

    All fields that affect reproducibility are included.
    """
    run_id: str
    scenario_id: str
    scenario_name: str
    seed: int
    data_source: str          # "SIMULATION" or "REAL"
    timestamp: str
    schema_version: str = SCHEMA_VERSION

    # Capability snapshot
    capability_report: Dict = field(default_factory=dict)

    # Design
    design_id: str = ""
    design_summary: str = ""

    # Pilot results
    pilot_result: Dict = field(default_factory=dict)

    # Actuator log
    actuator_log: List[Dict] = field(default_factory=list)

    # Raw measurements (stored separately in CSV, but summarised here)
    n_windows_total: int = 0
    n_windows_excluded: int = 0
    exclusion_log: List[str] = field(default_factory=list)

    # Estimation results
    estimation: Dict = field(default_factory=dict)

    # Audit battery
    audit: Dict = field(default_factory=dict)

    # Decision
    decision: Dict = field(default_factory=dict)

    # Baseline comparisons
    baselines: Dict = field(default_factory=dict)

    # Any run-level notes
    notes: List[str] = field(default_factory=list)


class StorageManager:
    """
    Manages filesystem storage for AGRDA runs.

    Directory layout:
      runs/
        <run_id>/
          manifest.json         – schema version, checksums, timestamps
          run_record.json       – complete run metadata and results
          raw_measurements.csv  – immutable raw window measurements
          actuator_log.csv      – all actuator operations
          capability_report.json
    """

    def __init__(self, runs_dir: str = "runs") -> None:
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def save_run(
        self,
        record: RunRecord,
        raw_measurements: Optional[List[Dict]] = None,
    ) -> Path:
        """
        Write a complete run to disk.

        Returns the path to the run directory.
        """
        run_dir = self.runs_dir / record.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Write raw measurements (immutable)
        raw_path = run_dir / "raw_measurements.csv"
        if raw_measurements:
            self._write_csv(raw_path, raw_measurements)

        # Write actuator log
        if record.actuator_log:
            act_path = run_dir / "actuator_log.csv"
            self._write_csv(act_path, record.actuator_log)

        # Write capability report separately
        cap_path = run_dir / "capability_report.json"
        cap_path.write_text(
            json.dumps(record.capability_report, indent=2), encoding="utf-8"
        )

        # Write main run record (without raw measurements and capability)
        record_dict = asdict(record)
        record_dict.pop("capability_report", None)  # stored separately
        record_path = run_dir / "run_record.json"
        record_path.write_text(
            json.dumps(record_dict, indent=2), encoding="utf-8"
        )

        # Write manifest
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": record.run_id,
            "data_source": record.data_source,
            "timestamp": record.timestamp,
            "files": {
                "run_record": "run_record.json",
                "raw_measurements": "raw_measurements.csv"
                if raw_measurements else None,
                "actuator_log": "actuator_log.csv"
                if record.actuator_log else None,
                "capability_report": "capability_report.json",
            },
            "checksums": {},
        }
        # Compute checksums for key files
        for key, fname in manifest["files"].items():
            if fname:
                fpath = run_dir / fname
                if fpath.exists():
                    manifest["checksums"][fname] = self._sha256(fpath)

        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        return run_dir

    def load_run(self, run_id: str) -> Optional[RunRecord]:
        """Load a run record by ID. Returns None if not found."""
        run_dir = self.runs_dir / run_id
        record_path = run_dir / "run_record.json"
        cap_path = run_dir / "capability_report.json"

        if not record_path.exists():
            return None

        data = json.loads(record_path.read_text(encoding="utf-8"))
        if cap_path.exists():
            data["capability_report"] = json.loads(
                cap_path.read_text(encoding="utf-8")
            )
        else:
            data.setdefault("capability_report", {})

        return RunRecord(**data)

    def list_runs(self) -> List[str]:
        """Return list of run IDs in the runs directory."""
        return [
            d.name for d in self.runs_dir.iterdir()
            if d.is_dir() and (d / "manifest.json").exists()
        ]

    def load_raw_measurements(self, run_id: str) -> List[Dict]:
        """Load raw measurements CSV for a run."""
        path = self.runs_dir / run_id / "raw_measurements.csv"
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_csv(path: Path, rows: List[Dict]) -> None:
        if not rows:
            return
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        h.update(path.read_bytes())
        return h.hexdigest()
