import json

import pytest
from pydantic import ValidationError

from phil.contracts import (
    ALL_CONTRACTS,
    Answer,
    ArchitectInput,
    AttemptWorklog,
    Brief,
    Goal,
    Plan,
    SelfCheck,
    Task,
    TaskResult,
    Worklog,
)
from phil.contracts.schema import export_schemas


def make_task(task_id: str = "MAPS-001") -> Task:
    return Task(
        id=task_id,
        description="Add lat/lng to Listing",
        acceptance_criteria=["Listing has lat and lng floats"],
        files_hint=["src/listing.py"],
    )


def test_task_defaults_to_todo():
    assert make_task().status == "TODO"


def test_task_id_must_match_keyword_pattern():
    with pytest.raises(ValidationError):
        make_task("maps-1")


def test_task_requires_acceptance_criteria():
    with pytest.raises(ValidationError):
        Task(id="MAPS-001", description="x", acceptance_criteria=[])


def test_plan_rejects_task_ids_with_other_keyword():
    with pytest.raises(ValidationError, match="keyword"):
        Plan(keyword="MAPS", description="d", tasks=[make_task("AUTH-001")])


def test_plan_rejects_duplicate_task_ids():
    with pytest.raises(ValidationError, match="duplicate"):
        Plan(keyword="MAPS", description="d", tasks=[make_task(), make_task()])


def test_plan_round_trips_through_json():
    plan = Plan(keyword="MAPS", description="d", tasks=[make_task()], story_ref="E4/S2")
    assert Plan.model_validate_json(plan.model_dump_json()) == plan
    assert plan.schema_version == 1


def test_contracts_forbid_extra_fields():
    with pytest.raises(ValidationError):
        Plan(keyword="MAPS", description="d", tasks=[make_task()], surprise=True)


def test_self_check_fields_are_required():
    with pytest.raises(ValidationError):
        SelfCheck(assumptions=[], evidence=[], risks=[], unverified=[])


def test_brief_headline_is_bounded():
    with pytest.raises(ValidationError):
        Brief(headline="x" * 121)


def test_brief_allows_at_most_five_points():
    with pytest.raises(ValidationError):
        Brief(headline="ok", points=["a", "b", "c", "d", "e", "f"])


def test_brief_points_are_bounded():
    with pytest.raises(ValidationError):
        Brief(headline="ok", points=["y" * 201])


def test_export_schemas_writes_one_file_per_contract(tmp_path):
    paths = export_schemas(tmp_path / "schemas")
    assert len(paths) == len(ALL_CONTRACTS)
    for path in paths:
        schema = json.loads(path.read_text())
        assert "properties" in schema
    assert (tmp_path / "schemas" / "Plan.schema.json").exists()


def test_task_defaults_to_tdd_without_a_check_cmd():
    task = make_task()
    assert (task.verify, task.check_cmd) == ("tdd", None)


def test_check_task_requires_a_check_cmd():
    with pytest.raises(ValidationError, match="check_cmd"):
        Task(id="MAPS-001", description="x", acceptance_criteria=["c"], verify="check")
    task = Task(id="MAPS-001", description="x", acceptance_criteria=["c"], verify="check", check_cmd="npm run build")
    assert task.check_cmd == "npm run build"


def test_tdd_task_forbids_a_check_cmd():
    with pytest.raises(ValidationError, match="check_cmd"):
        Task(id="MAPS-001", description="x", acceptance_criteria=["c"], check_cmd="npm run build")


def test_export_schemas_describe_the_check_fields(tmp_path):
    export_schemas(tmp_path / "schemas")
    schema = json.loads((tmp_path / "schemas" / "Plan.schema.json").read_text())
    task = schema["$defs"]["Task"]["properties"]
    assert task["verify"]["enum"] == ["tdd", "check"] and task["verify"]["default"] == "tdd"
    assert "description" in task["verify"] and "description" in task["check_cmd"]


def test_worklog_defaults_to_empty_and_task_result_carries_one():
    assert Worklog() == Worklog(files_changed=[], notes=[])
    result = TaskResult(phase="green", summary="s", files_changed=[], tests_added=[], self_check=_self_check())
    assert result.worklog == Worklog()


def _self_check() -> SelfCheck:
    return SelfCheck(assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[])


def _result_with_worklog(worklog: dict) -> TaskResult:
    return TaskResult.model_validate(
        {
            "phase": "green",
            "summary": "s",
            "files_changed": [],
            "tests_added": [],
            "self_check": _self_check().model_dump(),
            "worklog": worklog,
        }
    )


