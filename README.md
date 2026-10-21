<p align="center">
  <img src="https://raw.githubusercontent.com/ahochoy/phil/main/assets/banner.jpg" alt="Phil — The Friendly, Token-Efficient Development Agent" width="100%" />
</p>

# Phil

Named after *philia* (ancient Greek for friendship and mutual goodwill), Phil is a friendly
terminal coding agent built to be a token-efficient teammate. Run it inside a git repository:
it plans a goal with you, implements it in its own git worktree, and leaves a tested, reviewed
branch. It stops before it spends more than the budget you set.

## Install

Phil needs Python 3.14. [uv](https://docs.astral.sh/uv/) installs it for you if it's missing:

    uv tool install phil-agent

Or with [pipx](https://pipx.pypa.io/):

    pipx install phil-agent

Either way you get the `phil` command. To upgrade later, run `uv tool upgrade phil-agent` (or
`pipx upgrade phil-agent`).

**Windows:** Phil runs natively and needs [Git for Windows](https://git-scm.com/download/win),
which includes Git Bash. See the guide's
[Windows section](https://github.com/ahochoy/phil/blob/main/docs/guide.md#windows).

## Set up

    cd your-repo
    phil

The first time, setup starts automatically. It asks for a provider (OpenRouter, OpenAI,
Anthropic, Google, Ollama or any OpenAI-compatible endpoint), stores its API key in your OS key
store, picks a `high` and a `low` model and checks that each one answers. Run `phil setup` any
time to change them.

## Your first goal

In the chat, describe what you want:

    you › add a --verbose flag to the CLI that prints each step

Phil asks what it needs to know, shows you the plan, and runs it when you agree. When the run
finishes, look at the branch it left and open a pull request:

    phil diff <run-id>
    phil pr <run-id>

## Commands

| Command | What it does |
|---|---|
| `phil` | Open the chat in the current repository |
| `phil setup` | Choose a provider, store its key and pick your models |
| `phil runs` | List this repository's runs |
| `phil show <run>` | A run's tasks, usage and open issues |
| `phil attach <run>` | Follow a run and answer it when it pauses |
| `phil resume <run>` | Answer a paused run, or continue a stopped one |
| `phil stop <run>` | Stop a running run |
| `phil diff <run>` | The changes a run made |
| `phil pr <run>` | Push a finished run's branch and open its pull request |
| `phil clean <run>` | Remove a finished run's worktree and branch |
| `phil keys list` | Where each provider's key comes from |
| `phil models check` | Check the configured models |
| `phil config` | The effective settings, and where each came from |
| `phil permissions` | The rules for agents' shell commands |
| `phil memory` | What Phil remembers about this repository |

`phil --help` and `phil <command> --help` list every option.

## Learn more

- [User guide](https://github.com/ahochoy/phil/blob/main/docs/guide.md): configuration, routing,
  budgets, permissions, memory and every command in detail.
- [Changelog](https://github.com/ahochoy/phil/blob/main/CHANGELOG.md)
- [Contributing](https://github.com/ahochoy/phil/blob/main/CONTRIBUTING.md): development, tests
  and releases.
- [Issues](https://github.com/ahochoy/phil/issues)

## License

Apache-2.0. See [LICENSE](https://github.com/ahochoy/phil/blob/main/LICENSE).
