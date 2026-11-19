import json

from phil.agents.invoke import CommandLog
from phil.run.state import initial_state
from tests.run.conftest import TEST_CMD, review, tester_report, write_green, write_red

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c6300010000050001")


def scripts():
    return {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}


def attach(harness, tmp_path, *names):
    """Write the run's attachments.json for the named images (real files)."""
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(PNG)
        paths.append(str(path))
    write_attachments(harness, json.dumps({"images": paths}))
    return paths


def write_attachments(harness, text):
    run_dir = harness.deps.artifacts.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "attachments.json").write_text(text)


def content_of(harness, role):
    payload = next(p for r, p in harness.factory.calls if r == role)
    return payload["messages"][0]["content"]


def test_implementer_packet_carries_images(make_harness, tmp_path):
    harness = make_harness(scripts())
    attach(harness, tmp_path, "shot.png")
    harness.start()
    content = content_of(harness, "implementer")
    assert isinstance(content, list)
    assert [part["type"] for part in content] == ["text", "image"]


def test_tester_and_reviewer_get_paths_not_pictures(make_harness, tmp_path):
    harness = make_harness(scripts())
    (path,) = attach(harness, tmp_path, "shot.png")
    harness.start()
    for role in ("tester", "reviewer"):
        content = content_of(harness, role)
        assert isinstance(content, str), role
        assert "Images the user attached:" in content and path in content


def test_missing_attachments_file_is_fine(make_harness):
    harness = make_harness(scripts())
    harness.start()
    for role in ("implementer", "tester", "reviewer"):
        content = content_of(harness, role)
        assert isinstance(content, str) and "Images the user attached" not in content


def test_unreadable_attachments_file_is_fine(make_harness):
    harness = make_harness(scripts())
    write_attachments(harness, "{not json")
    harness.start()
    assert isinstance(content_of(harness, "implementer"), str)


def test_vision_notice_becomes_a_run_event(make_harness):
    harness = make_harness({})
    state = initial_state(harness.deps.run_id, harness.plan, harness.base_sha, TEST_CMD)
    ctx = harness.engine._context(state, CommandLog())
    assert ctx.notice is not None
    ctx.notice("↪ image: implementer runs on a vision model")
    assert harness.deps.events.latest("notice")["message"] == "↪ image: implementer runs on a vision model"
