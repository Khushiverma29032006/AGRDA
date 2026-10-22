# AGRDA Architecture Notes

## Overview

AGRDA is structured as a **layered research harness** with explicit
interfaces between components so that:

1. The research/statistical logic never touches system controls directly.
2. Simulation backends can be replaced with real Linux backends
   without changing any analysis code.
3. All safety constraints are enforced at the actuator layer, not
   sprinkled throughout the codebase.

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    CLI (agrda.cli)                          │
│  check-capabilities │ simulate │ design │ compare │ report  │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│               ExperimentRunner (agrda.runner)               │
│  Orchestrates: design → pilot → measure → estimate →        │
│                audit → decide → save                        │
└──┬──────────┬──────────┬──────────┬──────────┬─────────────┘
   │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼
 Design    WorkloadMgr  Actuator  Estimator  Audit+Decision
 (OAT)     (SimBackend) (Sim)     (OLS+Perm) (C1-C9, O1-O5)
                          │
                       Watchdog
                    (Safety rollback)
                          │
                       Storage
                    (JSON + CSV)
```

---

## Key Interface Contracts

### ActuatorInterface
Every actuator (simulation, MBA, CPU-quota) must implement:
- `is_available() → bool`
- `capture_baseline(names)`
- `apply(name, dose) → ActuatorResult`
- `rollback(name) → ActuatorResult`
- `rollback_all() → List[ActuatorResult]`
- `verify(name) → (value, matches)`

**MBA and CPU-quota are separate actuator types** with different causal pathways.
Never substitute one for the other.

### WorkloadManager backend
Any backend must implement:
- `start_all()` / `stop_all()`
- `measure_window(window_id, doses) → List[SimMeasurement]`
- `capture_baseline(n_windows) → Dict[str, float]`
- `check_safety(name, dose) → (bool, str)`

### AuditBattery
Each check returns `CheckResult` with:
- `status: CheckStatus`  (PASS / FAIL / UNRESOLVED)
- `metric_values: Dict`
- `thresholds: Dict`
- `evidence: List[str]`
- `reasons: List[str]`
- `limitations: List[str]`

**UNRESOLVED is never promoted to PASS.** This is a hard invariant.

---

## Statistical Design Decisions

### Why OAT (one-at-a-time)?

For K ≤ 3 suspects, a full 4^K factorial design would require 64 cells
(K=3). With realistic window counts (n_blocks=4, 4 doses = 16 windows
per suspect), a full factorial is too large for the intervention budget.

OAT allows identification of main effects only. **Interactions are
explicitly NOT reported.**

*Implementation decision, not a claim in the research document.*

### Why block-bootstrap + permutation inference?

The document specifies randomization/permutation inference as the primary
method (design-based, makes no distributional assumptions beyond
exchangeability within blocks). Block-bootstrap serves as a secondary
check. OLS is used only to fit the quadratic dose–response curve.

### Why not adjust for post-treatment variables?

Frequency, scheduling, and SMT metrics measured DURING the intervention
are post-treatment. Adding them as covariates in the primary model would
bias the causal estimate. They appear only in the audit (C5) where they
serve as pathway monitors.

---

## Simulation Ground-Truth Structure

Each scenario has explicit ground-truth labels:
- `sim_victim_impact_fraction`: total degradation at baseline dose
- `sim_memory_pathway_fraction`: fraction through memory

These labels exist **only in simulation** and are used to:
1. Verify the audit battery detects the right conditions.
2. Compute FCR/FRR for baseline comparison.

They never appear in real-hardware run records.

---

## Safety Architecture

```
Controller loop
    │
    ├── wd.beat()  ←── must call every iteration
    │
    ▼
Watchdog thread (background)
    │
    ├── Timeout (>5s without beat) → rollback_all()
    ├── SIGINT/SIGTERM             → rollback_all()
    ├── atexit                     → rollback_all() (best-effort)
    └── Context manager __exit__   → rollback_all()

rollback_all() is idempotent — safe to call multiple times.
Target: rollback completes within 1 second (document requirement).
```

---

## Real Linux Adapter Plan (Stage 6)

When a Linux host with cgroup v2 and Intel RDT is available:

1. **MBAActuator** (`actuators/mba_actuator.py`):
   - Writes to `/sys/fs/resctrl/` CLOSID schemata
   - Read-back verifies within MBA granularity tolerance
   - Requires `root` or `CAP_SYS_ADMIN`

2. **CpuQuotaActuator** (`actuators/cpuquota_actuator.py`):
   - Writes to cgroup's `cpu.max`
   - Used as a COMPARATOR (known alternative pathway)
   - Must NOT be described as a memory-bandwidth control

3. **PerfTelemetry** (`telemetry/perf_collector.py`):
   - Runs `perf stat -e mem_load_retired.l3_miss,cycles,...`
   - Records running/enabled ratio; invalidates windows above threshold

4. **MBMTelemetry** (`telemetry/mbm_collector.py`):
   - Reads `/sys/fs/resctrl/mon_data/` for memory bandwidth monitoring

All real adapters must:
- Require `dry_run=False` + explicit operator confirmation
- Verify writes with read-back
- Log every operation
- Support the same `ActuatorInterface` and `TelemetryCollector` APIs

---

## Threshold Freeze Workflow

```
1. Run pilot on dedicated host:
     agrda pilot --host <hostname> --workload mem-bandwidth

2. Review pilot results (ICC, MDE, settling time)

3. Freeze thresholds:
     agrda freeze-config --pilot-run-id <id> --output configs/frozen_v1.toml

4. Inspect frozen config:
     cat configs/frozen_v1.toml
   # Status: FROZEN, timestamp, pilot_run_id included

5. Run confirmatory experiment referencing frozen config:
     agrda run --scenario S1 --config configs/frozen_v1.toml
```

**After freezing, thresholds cannot be changed without creating a new
frozen config version.** Tuning thresholds on confirmatory results
violates the research design.
