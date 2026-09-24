import typer

from phil import __version__

app = typer.Typer(add_completion=False, help="Phil: a contract-driven coding agent.")


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_print_version, is_eager=True, help="Show version and exit."
    ),
) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo("Chat mode is not implemented yet.")
