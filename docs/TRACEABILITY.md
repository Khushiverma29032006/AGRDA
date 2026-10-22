# AGRDA Research Traceability Table

Maps all document elements (D1–D6, RQ1–RQ6, H1–H6, C1–C9, S0–S9, B1–B4)
to modules, tests, and outputs.

---

## Design Differences (D1–D6)

| ID | Description | Module | Test | Output |
|----|------------|--------|------|--------|
| D1 | Separate intervention-effect, memory-mediated, total-contribution estimands | `estimation/estimator.py` (DoseEffectEstimate), `decision/policy.py` | `test_estimation.py`, `test_decision.py` | dose_effects table, decision.supported_claims |
| D2 | Condition memory-specific claim on exclusion-restriction framing and audit | `audit/checks.py` (C3-C5), `decision/policy.py` (O4 tier label) | `test_audit.py::test_c3_*`, `test_decision.py::test_o4_claims_conditional` | decision.unsupported_claims |
| D3 | Audit OS-actuator side effects (SMT, frequency, scheduling, cache, IRQ) | `audit/checks.py` (C5), `telemetry/models.py` (MetricLabel.ALTERNATIVE_PATHWAY) | `test_audit.py::test_c5_*` | C5 check result |
| D4 | Quantify false certification and false rejection | `baselines/comparators.py` (BaselineEvaluator.fcr, frr) | `compare` CLI | EvaluationSummary table |
| D5 | Decision policy that can downgrade or abstain | `decision/policy.py` (O1–O5) | `test_decision.py` | decision.outcome |
| D6 | Measure when/why audit fails | `scenarios/definitions.py` (S4–S9), `audit/checks.py` | `test_scenarios.py` | per-check status + reasons |

---

## Research Questions (RQ1–RQ6)

| RQ | Question | Analysis module | Output |
|----|---------|----------------|--------|
| RQ1 | Does AGRDA produce lower FCR than passive correlation? | `baselines/comparators.py` B1 vs B3 | `compare --scenario S0` FCR table |
| RQ2 | Does the audit battery reduce false certification vs. no-audit? | B2 vs B3 comparison | `compare --scenario S4` |
| RQ3 | Is the full 9-check audit necessary vs. B4 (partial)? | B3 vs B4 comparison | `compare` with all scenarios |
| RQ4 | Can AGRDA correctly identify null cases? | S0 scenario, O2 Null outcome | `simulate --scenario S0` |
| RQ5 | Does the audit detect genuine pathway violations? | S4, S9 scenarios, O3 outcome | `simulate --scenario S9` |
| RQ6 | Are CI estimates calibrated? | `estimation/estimator.py` bootstrap CI | CI coverage metric in EvaluationSummary |

---

## Hypotheses (H1–H6)

| H | Hypothesis | Status | Validation |
|---|-----------|--------|-----------|
| H1 | AGRDA has lower FCR than passive correlation | **Not yet tested on real hardware** | `compare --scenario S3` (simulation proxy) |
| H2 | AGRDA correctly identifies null scenarios | Verifiable in simulation | `test_scenarios.py::test_s0_null_outcome` |
| H3 | Full audit outperforms partial audit (B4) for FCR | **Not yet tested** | `compare` B3 vs B4 metrics |
| H4 | Pathway violation (S4, S9) consistently detected | Verifiable in simulation | `test_scenarios.py::test_s4_*`, `test_s9_*` |
| H5 | Carry-over detected by C8 when washout is short | Verifiable in simulation | `test_scenarios.py::test_s6_carryover_detected` |
| H6 | Noisy counters trigger C1 FAIL and prevent overclaiming | Verifiable in simulation | `test_scenarios.py::test_s7_noisy_counters_c1_fails` |

**NOTE: Hypotheses are research claims to be tested — they are not results.**

---

## Audit Checks (C1–C9)

| Check | Name | Module | Test | Scenarios |
|-------|------|--------|------|----------|
| C1 | Measurement Adequacy | `audit/checks.py::c1_measurement_adequacy` | `test_audit.py::TestAuditBattery::test_c1_*` | S7 |
| C2 | Intervention Strength | `audit/checks.py::c2_intervention_strength` | `test_audit.py::TestAuditBattery::test_c2_*` | All |
| C3 | Placebo Actuation | `audit/checks.py::c3_placebo` | `test_audit.py::TestAuditBattery::test_c3_*` | S0, S1 |
| C4 | Sentinel | `audit/checks.py::c4_sentinel` | `test_audit.py::TestAuditBattery::test_c4_*` | All |
| C5 | Alternative-Pathway Monitors | `audit/checks.py::c5_alternative_pathways` | `test_audit.py::TestAuditBattery::test_c5_*` | S4, S9 |
| C6 | Alternative-Actuator Consistency | `audit/checks.py::c6_alternative_actuator` | `test_audit.py::TestAuditBattery::test_c6_*` | — (UNRESOLVED on this host) |
| C7 | Dose-Response Consistency | `audit/checks.py::c7_dose_response_consistency` | `test_audit.py::TestAuditBattery::test_c7_*` | S8 |
| C8 | Carry-Over and Recovery | `audit/checks.py::c8_carryover` | `test_audit.py::TestAuditBattery::test_c8_*` | S6 |
| C9 | Phase and Temporal-Dependence Balance | `audit/checks.py::c9_phase_balance` | `test_audit.py::TestAuditBattery::test_c9_*` | All |

---

## Scenarios (S0–S9)

| Scenario | Name | Expected Outcome | Ground-Truth Causal Structure |
|----------|------|-----------------|------------------------------|
| S0 | Null | O2 Null | victim_impact=0.0 |
| S1 | Genuine memory-bandwidth contention | O4 tier2 | memory_pathway=0.95 |
| S2 | Multiple simultaneous interferers | O4 tier2 | 2 suspects, additive |
| S3 | Workload-phase correlation | O2 Null | impact=0.03 (below delta_eff) |
| S4 | CPU-frequency pathway | O3 Pathway Violation | memory_pathway=0.10 |
| S5 | Cache and SMT interference | O5 Effect Only | memory_pathway=0.60 |
| S6 | Carry-over | O1 Inconclusive | washout_residual > delta_eq |
| S7 | Weak/noisy counters | O1 Inconclusive | ICC=0.45, MDE=0.12 |
| S8 | Nonlinearity/saturation | O4 + flag | memory_pathway=0.90 |
| S9 | Exclusion-restriction violation | O3 Pathway Violation | memory_pathway=0.50 |

---

## Baselines (B1–B4)

| Baseline | Description | Module | What it tests |
|----------|------------|--------|--------------|
| B1 | Passive correlation | `baselines/comparators.py::B1PassiveCorrelation` | FAR in null/phase-correlated scenarios |
| B2 | Randomized, no audit | `baselines/comparators.py::B2RandomizedNoAudit` | FCR without validity checks |
| B3 | Full AGRDA | `baselines/comparators.py::B3FullAGRDA` | FCR with full 9-check audit |
| B4 | Partial audit (C3+C4 only) | `baselines/comparators.py::B4PartialAudit` | Is full audit necessary? |

---

## CLI Commands → Modules

| Command | Module | What it does |
|---------|--------|-------------|
| `check-capabilities` | `capabilities/detector.py` | Read-only host inspection |
| `simulate` | `runner.py` + all modules | Full simulation pipeline |
| `list-scenarios` | `scenarios/definitions.py` | Lists S0–S9 |
| `design` | `design/designer.py` | Shows randomized schedule |
| `report` | `reporting/generator.py` | Generates text report |
| `compare` | `baselines/comparators.py` | B1–B4 metric table |
