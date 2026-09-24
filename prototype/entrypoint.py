import typer
from rich import print

app = typer.Typer()

@app.command()
def task(goal: str):
    """Start a new project with Phil."""
    thread_id = generate_unique_id()
    print(f"[bold blue]Phil is analyzing the mission...[/bold blue]")
    
    # Run ONLY the architect node
    config = {"configurable": {"thread_id": thread_id}}
    phil.invoke({"objective": goal}, config)
    
    print("[green]Architect has a plan! Run `phil approve` to begin.[/green]")

@app.command()
def approve(thread_id: str):
    """Approve the plan and let the agents loose."""
    # Phil picks up where he left off in Supabase
    config = {"configurable": {"thread_id": thread_id}}
    phil.resume(config) 
    print("[bold yellow]Phil is now in the lab. I'll ping you when PRs are ready.[/bold yellow]")

if __name__ == "__main__":
    app()
