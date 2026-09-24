import json

import pytest
from pydantic import ValidationError

from phil.contracts import (
    ALL_CONTRACTS,
    Brief,
    Plan,
    SelfCheck,
    Task,
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
