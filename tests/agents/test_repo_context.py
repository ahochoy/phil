from dataclasses import replace

from phil.agents.fake import FakeAgent
from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.store.paths import ProjectPaths
from tests.agents.conftest import critique


class RecordingFactory:
    def __init__(self):
        self.contexts: list[str] = []

    def __call__(self, spec, model, workdir, tools, *, timeout_s=180, provider=None):
        self.contexts.append(spec.context)
        return FakeAgent([critique()])


def run(config, conn, artifacts, packet, spec=None, **overrides):
    factory = RecordingFactory()
    ctx = AgentContext(
        config=config, conn=conn, layer="run", run_id="r-0001", artifacts=artifacts, factory=factory, **overrides
    )
    invoke_agent(spec or get_spec("critic"), packet, ctx, node="critic")
    return factory.contexts


def test_a_given_block_reaches_a_repo_context_spec(config, conn, artifacts, critic_packet):
    assert run(config, conn, artifacts, critic_packet, repo_context="CTX") == ["CTX"]


def test_a_spec_without_the_flag_gets_no_context(config, conn, artifacts, critic_packet):
    spec = replace(get_spec("critic"), repo_context=False)
    assert run(config, conn, artifacts, critic_packet, spec, repo_context="CTX") == [""]


def test_none_builds_the_block_fresh(config, conn, artifacts, critic_packet, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("Run `make test`.\n", encoding="utf-8")
    paths = ProjectPaths("x", home=tmp_path / "home")
    [text] = run(config, conn, artifacts, critic_packet, repo_root=root, paths=paths)
    assert "### From AGENTS.md\nRun `make test`." in text


def test_an_empty_block_gives_no_context(config, conn, artifacts, critic_packet, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("ignored\n", encoding="utf-8")
    paths = ProjectPaths("x", home=tmp_path / "home")
    assert run(config, conn, artifacts, critic_packet, repo_root=root, paths=paths, repo_context="") == [""]
