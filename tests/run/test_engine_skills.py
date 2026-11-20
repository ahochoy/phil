from phil.agents.invoke import CommandLog
from phil.run.state import initial_state
from tests.run.conftest import TEST_CMD


def test_the_skill_runtime_is_loaded_once_per_run(make_harness, monkeypatch):
    loaded = []
    marker = object()

    def fake_load(config, repo_root, paths):
        loaded.append((repo_root, paths))
        return marker

    monkeypatch.setattr("phil.run.engine.load_runtime", fake_load)
    harness = make_harness({})
    state = initial_state(harness.deps.run_id, harness.plan, harness.base_sha, TEST_CMD)
    first = harness.engine._context(state, CommandLog())
    second = harness.engine._context(state, CommandLog())
    assert first.skills is marker and second.skills is marker
    assert loaded == [(harness.deps.repo_root, harness.deps.paths)]


def test_no_runtime_is_remembered_too(make_harness, monkeypatch):
    loaded = []
    monkeypatch.setattr("phil.run.engine.load_runtime", lambda *args: loaded.append(args))
    harness = make_harness({})
    state = initial_state(harness.deps.run_id, harness.plan, harness.base_sha, TEST_CMD)
    assert harness.engine._context(state, CommandLog()).skills is None
    assert harness.engine._context(state, CommandLog()).skills is None
    assert len(loaded) == 1
