import logging
import warnings
from pathlib import Path

import pytest

from phil import diagnostics


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("PHIL_DEBUG", raising=False)
    yield
    diagnostics.reset()


@pytest.mark.parametrize(
    ("value", "on"),
    [("1", True), ("true", True), (" YES ", True), ("0", False), ("false", False), ("", False), ("no", False)],
)
def test_debug_is_on_only_for_yes_values(monkeypatch, value, on):
    monkeypatch.setenv("PHIL_DEBUG", value)
    assert diagnostics.debug_enabled() is on


def test_a_library_log_line_goes_to_the_file_not_the_terminal(tmp_path, capfd):
    diagnostics.configure(tmp_path / "logs" / "phil.log")
    logging.getLogger("somelib").warning("a library log line")
    logging.getLogger("somelib").info("library chatter")
    logging.getLogger("phil.x").info("phil detail")
    out, err = capfd.readouterr()
    assert (out, err) == ("", "")
    log = (tmp_path / "logs" / "phil.log").read_text(encoding="utf-8")
    assert "a library log line" in log and "phil detail" in log
    assert "library chatter" not in log


def test_a_warning_becomes_a_log_record(tmp_path, capfd):
    diagnostics.configure(tmp_path / "phil.log")
    warnings.warn("a library warning", UserWarning)  # attributed to this test module, not to phil
    assert capfd.readouterr().err == ""
    assert "a library warning" in (tmp_path / "phil.log").read_text(encoding="utf-8")


def test_debug_mirrors_to_stderr(tmp_path, capfd, monkeypatch):
    monkeypatch.setenv("PHIL_DEBUG", "1")
    diagnostics.configure(tmp_path / "phil.log")
    logging.getLogger("phil.x").debug("phil debug detail")
    logging.getLogger("somelib").info("library chatter")
    logging.getLogger("somelib").warning("a library log line")
    err = capfd.readouterr().err
    assert "phil debug detail" in err and "a library log line" in err
    assert "library chatter" not in err


def test_configure_twice_leaves_one_file_handler(tmp_path):
    before = list(logging.getLogger().handlers)
    diagnostics.configure(tmp_path / "a.log")
    diagnostics.configure(tmp_path / "b.log")
    added = [h for h in logging.getLogger().handlers if h not in before]
    assert len(added) == 1
    assert diagnostics.configured()
    diagnostics.reset()
    assert logging.getLogger().handlers == before
    assert not diagnostics.configured()


def test_redirect_switches_files_and_back(tmp_path):
    diagnostics.configure(tmp_path / "phil.log")
    log = logging.getLogger("somelib")
    with diagnostics.redirect(tmp_path / "chat" / "phil.log"):
        log.warning("inside the chat")
    log.warning("after the chat")
    chat = (tmp_path / "chat" / "phil.log").read_text(encoding="utf-8")
    main = (tmp_path / "phil.log").read_text(encoding="utf-8")
    assert "inside the chat" in chat and "after the chat" not in chat
    assert "after the chat" in main and "inside the chat" not in main


def test_redirect_works_without_configure(tmp_path, capfd):
    with diagnostics.redirect(tmp_path / "chat" / "phil.log"):
        logging.getLogger("phil.watch").warning("watcher failed")
    assert capfd.readouterr().err == ""
    assert "watcher failed" in (tmp_path / "chat" / "phil.log").read_text(encoding="utf-8")


def test_an_unusable_log_directory_stays_silent(tmp_path, capfd):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    diagnostics.configure(blocker / "logs" / "phil.log")
    logging.getLogger("somelib").warning("nowhere to go")
    assert capfd.readouterr().err == ""


def test_a_log_that_cannot_be_written_stays_silent(tmp_path, capfd):
    (tmp_path / "phil.log").mkdir()  # opening it for append fails at the first write
    diagnostics.configure(tmp_path / "phil.log")
    logging.getLogger("somelib").warning("nowhere to go")
    assert capfd.readouterr().err == ""


def test_crash_writes_a_file_without_locals(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path))

    def explode():
        planted_secret = "sk-planted-123"  # noqa: F841 - must not reach the file
        raise RuntimeError("it broke")

    try:
        explode()
    except RuntimeError as exc:
        path = diagnostics.crash(exc, "keys set")
    assert path is not None and path.parent == tmp_path / "logs"
    assert path.name.startswith("crash-") and path.suffix == ".log"
    text = path.read_text(encoding="utf-8")
    assert "RuntimeError: it broke" in text and "Command: keys set" in text
    assert "Phil:" in text and "Python:" in text and "OS:" in text
    assert "sk-planted-123" not in text


def test_crash_keeps_the_newest_twenty(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path))
    logs = tmp_path / "logs"
    logs.mkdir()
    for i in range(25):
        (logs / f"crash-20260101T0000{i:02d}000000Z-1.log").write_text("old")
    path = diagnostics.crash(RuntimeError("new"), None)
    kept = sorted(p.name for p in logs.glob("crash-*.log"))
    assert len(kept) == diagnostics.CRASH_KEEP
    assert path.name in kept
    assert "crash-20260101T000000000000Z-1.log" not in kept


def test_crash_returns_none_when_it_cannot_write(tmp_path, monkeypatch):
    blocker = tmp_path / "home"
    blocker.write_text("x")
    monkeypatch.setenv("PHIL_HOME", str(blocker))
    assert diagnostics.crash(RuntimeError("x"), None) is None


def test_crash_message():
    exc = KeyError("sk-secret")
    assert diagnostics.crash_message(exc, Path("/h/logs/crash-1.log")) == (
        "Phil hit an unexpected error (KeyError). Details were saved to /h/logs/crash-1.log"
        " — please include it if you report this."
    )
    assert diagnostics.crash_message(exc, None) == "Phil hit an unexpected error (KeyError)."


def test_a_warning_raised_in_phil_code_fails_the_tests():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    options = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    assert "error:::phil" in options.get("filterwarnings", [])
