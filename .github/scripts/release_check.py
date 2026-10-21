"""Release checks for .github/workflows/release.yml (spec 2026-10-18 §5). Stdlib only, so the
workflow can run it without installing Phil:

    uv run --no-project python .github/scripts/release_check.py <command> ...

Commands (paths are relative to the repository root):
  version                  print pyproject.toml's [project] version
  check-tag TAG            exit 1 unless TAG is "v" + that version
  dev-version RUN_NUMBER   print VERSION.devRUN_NUMBER, the version a TestPyPI rehearsal publishes
  set-version VERSION      rewrite pyproject.toml's [project] version (the CI checkout only)
  notes VERSION            print CHANGELOG.md's section for VERSION; exit 1 if it has none
  check-sdist PATH         exit 1 if the sdist is 2 MB or larger
  fetch-wheel JSON_URL DIR download the wheel a PyPI JSON-API release lists into DIR, checking
                           its sha256 and retrying while the index catches up; print its path
"""

import argparse
import hashlib
import json
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"
SDIST_LIMIT = 2 * 1024 * 1024

_VERSION_LINE = re.compile(r'^version = "[^"]*"$', re.MULTILINE)


def project_version(pyproject: str) -> str:
    return tomllib.loads(pyproject)["project"]["version"]


def tag_matches(tag: str, version: str) -> bool:
    return tag == f"v{version}"


def dev_version(version: str, run_number: int) -> str:
    return f"{version}.dev{run_number}"


def with_version(pyproject: str, version: str) -> str:
    """`pyproject` with the [project] table's `version` line replaced and every other line kept."""
    start = pyproject.index("[project]\n")
    end = pyproject.find("\n[", start)
    end = len(pyproject) if end == -1 else end
    table, count = _VERSION_LINE.subn(f'version = "{version}"', pyproject[start:end], count=1)
    if count != 1:
        raise ValueError("pyproject.toml's [project] table has no version line")
    return pyproject[:start] + table + pyproject[end:]


def changelog_section(changelog: str, version: str) -> str | None:
    """The body under `## [VERSION]`, up to the next `## ` heading or link reference. None when
    there's no such heading or its body is empty."""
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |^\[[^\]]+\]: |\Z)", re.MULTILINE | re.DOTALL
    )
    match = pattern.search(changelog)
    if match is None:
        return None
    return match.group(1).strip() or None


def sdist_ok(path: Path) -> bool:
    return path.stat().st_size < SDIST_LIMIT


def pick_wheel(release: dict) -> tuple[str, str]:
    """(url, sha256) of the pure-Python wheel in a PyPI JSON-API release document."""
    for entry in release.get("urls", []):
        if entry.get("packagetype") == "bdist_wheel" and entry["filename"].endswith("-py3-none-any.whl"):
            return entry["url"], entry["digests"]["sha256"]
    raise LookupError("the release lists no py3-none-any wheel")


def fetch_wheel(json_url: str, out_dir: Path, *, attempts: int = 20, delay: float = 15,
                urlopen=urllib.request.urlopen, sleep=time.sleep) -> Path:
    """Download the wheel `json_url` lists into `out_dir`. A 404, or a release with no wheel yet,
    is retried `attempts` times, `delay` seconds apart: an index takes a while to serve a new
    upload. A wheel whose sha256 differs from the listed one is an error, not retried."""
    for attempt in range(attempts):
        try:
            with urlopen(json_url, timeout=30) as response:
                url, sha256 = pick_wheel(json.load(response))
            break
        except (urllib.error.HTTPError, LookupError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code != 404:
                raise
            if attempt == attempts - 1:
                raise RuntimeError(f"{json_url} had no wheel after {attempts} attempts") from error
            sleep(delay)
    with urlopen(url, timeout=120) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != sha256:
        raise RuntimeError(f"the downloaded wheel's sha256 doesn't match the index's ({url})")
    path = out_dir / url.rsplit("/", 1)[-1]
    path.write_bytes(payload)
    return path


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release_check")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    commands.add_parser("check-tag").add_argument("tag")
    commands.add_parser("dev-version").add_argument("run_number", type=int)
    commands.add_parser("set-version").add_argument("version")
    commands.add_parser("notes").add_argument("version")
    commands.add_parser("check-sdist").add_argument("path", type=Path)
    fetch = commands.add_parser("fetch-wheel")
    fetch.add_argument("json_url")
    fetch.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)

    version = project_version(PYPROJECT.read_text(encoding="utf-8"))
    if args.command == "version":
        print(version)
    elif args.command == "check-tag":
        if not tag_matches(args.tag, version):
            return _fail(f"tag {args.tag} doesn't match pyproject.toml's version {version} (expected v{version})")
    elif args.command == "dev-version":
        print(dev_version(version, args.run_number))
    elif args.command == "set-version":
        PYPROJECT.write_text(with_version(PYPROJECT.read_text(encoding="utf-8"), args.version), encoding="utf-8")
    elif args.command == "notes":
        section = changelog_section(CHANGELOG.read_text(encoding="utf-8"), args.version)
        if section is None:
            return _fail(f"CHANGELOG.md has no section for {args.version}; add a '## [{args.version}]' heading")
        print(section)
    elif args.command == "check-sdist":
        if not sdist_ok(args.path):
            return _fail(f"{args.path.name} is {args.path.stat().st_size} bytes; the limit is {SDIST_LIMIT}")
    elif args.command == "fetch-wheel":
        print(fetch_wheel(args.json_url, args.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
