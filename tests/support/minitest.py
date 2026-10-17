"""A stand-in for `pytest -q` in the offline tests that run Phil end to end.

Those tests run the sample repo's test command about seven times each. Real pytest takes about half a
second to start (much longer on Windows, where each command also starts through Git Bash), and Phil
reads only two kinds of line from the output, so this script prints just those, the way pytest does:

    FAILED tests/test_calc.py::test_sub
    1 failed, 2 passed in 0.01s

It runs every module-level `test*` function in `tests/**/test_*.py` under the current folder, with
plain `assert`s only (no fixtures, marks or parametrize). The exit code matches pytest's: 0 when all
pass, 1 when any fail, 5 when none ran. `tests/run/test_gates_tests.py` checks that the gates read
this output the same as real pytest's, and keeps using real pytest itself.
"""

import importlib.util
import sys
import time
import traceback
from pathlib import Path

sys.dont_write_bytecode = True  # don't leave __pycache__ in the worktree the run inspects


def main() -> int:
    start = time.monotonic()
    root = Path.cwd()
    sys.path.insert(0, str(root))
    passed = 0
    failures: list[str] = []
    for path in sorted(root.glob("tests/**/test_*.py")):
        relative = path.relative_to(root)
        try:
            name = ".".join(relative.with_suffix("").parts)
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except Exception:
            traceback.print_exc()
            failures.append(f"ERROR {relative.as_posix()}")
            continue
        for attr, value in sorted(vars(module).items()):
            if not (attr.startswith("test") and callable(value)):
                continue
            try:
                value()
            except Exception:
                traceback.print_exc()
                failures.append(f"FAILED {relative.as_posix()}::{attr}")
            else:
                passed += 1
    for line in failures:
        print(line)
    elapsed = time.monotonic() - start
    if not passed and not failures:
        print(f"no tests ran in {elapsed:.2f}s")
        return 5
    counts = ([f"{len(failures)} failed"] if failures else []) + [f"{passed} passed"]
    print(f"{', '.join(counts)} in {elapsed:.2f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
