"""
Main interactive CLI for the Computer Assistant Agent System.
Run with:  python main.py
"""

import os
import sys
import threading as _threading

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
# JUDGE_MODEL     = os.getenv("JUDGE_MODEL",      "llama3.1:8b")  # JUDGE DISABLED

BANNER = """
╔══════════════════════════════════════════════════════╗
║         💻  Computer Assistant Agent System          ║
║  Storage · Battery · Health · Network · Startup      ║
║  Activity                                            ║
╚══════════════════════════════════════════════════════╝
"""

EXAMPLES = [
    "Why is my computer slow?",
    "What is eating up my disk space?",
    "Why is my battery draining so fast?",
    "Which apps are using the most memory?",
    "I need at least 50 GB free space — help me find it.",
    "My fan is very loud. What is happening?",
    "Why is my internet connection so slow?",
    "What programs start automatically when I log in?",
    "What have I been working on recently?",
    "Which files did I open last week?",
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


# JUDGE DISABLED — _show_judge_verdict function commented out
# def _show_judge_verdict(judged) -> None:
#     """Always render an LLM-as-a-Judge panel showing the full evaluation."""
#     verdict = judged.verdict
#     ar = judged.agent_result
#
#     _RISK_COLOR = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red", "CRITICAL": "bold red"}
#
#     if _RICH:
#         lines: list[str] = []
#         n = len(verdict.verdicts)
#         if n == 0:
#             lines.append("[dim]No suggestions to evaluate.[/dim]")
#         else:
#             lines.append("[bold]Suggestions[/bold]")
#             for v in verdict.verdicts:
#                 sug = ar.suggestions[v.index]
#                 orig_risk = ar.risk_levels[v.index]
#                 if v.approved:
#                     escalated = v.adjusted_risk and v.adjusted_risk != orig_risk
#                     if escalated:
#                         risk_display = (
#                             f"[{_RISK_COLOR.get(orig_risk, 'white')}]{orig_risk}[/{_RISK_COLOR.get(orig_risk, 'white')}]"
#                             f"[dim]→[/dim]"
#                             f"[{_RISK_COLOR.get(v.adjusted_risk, 'white')}]{v.adjusted_risk}[/{_RISK_COLOR.get(v.adjusted_risk, 'white')}]"
#                         )
#                     else:
#                         rc = _RISK_COLOR.get(orig_risk, "white")
#                         risk_display = f"[{rc}]{orig_risk}[/{rc}]"
#                     lines.append(f"  [green]✓[/green]  {risk_display:<30}  {sug}")
#                     if not v.factual and v.factuality_note:
#                         lines.append(f"     [yellow]⚠ Hallucination detected:[/yellow] [dim]{v.factuality_note}[/dim]")
#                 else:
#                     rc = _RISK_COLOR.get(v.adjusted_risk, "bold red")
#                     lines.append(f"  [red]✗[/red]  [red]BLOCKED[/red]  [{rc}]{v.adjusted_risk}[/{rc}]  [dim]{sug}[/dim]")
#                     if v.block_reason:
#                         lines.append(f"     [dim red]↳ {v.block_reason}[/dim red]")
#         lines.append("")
#         lines.append("[bold]Response checks[/bold]")
#         router_icon = "[green]✓[/green]" if verdict.router_domain_correct else "[yellow]⚠[/yellow]"
#         router_detail = (
#             f"[dim]{verdict.router_note}[/dim]"
#             if verdict.router_note and not verdict.router_domain_correct
#             else f"[dim]Correctly routed to {ar.agent}[/dim]"
#         )
#         lines.append(f"  {router_icon}  [bold]Router    [/bold]  {router_detail}")
#         rel_icon = "[green]✓[/green]" if verdict.response_relevant else "[yellow]⚠[/yellow]"
#         rel_detail = (
#             f"[dim]{verdict.relevance_note}[/dim]"
#             if verdict.relevance_note and not verdict.response_relevant
#             else "[dim]Response addresses the user's question[/dim]"
#         )
#         lines.append(f"  {rel_icon}  [bold]Relevance [/bold]  {rel_detail}")
#         qc = _RISK_COLOR.get(
#             {"GOOD": "LOW", "ACCEPTABLE": "MEDIUM", "POOR": "HIGH"}.get(verdict.overall_quality, "LOW"),
#             "green",
#         )
#         q_icon = "[green]✓[/green]" if verdict.overall_quality == "GOOD" else "[yellow]⚠[/yellow]"
#         q_detail = (
#             f"[dim]{verdict.quality_note}[/dim]"
#             if verdict.quality_note and verdict.overall_quality != "GOOD"
#             else ""
#         )
#         lines.append(
#             f"  {q_icon}  [bold]Quality   [/bold]  [{qc}]{verdict.overall_quality}[/{qc}]"
#             + (f"  {q_detail}" if q_detail else "")
#         )
#         console.print(Panel(
#             "\n".join(lines),
#             title=f"[bold blue]LLM-as-a-Judge[/bold blue] [dim]({verdict.judge_model})[/dim]",
#             border_style="blue",
#             padding=(1, 2),
#         ))
#     else:
#         sep = "-" * 60
#         print(f"\n{sep}")
#         print(f"LLM-AS-A-JUDGE  ({verdict.judge_model})")
#         print(sep)
#         n = len(verdict.verdicts)
#         if n == 0:
#             print("  No suggestions to evaluate.")
#         else:
#             print("  Suggestions:")
#             for v in verdict.verdicts:
#                 sug = ar.suggestions[v.index]
#                 orig_risk = ar.risk_levels[v.index]
#                 if v.approved:
#                     escalated = v.adjusted_risk and v.adjusted_risk != orig_risk
#                     risk_label = f"{orig_risk}→{v.adjusted_risk}" if escalated else orig_risk
#                     print(f"  ✓  [{risk_label}]  {sug}")
#                     if not v.factual and v.factuality_note:
#                         print(f"     ⚠ Hallucination: {v.factuality_note}")
#                 else:
#                     print(f"  ✗  BLOCKED [{v.adjusted_risk}]  {sug}")
#                     if v.block_reason:
#                         print(f"     ↳ {v.block_reason}")
#         print()
#         print("  Response checks:")
#         router_ok = "✓" if verdict.router_domain_correct else "⚠"
#         print(f"  {router_ok}  Router     {verdict.router_note or f'Correctly routed to {ar.agent}'}")
#         rel_ok = "✓" if verdict.response_relevant else "⚠"
#         print(f"  {rel_ok}  Relevance  {verdict.relevance_note or 'Response addresses the question'}")
#         q_ok = "✓" if verdict.overall_quality == "GOOD" else "⚠"
#         print(f"  {q_ok}  Quality    {verdict.overall_quality}" + (f"  {verdict.quality_note}" if verdict.quality_note and verdict.overall_quality != "GOOD" else ""))
#         print(sep)


def _show_and_run_actions(actions) -> None:
    """Display available actions and let the user run them interactively (multiple allowed)."""
    from src.actions import ActionExecutor, type_label

    _RISK_COLOR = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red", "CRITICAL": "bold red"}
    executor = ActionExecutor()

    while True:
        if _RICH:
            lines: list[str] = []
            for i, a in enumerate(actions, 1):
                rc = _RISK_COLOR.get(a.risk, "white")
                label = type_label(a.type)
                desc = (a.description[:60] + "…") if len(a.description) > 60 else a.description
                lines.append(
                    f"  [bold]{i}.[/bold]  [{rc}]{a.risk:<8}[/{rc}]  {label:<22}  [dim]{desc}[/dim]"
                )
            console.print(Panel(
                "\n".join(lines),
                title="[bold cyan]Executable Actions[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            ))
            choice = Prompt.ask(
                "[bold cyan]Run action[/bold cyan] [dim](number, or Enter to skip)[/dim]",
                default="",
            )
        else:
            print("\n--- Executable Actions ---")
            for i, a in enumerate(actions, 1):
                print(f"  [{i}]  {a.risk:<8}  {type_label(a.type):<22}  {a.description[:60]}")
            choice = input("Run action (number or Enter to skip): ").strip()

        if not choice:
            return

        try:
            idx = int(choice) - 1
        except ValueError:
            _print("Invalid choice.", "bold red")
            continue

        if idx < 0 or idx >= len(actions):
            _print("Invalid choice.", "bold red")
            continue

        selected = actions[idx]

        if selected.risk in ("HIGH", "CRITICAL"):
            rc = _RISK_COLOR.get(selected.risk, "red") if _RICH else ""
            if _RICH:
                confirm = Prompt.ask(
                    f"[{rc}]⚠  {selected.risk} risk — are you sure?[/{rc}]",
                    choices=["y", "n"],
                    default="n",
                )
            else:
                confirm = input(f"⚠  {selected.risk} risk — are you sure? [y/N]: ").strip().lower()
            if confirm != "y":
                _print("Cancelled.", "dim")
                continue

        ar = executor.execute(selected)
        label = type_label(selected.type)

        if _RICH:
            color = "green" if ar.success else "red"
            icon = "✓" if ar.success else "✗"
            content = ar.output if ar.success else (ar.error or "(no output)")
            console.print(Panel(
                content,
                title=f"[bold {color}]{icon}  {label}[/bold {color}]",
                border_style=color,
                padding=(1, 2),
            ))
        else:
            status = "✓" if ar.success else "✗"
            print(f"\n{status}  {label}")
            print(ar.output if ar.success else (ar.error or "(no output)"))


def _spinner_call(fn, *args, on_token=None, **kwargs):
    """Run fn with a spinner during collection; stream LLM tokens when they arrive."""
    if on_token is None:
        # Non-streaming fallback (e.g. multi-domain queries)
        if _RICH:
            with Live(Spinner("dots", text="[cyan]Analysing and auditing your system…[/cyan]"),
                      refresh_per_second=10, console=console):
                return fn(*args, **kwargs)
        else:
            print("Analysing your system, please wait…")
            return fn(*args, **kwargs)

    # Streaming path: spinner stops when first token arrives, then tokens print live.
    first_token_event = _threading.Event()

    def _wrapped_token(delta: str) -> None:
        if not first_token_event.is_set():
            first_token_event.set()
        on_token(delta)

    if _RICH:
        live = Live(
            Spinner("dots", text="[cyan]Analysing and auditing your system…[/cyan]"),
            refresh_per_second=10,
            console=console,
        )
        live.start()

        def _stop_spinner() -> None:
            first_token_event.wait(timeout=60)
            live.stop()
            console.print()  # newline after spinner clears

        _threading.Thread(target=_stop_spinner, daemon=True).start()
        result = fn(*args, on_token=_wrapped_token, **kwargs)
        live.stop()  # ensure stopped if streaming never started
        return result
    else:
        print("Analysing your system, please wait…")
        return fn(*args, on_token=_wrapped_token, **kwargs)


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
            _stream_buf: list[str] = []
            def _on_token(delta: str) -> None:
                _stream_buf.append(delta)
                if _RICH:
                    console.print(delta, end="", highlight=False)
                else:
                    print(delta, end="", flush=True)

            result = _spinner_call(handle, user_input, client, MODEL,
                                   verbose=False, on_token=_on_token)
            if _stream_buf:
                # Tokens were already printed live; show only raw data + actions.
                if _RICH:
                    console.print()  # final newline after streamed tokens
                else:
                    print()
            _show_raw_data(result.raw_data_summary, result.agent)
            if not _stream_buf:
                _show_answer(result.full_response)
            # _show_judge_verdict(result)  # JUDGE DISABLED
            _actions = result.actions
            if _actions:
                _show_and_run_actions(_actions)
            from src.history import save_entry
            save_entry(user_input, result)
        except Exception as exc:
            _print(f"\n⚠️  Error: {exc}", "bold red")
            _print("Make sure Ollama is running and the model is available.", "dim")

        _print("")  # blank line between turns


if __name__ == "__main__":
    main()