def test_worklog_limits_clip_instead_of_rejecting():
    notes = ["short"] * 6 + ["n" * 500]
    notes[1] = "m" * 500
    result = _result_with_worklog({"files_changed": ["c"] * 60, "notes": notes})
    assert result.worklog.notes == ["short", "m" * 200, "short", "short", "short"]
    assert len(result.worklog.files_changed) == 50
    stored = AttemptWorklog(files_read=[f"f{n}" for n in range(60)])
    assert stored.files_read == [f"f{n}" for n in range(50)]


def test_worklog_limits_stay_in_the_schema_as_guidance():
    schema = TaskResult.model_json_schema()["$defs"]["Worklog"]["properties"]
    assert schema["files_changed"]["maxItems"] == 50
    assert schema["notes"]["maxItems"] == 5 and schema["notes"]["items"]["maxLength"] == 200


def test_the_model_does_not_write_files_read():
    # The engine records what the file tools read; the output schema leaves it out, and a stray
    # value from the model is dropped rather than failing the whole output.
    schema = TaskResult.model_json_schema()["$defs"]["Worklog"]["properties"]
    assert "files_read" not in schema
    result = _result_with_worklog({"files_read": ["x.py"], "files_changed": ["a.py"]})
    assert result.worklog == Worklog(files_changed=["a.py"])


def test_the_stored_worklog_carries_files_read_into_the_implement_input():
    from phil.contracts import ImplementInput

    worklog = AttemptWorklog(files_read=["calc.py"], files_changed=["a.py"], notes=["n"])
    implement = ImplementInput(task=make_task(), phase="green", test_cmd="pytest", worklog=worklog.model_dump())
    assert implement.worklog == worklog
    assert "files_read" in ImplementInput.model_json_schema()["$defs"]["AttemptWorklog"]["properties"]


def test_implement_input_carries_an_optional_worklog_and_diff():
    from phil.contracts import ImplementInput

    implement = ImplementInput(task=make_task(), phase="green", test_cmd="pytest")
    assert (implement.worklog, implement.diff) == (None, "")


def test_goal_task_is_optional_and_accepts_a_task():
    assert Goal(objective="Add lat/lng to Listing").task is None
    task = make_task()
    assert Goal(objective="Add lat/lng to Listing", task=task).task.model_dump() == task.model_dump(
        exclude={"status"}
    )


@pytest.mark.parametrize("stub", ["x", "placeholder", "  TBD "], ids=["one-word", "filler", "filler-padded"])
def test_goal_rejects_a_stub_objective(stub):
    # A flaky intake call returned exactly these live: a one-word objective, or a filler value,
    # with every other field empty. Phil must never plan from one (contract retry catches it).
    with pytest.raises(ValidationError, match="not a placeholder"):
        Goal(objective=stub)


def test_goal_accepts_a_real_objective():
    assert Goal(objective="Fix the typo").objective == "Fix the typo"


@pytest.mark.parametrize(
    "fields",
    [{"id": "calc-1"}, {"verify": "check"}, {"acceptance_criteria": []}],
    ids=["malformed-id", "check-without-cmd", "no-criteria"],
)
def test_goal_keeps_a_quick_task_a_plan_would_reject(fields):
    # Intake's whole goal must survive a bad quick task; quick_plan rejects it later (Ruling R9).
    task = {"id": "CALC-001", "description": "d", "acceptance_criteria": ["c"]} | fields
    goal = Goal.model_validate({"objective": "Add lat/lng to Listing", "task": task})
    assert goal.task.model_dump(include=set(fields)) == fields


def test_goal_quick_task_still_lists_the_verify_modes():
    schema = Goal.model_json_schema()["$defs"]["QuickTask"]["properties"]
    assert schema["verify"]["enum"] == ["tdd", "check"]
    assert "pattern" not in schema["id"]
    assert all("description" in prop for prop in schema.values())


def test_architect_input_prior_attempt_defaults_to_empty():
    assert ArchitectInput(goal=Goal(objective="Add lat/lng to Listing")).prior_attempt == []


def test_architect_input_carries_prior_attempt_worklogs():
    worklog = AttemptWorklog(files_changed=["a.py"], notes=["tried X"])
    architect_input = ArchitectInput(goal=Goal(objective="Add lat/lng to Listing"), prior_attempt=[worklog])
    assert architect_input.prior_attempt == [worklog]


def test_answer_diagnosis_defaults_to_false():
    assert Answer(text="because of the bug in x.py").diagnosis is False
    assert Answer(text="because of the bug in x.py", diagnosis=True).diagnosis is True
