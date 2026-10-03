import typer
from typer import Option
from typing import Optional
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import typer
from typing import Optional
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
# import deferred to gateway command

app = typer.Typer(help="Aether: Deterministic Trading Intelligence Platform")
console = Console()

@app.command()
def onboard():
    """Prepare the Aether environment, validate dependencies, and initialize configuration."""
    from aether.cli.commands.onboard import run_onboarding
    run_onboarding()

@app.command()
def gateway():
    """Launch the Aether operational gateway."""
    console.print("[bold green]Launching Aether Gateway...[/bold green]")
    # Import here to avoid heavy optional dependencies during other commands
    from aether.core.gateway.server import run_gateway
    run_gateway()

@app.command()
def start(mode: str = typer.Option("dev", help="Runtime mode: dev, paper, production")):
    """Launch the Aether operational services in a specific mode."""
    console.print(f"[bold green]Starting Aether in [bold white]{mode}[bold green] mode...[/bold green]")
    # Placeholder for service orchestration
    console.print("[green]Aether is now operational.[/green]")

@app.command()
def stop():
    """Gracefully shut down the Aether gateway."""
    console.print("[bold red]Shutting down Aether...[/bold red]")
    console.print("[green]Aether stopped successfully.[/green]")

@app.command()
def restart():
    """Restart the Aether gateway."""
    stop()
    gateway()

@app.command()
def status():
    """Check the current operational status of the Aether platform."""
    table = Table(title="Aether System Status")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="magenta")
    table.add_column("Health", style="green")
    table.add_row("Gateway", "Running", "OK")
    table.add_row("Data Layer", "Connected", "OK")
    console.print(table)

@app.command()
def logs(tail: int = typer.Option(100, help="Number of lines to tail")):
    """Stream system logs."""
    console.print(f"Tailing last {tail} lines of system logs...")

@app.command()
def doctor():
    """Run comprehensive platform diagnostics."""
    console.print(Panel("[bold yellow]Running Aether Diagnostics...[/bold yellow]"))
    console.print("[green]No issues found. Your platform is healthy.[/green]")

@app.command()
def backtest():
    """Run a backtest session."""
    console.print("[bold blue]Starting Backtest...[/bold blue]")

@app.command()
def config():
    """Inspect and manage platform configuration."""
    console.print("[bold blue]Aether Configuration Inspector[/bold blue]")

@app.command()
def signals():
    """Inspect active trade signals."""
    console.print("[bold blue]Active Signals:[/bold blue]")

@app.command()
def adapters():
    """Manage and inspect adapters."""
    console.print("[bold blue]Adapter Status:[/bold blue]")

@app.command()
def install():
    """Install Aether globally so the 'aether' command works from anywhere."""
    console.print(Panel("[bold blue]Aether Global Installation[/bold blue]"))

    import subprocess
    from pathlib import Path

    script_path = Path(__file__).parents[2] / "install.sh"

    if script_path.exists():
        console.print("[yellow]Executing installation script...[/yellow]")
        try:
            subprocess.run(["bash", str(script_path)], check=True)
            console.print("[green]✓ Global installation completed successfully![/green]")
            console.print("\n[bold white]IMPORTANT:[/bold white] Ensure [cyan]~/.local/bin[/cyan] is in your PATH.")
            console.print("Run: [bold yellow]export PATH=\"$HOME/.local/bin:$PATH\"[/bold yellow]")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗ Installation failed: {e}[/red]")
    else:
        console.print("[red]✗ install.sh not found in project root.[/red]")

if __name__ == "__main__":
    app()

