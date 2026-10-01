import pytest

from phil.config import load_config
from phil.setup.write import HEADER, write_global_config

SECRET = "sk-TESTSECRET-123"


def test_a_new_file_loads_with_load_config(tmp_path, phil_home):
    path = phil_home / "config.toml"
    assert not phil_home.exists()
    write_global_config(
        path,
        models={"high": "openrouter:anthropic/claude-sonnet-5", "low": "openrouter:google/gemini-3.8-flash"},
        provider_name=None,
        provider_fields={},
    )
    text = path.read_text()
    assert text.startswith(HEADER)
    config = load_config(tmp_path)
    assert config.models == {
        "high": "openrouter:anthropic/claude-sonnet-5",
        "low": "openrouter:google/gemini-3.8-flash",
    }
    assert config.sources["models.high"] == str(path)
    assert "providers" not in text


def test_a_custom_provider_gets_its_own_table(tmp_path, phil_home):
    path = phil_home / "config.toml"
    write_global_config(
        path,
        models={"high": "lab:big", "low": "lab:small"},
        provider_name="lab",
        provider_fields={"kind": "openai", "base_url": "http://lab:8000/v1", "api_key_env": "LAB_API_KEY"},
    )
    config = load_config(tmp_path)
    entry = config.providers["lab"]
    assert (entry.kind, entry.base_url, entry.api_key_env) == ("openai", "http://lab:8000/v1", "LAB_API_KEY")


def test_an_existing_file_keeps_its_comments_and_other_settings(tmp_path, phil_home):
    path = phil_home / "config.toml"
    phil_home.mkdir()
    path.write_text(
        "# my own notes\n"
        "[models]\n"
        'high = "openai:old"  # the old one\n'
        'critic = "openai:judge"\n'
        "\n"
        "[run]\n"
        "max_cost_usd = 5.0  # keep this\n"
        "\n"
        "[shell]\n"
        '# extra commands\nallow = ["make test"]\n'
        "\n"
        "[providers.lab]\n"
        'kind = "openai"\n'
        'base_url = "http://old/v1"\n'
        "input_per_mtok = 1.5\n"
        'api_key_env = "LAB_API_KEY"\n'
    )
    write_global_config(
        path,
        models={"high": "lab:big", "low": "lab:small"},
        provider_name="lab",
        provider_fields={"base_url": "http://lab:8000/v1", "api_key_env": None},
    )
    text = path.read_text()
    for kept in ("# my own notes", "# keep this", "# extra commands", 'allow = ["make test"]', 'critic = "openai:judge"'):
        assert kept in text
    assert not text.startswith(HEADER)
    config = load_config(tmp_path)
    assert config.models == {"high": "lab:big", "low": "lab:small", "critic": "openai:judge"}
    assert config.run.max_cost_usd == 5.0
    assert config.shell.allow == ["make test"]
    entry = config.providers["lab"]
    assert entry.base_url == "http://lab:8000/v1"
    assert entry.input_per_mtok == 1.5
    assert entry.api_key_env is None  # a None field is removed


def test_only_known_provider_fields_are_written_never_a_key(phil_home):
    path = phil_home / "config.toml"
    for field in ("api_key", "key", "token", "password"):
        try:
            write_global_config(path, models={"high": "a:b"}, provider_name="lab", provider_fields={field: SECRET})
        except ValueError as exc:
            assert SECRET not in str(exc)
        else:
            raise AssertionError(f"{field} was accepted")
    assert not path.exists()


def test_a_written_file_never_contains_a_key_value(phil_home, monkeypatch):
    monkeypatch.setenv("LAB_API_KEY", SECRET)
    path = phil_home / "config.toml"
    write_global_config(
        path,
        models={"high": "lab:big", "low": "lab:small"},
        provider_name="lab",
        provider_fields={"kind": "openai", "base_url": "http://lab/v1", "api_key_env": "LAB_API_KEY"},
    )
    assert SECRET not in path.read_text()


def test_the_write_is_atomic(phil_home, monkeypatch):
    import os

    path = phil_home / "config.toml"
    phil_home.mkdir()
    path.write_text('[models]\nhigh = "openai:old"\n')

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        write_global_config(path, models={"high": "openai:new"}, provider_name=None, provider_fields={})
    assert path.read_text() == '[models]\nhigh = "openai:old"\n'
    assert [p.name for p in phil_home.iterdir()] == ["config.toml"]  # no temp file left behind


def test_the_write_is_flushed_to_disk_before_the_replace(phil_home, monkeypatch):
    import os

    events = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda src, dst: (events.append("replace"), real_replace(src, dst))[1])
    write_global_config(phil_home / "config.toml", models={"high": "a:b"}, provider_name=None, provider_fields={})
    assert events == ["fsync", "replace"]


def test_a_symlinked_config_is_written_through_to_its_target(tmp_path, phil_home):
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "phil.toml"
    target.write_text("# tracked in my dotfiles\n")
    phil_home.mkdir()
    link = phil_home / "config.toml"
    link.symlink_to(target)
    write_global_config(link, models={"high": "a:b", "low": "a:c"}, provider_name=None, provider_fields={})
    assert link.is_symlink()
    assert target.read_text().startswith("# tracked in my dotfiles")
    assert 'high = "a:b"' in target.read_text()


def test_the_file_keeps_its_permission_mode(phil_home):
    import stat

    phil_home.mkdir()
    path = phil_home / "config.toml"
    path.write_text("[run]\n")
    path.chmod(0o640)
    write_global_config(path, models={"high": "a:b"}, provider_name=None, provider_fields={})
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_a_new_file_is_private(phil_home):
    import stat

    path = phil_home / "config.toml"
    write_global_config(path, models={"high": "a:b"}, provider_name=None, provider_fields={})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
