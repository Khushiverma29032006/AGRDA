"""Small end-to-end simulation check; does not change host settings."""
from agrda.design.matrix import BlockedRandomizationEngine
from agrda.workloads.models import EnvironmentSimulator, MBAActuator, SimulationScenario


def main() -> None:
    design = BlockedRandomizationEngine(
        suspects=("S1", "S2"), n_blocks=1, seed=17,
        warmup_s=1, measurement_s=2, washout_s=1,
    ).create_design()
    assert design.main_effects_identifiable
    assert design.interaction_identifiable
    assert len(design.windows) == 16

    actuator = MBAActuator()
    simulator = EnvironmentSimulator(SimulationScenario.S1, actuator=actuator, seed=17)
    required_metrics = {
        "source", "scenario", "window_id", "applied_dose", "throughput",
        "mem_bw_gbps", "mem_stall_cpi", "llc_miss_rate", "frequency_ghz",
        "quality_ok",
    }
    for window in design.windows:
        telemetry = simulator.simulate(
            window.doses,
            phase=window.window_id % 4,
            washout_s=window.phase_durations_s["washout"],
        )
        assert set(telemetry) == {"V", "S1", "S2", "P", "N-", "N+"}
        for metrics in telemetry.values():
            assert required_metrics <= metrics.keys()
            assert metrics["source"] == "SIMULATION"
            assert metrics["quality_ok"] is True

    assert actuator.host_mutations == 0
    assert actuator.applied_doses
    print("AGRDA simulation smoke test passed; no host settings changed.")


if __name__ == "__main__":
    main()