"""
Main interactive CLI for the Computer Assistant Agent System.
Run with:  python main.py
"""

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ── rich is optional — fall back to plain print if not installed ──
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.markdown import Markdown
    from rich.rule import Rule
    from rich.spinner import Spinner
    from rich.live import Live
    _RICH = True
    console = Console()
except ImportError:
    _RICH = False
    console = None  # type: ignore


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_API_KEY  = os.getenv("OLLAMA_API_KEY",  "ollama")
MODEL           = os.getenv("AGENT_MODEL",      "llama3.2")

BANNER = """
╔══════════════════════════════════════════════════════╗
║         💻  Computer Assistant Agent System          ║
║   Storage · Battery · CPU/GPU/RAM Health Advisor     ║
╚══════════════════════════════════════════════════════╝
"""

EXAMPLES = [
    "Why is my computer slow?",
    "What is eating up my disk space?",
    "Why is my battery draining so fast?",
    "Which apps are using the most memory?",
    "I need at least 50 GB free space — help me find it.",
    "My fan is very loud. What is happening?",
]

QUIT_COMMANDS = {"quit", "exit", "q", "bye"}


def _print(text: str, style: str = "") -> None:
    if _RICH:
        console.print(text, style=style)
    else:
        print(text)


def _ask(prompt: str) -> str:
    if _RICH:
        return Prompt.ask(f"[bold cyan]{prompt}[/bold cyan]")
    return input(f"{prompt}: ")


def _show_raw_data(raw: str, agent: str) -> None:
    if _RICH:
        console.print(Panel(
            raw,
            title=f"[bold yellow]Raw Terminal Data — {agent}[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        ))
    else:
        print("\n" + "-"*60)
        print(f"RAW DATA ({agent}):")
        print(raw)
        print("-"*60 + "\n")


def _show_answer(answer: str) -> None:
    if _RICH:
        console.print(Panel(Markdown(answer), title="[bold green]Assistant[/bold green]",
                            border_style="green", padding=(1, 2)))
    else:
        print("\n" + "="*60)
        print(answer)
        print("="*60 + "\n")


def _spinner_call(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) with a spinner if Rich is available."""
    if _RICH:
        with Live(Spinner("dots", text="[cyan]Analysing your system…[/cyan]"),
                  refresh_per_second=10, console=console):
            return fn(*args, **kwargs)
    else:
        print("Analysing your system, please wait…")
        return fn(*args, **kwargs)


def main() -> None:
    # Lazy import here so the CLI prints the banner before any heavy work
    from src.router import handle

    client = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)

    if _RICH:
        console.print(BANNER, style="bold magenta")
        console.print(Rule(style="dim"))
        console.print("Example questions you can ask:", style="dim")
        for ex in EXAMPLES:
            console.print(f"  • {ex}", style="italic dim")
        console.print(Rule(style="dim"))
        console.print('Type [bold]quit[/bold] to exit.\n')
    else:
        print(BANNER)
        print("Example questions:")
        for ex in EXAMPLES:
            print(f"  • {ex}")
        print("\nType 'quit' to exit.\n")

    while True:
        try:
            user_input = _ask("You").strip()
        except (EOFError, KeyboardInterrupt):
            _print("\nGoodbye! 👋", "bold yellow")
            break

        if not user_input:
            continue

        if user_input.lower() in QUIT_COMMANDS:
            _print("Goodbye! 👋", "bold yellow")
            break

        try:
            result = _spinner_call(handle, user_input, client, MODEL, verbose=False)
            _show_raw_data(result.raw_data_summary, result.agent)
            _show_answer(result.full_response)
        except Exception as exc:
            _print(f"\n⚠️  Error: {exc}", "bold red")
            _print("Make sure Ollama is running and the model is available.", "dim")

        _print("")  # blank line between turns


if __name__ == "__main__":
    main()
