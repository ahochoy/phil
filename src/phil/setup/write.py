"""Edit `~/.phil/config.toml` in place with `tomlkit`, keeping every other setting and comment."""

import contextlib
import os
import stat
import tempfile
from pathlib import Path

import tomlkit

HEADER = "# Phil settings (written by phil setup; edit freely)"
# The only fields setup writes under [providers.<name>]. Keys never go in the file, only the
# name of the variable that holds one.
PROVIDER_FIELDS = ("kind", "base_url", "api_key_env", "input_per_mtok", "output_per_mtok")


def _table(parent, name: str, *, super_table: bool = False):
    """`parent[name]`, created as a table if missing (a super table renders no header of its
    own, only its sub-tables': `[providers.lab]`, not `[providers]` then `[providers.lab]`)."""
    if name not in parent:
        parent[name] = tomlkit.table(is_super_table=super_table)
    return parent[name]


def write_global_config(
    path: Path,
    *,
    models: dict[str, str],
    provider_name: str | None,
    provider_fields: dict[str, object],
) -> None:
    """Set `models.<tier>` for each of `models`, and `providers.<provider_name>.<field>` for each
    of `provider_fields` (a None value removes that field), leaving the rest of the file as it is.

    The file and its directory are created when missing; the write is atomic (a temp file in the
    same directory, then `os.replace`). Raises ValueError for a field outside `PROVIDER_FIELDS`."""
    unknown = sorted(set(provider_fields) - set(PROVIDER_FIELDS))
    if unknown:
        raise ValueError(f"setup can't write provider field(s) {unknown}; allowed: {list(PROVIDER_FIELDS)}")
    if path.is_file():
        document = tomlkit.parse(path.read_text())
    else:
        document = tomlkit.document()
        document.add(tomlkit.comment(HEADER.removeprefix("# ")))
        document.add(tomlkit.nl())
    models_table = _table(document, "models")
    for tier, model in models.items():
        models_table[tier] = model
    if provider_name is not None and provider_fields:
        entry = _table(_table(document, "providers", super_table=True), provider_name)
        for field, value in provider_fields.items():
            if value is None:
                entry.pop(field, None)
            else:
                entry[field] = value
    _write_atomically(path, tomlkit.dumps(document))


def _write_atomically(path: Path, text: str) -> None:
    """Replace `path`'s content with `text` atomically. A symlink is followed, so its target is
    rewritten and the link kept; an existing file keeps its permission mode (a new one is 0o600)."""
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        mode = 0o600
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".config-", suffix=".toml")
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
