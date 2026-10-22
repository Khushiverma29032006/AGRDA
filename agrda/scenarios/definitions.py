"""
Scenario definitions S0–S9.

Each scenario has:
  - explicit ground-truth causal structure (for simulation verification)
  - workload configurations
  - expected decision outcome (for test validation)
  - notes about what the scenario tests

IMPORTANT:
  All scenarios are labelled SIMULATION.
  Ground-truth labels exist ONLY in simulation. They must NEVER appear
  in real-hardware result files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..workloads.models import (
    WorkloadConfig, WorkloadProfile, WorkloadRole,
)
from ..decision.policy import Outcome


@dataclass
class ScenarioSpec:
    """
    Specification for one AGRDA simulation scenario.

    Attributes
    ----------
    scenario_id   : e.g. "S0", "S1", ...
    name          : short name
    description   : what the scenario tests
    workloads     : list of WorkloadConfig for this scenario
    expected_outcome : which Outcome this scenario is designed to produce
                       (for automated test verification)
    expected_pathway_violation : whether C3/C4/C5 should FAIL
    expected_carryover         : whether C8 should FAIL (S6)
    expected_noisy_counters    : whether C1 should FAIL (S7)
    ground_truth_notes         : documentation of the causal structure
    """
    scenario_id: str
    name: str
    description: str
    workloads: List[WorkloadConfig] = field(default_factory=list)
    expected_outcome: Outcome = Outcome.O1_INCONCLUSIVE
    expected_pathway_violation: bool = False
    expected_carryover: bool = False
    expected_noisy_counters: bool = False
    ground_truth_notes: List[str] = field(default_factory=list)
    # Extra parameters for audit mock data
    extra_audit_data: Dict = field(default_factory=dict)


def _make_standard_support_workloads(
    suspect_profile: WorkloadProfile,
    victim_impact: float,
    memory_pathway_fraction: float,
    n_suspects: int = 1,
    seed_offset: int = 0,
) -> List[WorkloadConfig]:
    """Helper: build the standard V + S1..SK + P + N- + N+ set."""
    configs = []

    # Victim
    configs.append(WorkloadConfig(
        name="victim",
        role=WorkloadRole.VICTIM,
        profile=WorkloadProfile.MEMORY_BANDWIDTH_BOUND,
        sim_base_throughput=1000.0,
        sim_mem_bw_gbps=20.0,
        seed_offset=seed_offset,
    ))

    # Suspects
    per_suspect_impact = victim_impact / n_suspects
    for i in range(n_suspects):
        configs.append(WorkloadConfig(
            name=f"suspect_{i + 1}",
            role=WorkloadRole.SUSPECT,
            profile=suspect_profile,
            sim_base_throughput=800.0,
            sim_mem_bw_gbps=15.0 / n_suspects,
            sim_victim_impact_fraction=per_suspect_impact,
            sim_memory_pathway_fraction=memory_pathway_fraction,
            seed_offset=seed_offset + i + 1,
        ))

    # Placebo (negligible memory)
    configs.append(WorkloadConfig(
        name="placebo",
        role=WorkloadRole.PLACEBO,
        profile=WorkloadProfile.NEGLIGIBLE,
        sim_base_throughput=500.0,
        sim_mem_bw_gbps=0.5,
        seed_offset=seed_offset + 10,
    ))

    # Negative sentinel (disjoint resources, should NOT respond)
    configs.append(WorkloadConfig(
        name="neg_sentinel",
        role=WorkloadRole.NEG_SENTINEL,
        profile=WorkloadProfile.COMPUTE_BOUND,
        sim_base_throughput=700.0,
        sim_mem_bw_gbps=1.0,
        seed_offset=seed_offset + 11,
    ))

    # Positive sentinel (memory-bound, shares victim's memory bus)
    configs.append(WorkloadConfig(
        name="pos_sentinel",
        role=WorkloadRole.POS_SENTINEL,
        profile=WorkloadProfile.MEMORY_BANDWIDTH_BOUND,
        sim_base_throughput=600.0,
        sim_mem_bw_gbps=10.0,
        sim_victim_impact_fraction=victim_impact,
        sim_memory_pathway_fraction=memory_pathway_fraction,
        seed_offset=seed_offset + 12,
    ))

    return configs


# ===========================================================================
# S0: Null – suspect has negligible memory traffic and compute
# ===========================================================================
S0 = ScenarioSpec(
    scenario_id="S0",
    name="Null (no interference)",
    description=(
        "Suspect has negligible memory traffic. "
        "Victim should show no relevant dose-response. "
        "Expected outcome: O2 Null."
    ),
    workloads=_make_standard_support_workloads(
        suspect_profile=WorkloadProfile.NEGLIGIBLE,
        victim_impact=0.0,
        memory_pathway_fraction=1.0,
        seed_offset=0,
    ),
    expected_outcome=Outcome.O2_NULL,
    ground_truth_notes=[
        "No causal effect from suspect to victim (victim_impact=0.0)",
        "Dose changes should produce no victim throughput change",
    ],
    extra_audit_data={
        "suspect_bw_reduction_fraction": 0.05,  # very little to reduce
        "mediator_effect_fraction": 0.02,
        "placebo_victim_effect": 0.005,
        "neg_sentinel_effect": 0.003,
        "pos_sentinel_effect": 0.02,   # positive sentinel barely responds
        "washout_residual_fraction": 0.005,
    },
)

# ===========================================================================
# S1: Genuine memory-bandwidth contention
# ===========================================================================
S1 = ScenarioSpec(
    scenario_id="S1",
    name="Genuine memory-bandwidth contention",
    description=(
        "Memory-heavy suspect causes memory-bound victim to degrade. "
        "Throttling suspect reduces interference via memory pathway only. "
        "Expected outcome: O4 Supported Attribution (tier2 in simulation)."
    ),
    workloads=_make_standard_support_workloads(
        suspect_profile=WorkloadProfile.MEMORY_BANDWIDTH_BOUND,
        victim_impact=0.20,           # 20% throughput degradation at full dose
        memory_pathway_fraction=0.95,  # 95% through memory
        seed_offset=100,
    ),
    expected_outcome=Outcome.O4_SUPPORTED_ATTRIBUTION,
    ground_truth_notes=[
        "Suspect causes 20% victim degradation at baseline",
        "95% of effect is via memory bandwidth contention",
        "5% is via shared cache / LLC pressure (small alternative pathway)",
        "Throttling suspect at dose=1.0 nearly eliminates interference",
    ],
    extra_audit_data={
        "suspect_bw_reduction_fraction": 0.75,
        "mediator_effect_fraction": 0.18,
        "placebo_victim_effect": 0.004,    # well below delta_eq
        "neg_sentinel_effect": 0.003,
        "pos_sentinel_effect": 0.22,
        "washout_residual_fraction": 0.006,
    },
)

# ===========================================================================
# S2: Multiple simultaneous interferers
# ===========================================================================
S2 = ScenarioSpec(
    scenario_id="S2",
    name="Multiple simultaneous interferers",
    description=(
        "Two suspects each contributing additive memory interference. "
        "OAT design identifies main effects but NOT interactions. "
        "Expected outcome: O4 (tier2) or O5 depending on interaction identifiability."
    ),
    workloads=_make_standard_support_workloads(
        suspect_profile=WorkloadProfile.MEMORY_BANDWIDTH_BOUND,
        victim_impact=0.30,
        memory_pathway_fraction=0.90,
        n_suspects=2,
        seed_offset=200,
    ),
    expected_outcome=Outcome.O4_SUPPORTED_ATTRIBUTION,
    ground_truth_notes=[
        "Two suspects each cause ~15% victim degradation",
        "Combined degradation ~30% (additive model in simulation)",
        "OAT design cannot identify interaction effects — not reported",
    ],
    extra_audit_data={
        "suspect_bw_reduction_fraction": 0.70,
        "mediator_effect_fraction": 0.25,
        "placebo_victim_effect": 0.005,
        "neg_sentinel_effect": 0.004,
        "pos_sentinel_effect": 0.28,
        "washout_residual_fraction": 0.007,
    },
)

# ===========================================================================
# S3: Workload-phase correlation (decoy/passive correlation)
# ===========================================================================
S3 = ScenarioSpec(
    scenario_id="S3",
    name="Workload-phase correlation (passive correlation decoy)",
    description=(
        "Suspect's activity aligns with victim phases, creating misleading "
        "passive correlation. Suspect has minimal actual memory effect. "
        "B1 (passive correlation) would falsely accuse suspect. "
        "AGRDA should detect weak intervention effect."
    ),
    workloads=_make_standard_support_workloads(
        suspect_profile=WorkloadProfile.COMPUTE_BOUND,
        victim_impact=0.03,           # tiny actual effect
        memory_pathway_fraction=0.5,
        seed_offset=300,
    ),
    expected_outcome=Outcome.O2_NULL,   # effect is below delta_eff
    ground_truth_notes=[
        "Suspect's compute activity is phase-correlated with victim",
        "Actual memory interference: ~3% (below delta_eff=5%)",
        "Passive correlation would overestimate the effect",
        "AGRDA randomised design breaks the spurious correlation",
    ],
    extra_audit_data={
        "suspect_bw_reduction_fraction": 0.30,
        "mediator_effect_fraction": 0.02,
        "placebo_victim_effect": 0.004,
        "neg_sentinel_effect": 0.003,
        "pos_sentinel_effect": 0.03,
        "washout_residual_fraction": 0.005,
    },
)

# ===========================================================================
# S4: CPU-frequency pathway
# ===========================================================================
S4 = ScenarioSpec(
    scenario_id="S4",
    name="CPU-frequency pathway interference",
    description=(
        "Compute-heavy suspect with low memory traffic. "
        "Power/frequency changes affect victim via non-memory pathway. "
        "C5 should detect frequency pathway. Expected: O3 Pathway Violation."
    ),
    workloads=_make_standard_support_workloads(
        suspect_profile=WorkloadProfile.COMPUTE_BOUND,
        victim_impact=0.12,
        memory_pathway_fraction=0.10,   # only 10% through memory
        seed_offset=400,
    ),
    expected_outcome=Outcome.O3_PATHWAY_VIOLATION,
    expected_pathway_violation=True,
    ground_truth_notes=[
        "Suspect has low memory bandwidth but high CPU frequency pressure",
        "90% of victim effect is via frequency/thermal pathway",
        "Memory pathway fraction = 0.10",
        "C5 (alternative pathways) should detect the frequency component",
    ],
    extra_audit_data={
        "suspect_bw_reduction_fraction": 0.15,
        "mediator_effect_fraction": 0.01,
        "placebo_victim_effect": 0.004,
        "neg_sentinel_effect": 0.003,
        "pos_sentinel_effect": 0.13,
        "washout_residual_fraction": 0.006,
        # Inject a frequency pathway effect > delta_eq
        "pathway_effects": {
            "cpu_frequency_drop_fraction": 0.08,  # 8% freq drop > delta_eq=1.25%
        },
    },
)

# ===========================================================================
# S5: Cache and SMT interference
# ===========================================================================
S5 = ScenarioSpec(
    scenario_id="S5",
    name="Cache and SMT interference",
    description=(
        "Cache-resident suspect evicting victim's LLC footprint. "
        "Interference primarily through LLC / SMT, not raw bandwidth. "
        "MBA has limited effect on cache sharing."
    ),
    workloads=_make_standard_support_workloads(
        suspect_profile=WorkloadProfile.CACHE_RESIDENT,
        victim_impact=0.15,
        memory_pathway_fraction=0.60,   # 60% memory/LLC, 40% other
        seed_offset=500,
    ),
    expected_outcome=Outcome.O5_INTERVENTION_EFFECT_ONLY,
    ground_truth_notes=[
        "Cache thrashing causes 15% victim degradation",
        "60% through LLC/memory pathway, 40% through SMT/scheduler",
        "MBA throttles bandwidth but not LLC sharing directly",
        "C5 pathway monitors partially UNRESOLVED (no LLC isolation on sim)",
    ],
    extra_audit_data={
        "suspect_bw_reduction_fraction": 0.50,
        "mediator_effect_fraction": 0.10,
        "placebo_victim_effect": 0.006,
        "neg_sentinel_effect": 0.004,
        "pos_sentinel_effect": 0.14,
        "washout_residual_fraction": 0.007,
    },
)

# ===========================================================================
# S6: Carry-over (intentionally inadequate washout)
# ===========================================================================
S6 = ScenarioSpec(
    scenario_id="S6",
    name="Carry-over (inadequate washout)",
    description=(
        "Deliberately short washout. C8 should detect carry-over. "
        "Expected: O1 Inconclusive (C8 FAIL → design gate concern)."
    ),
    workloads=_make_standard_support_workloads(
        suspect_profile=WorkloadProfile.MEMORY_BANDWIDTH_BOUND,
        victim_impact=0.18,
        memory_pathway_fraction=0.90,
        seed_offset=600,
    ),
    expected_outcome=Outcome.O1_INCONCLUSIVE,
    expected_carryover=True,
    ground_truth_notes=[
        "Washout is intentionally too short (5s instead of 20s)",
        "Previous dose bleeds into next window",
        "C8 (carry-over) should FAIL",
        "Run should be flagged invalid or inconclusive",
    ],
    extra_audit_data={
        "suspect_bw_reduction_fraction": 0.70,
        "mediator_effect_fraction": 0.16,
        "placebo_victim_effect": 0.004,
        "neg_sentinel_effect": 0.003,
        "pos_sentinel_effect": 0.20,
        # Inject carry-over residual > delta_eq
        "washout_residual_fraction": 0.04,   # 4% > delta_eq=1.25%
    },
)

# ===========================================================================
# S7: Weak / noisy counters
# ===========================================================================
S7 = ScenarioSpec(
    scenario_id="S7",
    name="Weak / noisy counters",
    description=(
        "Multiplexed or deliberately noisy mediator metrics. "
        "C1 should detect inadequate measurement quality. "
        "Expected: O1 Inconclusive (C1 FAIL)."
    ),
    workloads=_make_standard_support_workloads(
        suspect_profile=WorkloadProfile.MEMORY_BANDWIDTH_BOUND,
        victim_impact=0.18,
        memory_pathway_fraction=0.90,
        seed_offset=700,
    ),
    expected_outcome=Outcome.O1_INCONCLUSIVE,
    expected_noisy_counters=True,
    ground_truth_notes=[
        "Mediator counter has high multiplexing ratio (>30%)",
        "ICC of victim throughput: 0.45 (below 0.70 threshold)",
        "MDE: 0.12 (above delta_eff=0.05)",
        "C1 should FAIL, preventing overclaiming",
    ],
)

# ===========================================================================
# S8: Nonlinearity / saturation
# ===========================================================================
S8 = ScenarioSpec(
    scenario_id="S8",
    name="Nonlinearity / saturation",
    description=(
        "Small-dose response differs from full-removal extrapolation. "
        "Estimator should flag extrapolation and not claim total contribution "
        "from small-dose effects."
    ),
    workloads=_make_standard_support_workloads(
        suspect_profile=WorkloadProfile.MEMORY_BANDWIDTH_BOUND,
        victim_impact=0.25,
        memory_pathway_fraction=0.90,
        seed_offset=800,
    ),
    expected_outcome=Outcome.O4_SUPPORTED_ATTRIBUTION,
    ground_truth_notes=[
        "Effect saturates at ~50% throttle; further throttling gives little gain",
        "Small-dose linear extrapolation would overestimate total contribution",
        "Report must flag extrapolation with uncertainty interval",
    ],
    extra_audit_data={
        "suspect_bw_reduction_fraction": 0.65,
        "mediator_effect_fraction": 0.20,
        "placebo_victim_effect": 0.005,
        "neg_sentinel_effect": 0.003,
        "pos_sentinel_effect": 0.24,
        "washout_residual_fraction": 0.006,
    },
)

# ===========================================================================
# S9: Injected exclusion-restriction violation
# ===========================================================================
S9 = ScenarioSpec(
    scenario_id="S9",
    name="Injected exclusion-restriction violation",
    description=(
        "Hybrid suspect with known non-memory pathway (controlled interrupt load). "
        "MBA throttle affects memory AND interrupts. "
        "C3, C4, or C5 should detect the violation. "
        "Expected: O3 Pathway Violation."
    ),
    workloads=_make_standard_support_workloads(
        suspect_profile=WorkloadProfile.MIXED_HYBRID,
        victim_impact=0.20,
        memory_pathway_fraction=0.50,   # 50/50 memory vs. interrupt path
        seed_offset=900,
    ),
    expected_outcome=Outcome.O3_PATHWAY_VIOLATION,
    expected_pathway_violation=True,
    ground_truth_notes=[
        "Suspect affects victim via both memory bandwidth AND interrupt load",
        "Throttling suspect changes BOTH pathways simultaneously",
        "Exclusion restriction (intervention affects victim ONLY via memory) violated",
        "C5 should detect interrupt pathway; C3/C4 may also signal",
    ],
    extra_audit_data={
        "suspect_bw_reduction_fraction": 0.55,
        "mediator_effect_fraction": 0.10,
        "placebo_victim_effect": 0.004,
        "neg_sentinel_effect": 0.003,
        "pos_sentinel_effect": 0.19,
        "washout_residual_fraction": 0.006,
        # Inject interrupt pathway violation
        "pathway_effects": {
            "interrupt_rate_change_fraction": 0.05,   # 5% > delta_eq=1.25%
        },
    },
)


# ===========================================================================
# Registry
# ===========================================================================

ALL_SCENARIOS: Dict[str, ScenarioSpec] = {
    s.scenario_id: s
    for s in [S0, S1, S2, S3, S4, S5, S6, S7, S8, S9]
}


def get_scenario(scenario_id: str) -> ScenarioSpec:
    if scenario_id not in ALL_SCENARIOS:
        raise ValueError(
            f"Unknown scenario: {scenario_id}. "
            f"Available: {list(ALL_SCENARIOS.keys())}"
        )
    return ALL_SCENARIOS[scenario_id]


def list_scenarios() -> List[str]:
    return [
        f"{sid}: {spec.name}"
        for sid, spec in ALL_SCENARIOS.items()
    ]
