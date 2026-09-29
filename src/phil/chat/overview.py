from pathlib import Path

from phil.git import git

README_NAMES = ("README.md", "README.rst", "README")


def repo_overview(root: Path, *, max_files: int = 200, readme_chars: int = 1500) -> str:
    tracked = [line for line in git(root, "ls-files").splitlines() if line]
    lines = ["Tracked files:", *tracked[:max_files]]
    if len(tracked) > max_files:
        lines.append(f"… and {len(tracked) - max_files} more files")
    for name in README_NAMES:
        readme = root / name
        if readme.is_file():
            lines += ["", f"{name} (start):", readme.read_text(errors="replace")[:readme_chars]]
            break
    return "\n".join(lines)
