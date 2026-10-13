import pytest

from phil.permissions.catalog import KNOWN_PROGRAMS, catalog_reads, label_for

READS = [
    "gh pr view 42", "gh pr list", "gh pr checks 42 --watch", "gh pr diff 42", "gh pr status",
    "gh issue view 7", "gh issue list", "gh repo view", "gh run view 1", "gh run list",
    "gh release view v1", "gh release list", "gh auth status", "gh --version",
    "git remote", "git remote -v", "git rev-parse HEAD", "git blame src/x.py", "git tag -l", "git tag --list v*",
    "git stash list", "git config --get user.name", "git config --list",
    "npm ls", "npm view left-pad", "npm outdated", "npm why x", "pnpm ls", "yarn why x",
    "uv pip list", "uv pip show x", "uv python list", "uv --version",
    "cargo version", "go list ./...", "go version", "go env GOPATH",
    # R10: plain arguments and the entry's own safe flags
    "gh pr list --limit 5", "gh pr list --json title,url", "gh pr list --json=title -q .[0]",
    "gh pr view 1 --comments --repo o/r", "gh run view 1 --log-failed", "gh auth status --hostname github.com",
    "git blame -L 1,5 src/x.py", "git tag --sort=-creatordate -l", "git remote --verbose",
    "git rev-parse --abbrev-ref HEAD", "git rev-parse --show-toplevel", "git config --global --get user.name",
    "git stash list", "npm ls --json --depth=0", "npm view left-pad --json", "pnpm ls --depth 1 --json",
    "pnpm why x", "pnpm outdated -r", "yarn why x", "uv pip list --format=json", "uv pip freeze --strict",
    "go list -json -m all", "go list -f {{.Dir}} ./...", "go version -m", "go env -json", "cargo --version",
    "git --help", "git -h",
]
WRITES = [
    "gh pr merge 42", "gh pr create", "gh pr close 1", "gh pr edit 1", "gh api repos/x/y", "gh api -X GET x",
    "gh repo delete x", "gh release create v1",
    "git push", "git commit -m x", "git reset --hard", "git checkout main", "git tag v1", "git tag -d v1",
    "git remote add o url", "git config --add a.b c", "git config user.name x", "git stash",
    "npm install x", "npm run build", "npm publish", "uv add x", "uv sync", "uv run x", "uv pip install x",
    "cargo build", "cargo install x", "go get x", "go env -w GOPATH=/x",
    # R3: a path in argv[0] is never taken for the bare program
    "./gh pr view 1", "/tmp/gh pr view 1", "sub/git log", "/opt/homebrew/bin/gh pr view 1",
    # a read that prints a secret
    "gh auth status --show-token", "gh auth status -t", "gh auth status --show", "gh auth status -ht x",
    # R10: an option off the entry's safe list, or `--`, is not a read
    "git stash list --output=x", "git stash list -p", "git stash list x", "git stash list --output x",
    "uv pip list --python ./x", "uv pip show --python=./x y", "uv pip freeze -p ./x",
    "pnpm explain x", "pnpm view x", "pnpm info x", "yarn info x", "yarn list", "yarn ls", "yarn view x",
    "go env --w X=1", "go env -w X=1", "go env -u X", "go list -toolexec x .", "go list -mod=mod",
    "go list -export ./...", "go list -overlay=x ./...",
    "cargo metadata", "cargo tree", "uv tree", "cargo version --config x", "cargo --config x version",
    "gh pr view 1 -- x", "gh pr list --unknown", "git blame --contents x f",
    "git rev-parse --git-path x", "git tag -l -d v1", "git tag --contains", "git tag x", "git config --file x --get y",
    "git config x", "git config --get x --unset y", "git remote show o", "git remote -v add o u",
    "npm view x --registry=http://h", "npm ls --prefix x", "pnpm ls --config x",
    "gh pr list -", "gh pr list --",
]


@pytest.mark.parametrize("command", READS)
def test_reads(command):
    assert catalog_reads(command.split())


@pytest.mark.parametrize("command", WRITES)
def test_writes_are_not_reads(command):
    assert not catalog_reads(command.split())


def test_full_path_program_is_not_a_read():
    assert not catalog_reads(["/opt/homebrew/bin/gh", "pr", "view", "1"])


def test_non_ascii_program_is_not_a_read():
    assert not catalog_reads(["ɡh", "pr", "view", "1"])


def test_empty_argv_is_not_a_read():
    assert not catalog_reads([])


def test_known_programs_cover_the_catalog():
    assert {"gh", "git", "npm", "docker", "kubectl"} <= KNOWN_PROGRAMS


@pytest.mark.parametrize("command,label", [
    ("gh pr view 1", "reads only"), ("gh pr merge 1", "changes things"), ("npm install x", "changes things"),
    ("rm build.log", "changes things"), ("python3 x.py", "changes things"),
    ("make test", "may change things"), ("terraform plan", "may change things"),
])
def test_labels(command, label):
    argv = command.split()
    assert label_for(argv, read_only=catalog_reads(argv)) == label


def test_label_for_empty_argv():
    assert label_for([], read_only=False) == "may change things"
    assert label_for([], read_only=True) == "reads only"


@pytest.mark.parametrize("command", ["./gh pr view 1", "/tmp/git log"])
def test_label_for_path_qualified_program(command):
    argv = command.split()
    assert not catalog_reads(argv)
    assert label_for(argv, read_only=False) == "changes things"


def test_label_for_existing_read_only_list():
    assert label_for(["ls", "-la"], read_only=True) == "reads only"
