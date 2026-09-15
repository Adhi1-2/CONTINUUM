from continuum.benchmark.controlled_failures import SCENARIOS, by_name


def test_all_eleven_scenarios_declared() -> None:
    assert len(SCENARIOS) == 11
    names = {scenario.scenario for scenario in SCENARIOS}
    assert names == {
        "process_crash",
        "context_compaction",
        "tool_failure",
        "api_timeout",
        "dataset_change",
        "file_modification",
        "permission_change",
        "model_switch",
        "external_side_effect",
        "stale_decision",
        "partial_completion",
    }


def test_dataset_change_ground_truth() -> None:
    scenario = by_name("dataset_change")
    assert scenario.checkpoint_version == "v3"
    assert scenario.environment_version == "v4"
    assert scenario.expected == "REPAIR_AND_RESUME"


def test_each_scenario_has_ground_truth() -> None:
    for scenario in SCENARIOS:
        assert scenario.expected in {
            "RESUME",
            "RETRY",
            "REPAIR_AND_RESUME",
            "REQUEST_HUMAN",
            "REPLAN",
        }
        assert scenario.description
