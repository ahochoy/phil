from dataclasses import replace

from phil.store.paths import ProjectPaths
from tests.chat.test_controller import run_chat


def test_the_chat_loads_its_skills_once_and_jobs_inherit_them(calc_repo, monkeypatch):
    loaded = []
    marker = object()

    def fake_load(config, repo_root, paths):
        loaded.append((repo_root, paths.slug if paths is not None else None))
        return marker

    monkeypatch.setattr("phil.chat.controller.load_runtime", fake_load)
    box = {}

    def grab(controller):
        box["controller"] = controller
        return None

    run_chat(calc_repo, [grab], {})
    controller = box["controller"]
    assert controller.ctx.skills is marker
    assert replace(controller.ctx, conn=None).skills is marker  # how a job copies the context
    assert loaded == [(controller.info.root, ProjectPaths(controller.info.slug).slug)]
