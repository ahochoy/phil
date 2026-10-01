from pathlib import Path

import pytest

from tests.helpers import run_git


def _is_live(request: pytest.FixtureRequest) -> bool:
    """Live tests (`-m live`) and the benchmark (`-m bench`), both opt-in, call real models."""
    return any(request.node.get_closest_marker(name) is not None for name in ("live", "bench"))


@pytest.fixture(autouse=True)
def phil_home(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if _is_live(request):
        # Live tests and the benchmark call real APIs: let the real default price book use its
        # real cache under the real PHIL_HOME instead of refetching into a temp dir. The
        # benchmark's projects and results land there too, where the user can inspect them.
        from phil.store.paths import phil_home as real_phil_home

        return real_phil_home()
    home = tmp_path / "phil_home"
    monkeypatch.setenv("PHIL_HOME", str(home))
    return home


def guard_against_price_fetches(monkeypatch: pytest.MonkeyPatch):
    """Generator body for the `no_price_fetch` fixture below, kept as a plain function so
    tests/test_network_guard.py can drive it directly (pytest fixtures can't be called
    directly).

    Tests must never touch the network. PriceBook._load() catches any exception a fetch raises
    (falling back to a stale cache, or an empty book) so raising here would be swallowed rather
    than failing the test. Instead, record whether the default fetch (e.g. the process-wide
    default price book) was ever attempted, and fail loudly afterwards, once back in control
    outside that try/except.
    """
    attempted = False

    def refuse() -> dict:
        nonlocal attempted
        attempted = True
        raise AssertionError("tests must not fetch prices from the network")

    monkeypatch.setattr("phil.agents.pricing._default_fetch", refuse)
    monkeypatch.setattr("phil.agents.invoke._default_prices", None)
    yield
    if attempted:
        pytest.fail("a test attempted to fetch prices from the network (phil.agents.pricing._default_fetch)")


@pytest.fixture(autouse=True)
def no_price_fetch(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    if _is_live(request):
        yield  # live tests may fetch the real OpenRouter price list
        return
    yield from guard_against_price_fetches(monkeypatch)


@pytest.fixture(autouse=True)
def isolated_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests must not pick up the machine's own global/system git config (e.g. a real
    # gpg.format=ssh signing key), or they could sign test commits with the user's real key.
    global_config = tmp_path / "gitconfig"
    global_config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture(autouse=True)
def no_real_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests must never reach GitHub: the default publisher is an unavailable fake. This holds for
    # live and benchmark tests too: none of them publishes, so none needs the real `gh`. Tests that
    # publish patch `phil.publish.publisher.make_publisher` (or pass a FakePublisher) themselves.
    from phil.publish.publisher import FakePublisher

    monkeypatch.setattr(
        "phil.publish.publisher.make_publisher", lambda repo_root: FakePublisher(unavailable="gh is disabled in tests")
    )


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch):
    # Children spawned by `spawn_worker` (e.g. in tests/run/test_launch.py and
    # tests/cli/test_stop_command.py) don't inherit the `set_keyring` call below — each is a
    # fresh process that auto-detects its own backend on first use. Without this, a worker
    # process would fall through to the real OS keychain. `PYTHON_KEYRING_BACKEND` is `keyring`'s
    # own env var for pinning the backend; children that build their env from `os.environ` (via
    # `worker_env`, or by inheriting it outright when `env=None`) pick it up automatically.
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    import keyring
    from keyring.backend import KeyringBackend

    class MemoryKeyring(KeyringBackend):
        priority = 1

        def __init__(self):
            super().__init__()
            self.store: dict[tuple[str, str], str] = {}

        def get_password(self, service, username):
            return self.store.get((service, username))

        def set_password(self, service, username, password):
            self.store[(service, username)] = password

        def delete_password(self, service, username):
            from keyring.errors import PasswordDeleteError

            if (service, username) not in self.store:
                raise PasswordDeleteError(username)
            del self.store[(service, username)]

    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "target"
    repo.mkdir()
    repo = repo.resolve()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test")
    run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "init")
    return repo
