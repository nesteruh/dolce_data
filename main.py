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
MODEL           = os.getenv("AGENT_MODEL",  "llama3.2")
JUDGE_MODEL     = os.getenv("JUDGE_MODEL",  MODEL)

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


def _stars(score: int, total: int = 5) -> str:
    """Render a star rating string, e.g. '★★★☆☆'."""
    return "★" * score + "☆" * (total - score)


def _score_color(score: int) -> str:
    if score >= 4:
        return "green"
    if score == 3:
        return "yellow"
    return "red"


def _show_answer(answer: str) -> None:
    if _RICH:
        console.print(Panel(Markdown(answer), title="[bold green]Assistant[/bold green]",
                            border_style="green", padding=(1, 2)))
    else:
        print("\n" + "="*60)
        print(answer)
        print("="*60 + "\n")


def _show_blocked_answer(judged) -> None:
    """Render a red panel when the quality score blocks the answer."""
    verdict = judged.verdict
    score = verdict.score
    if _RICH:
        sc = _score_color(score)
        lines = [
            f"[bold red]Answer blocked by quality gate[/bold red]",
            f"  Score: [{sc}]{_stars(score)}  {score}/5[/{sc}]",
        ]
        if verdict.score_reason:
            lines.append(f"  [dim]{verdict.score_reason}[/dim]")
        lines.append("")
        lines.append("[dim]The response did not meet the minimum quality threshold (3/5).[/dim]")
        lines.append("[dim]Try rephrasing your question with more detail.[/dim]")
        console.print(Panel(
            "\n".join(lines),
            title="[bold red]Answer Blocked[/bold red]",
            border_style="red",
            padding=(1, 2),
        ))
    else:
        sep = "=" * 60
        print(f"\n{sep}")
        print(f"ANSWER BLOCKED  (score: {score}/5)")
        if verdict.score_reason:
            print(f"  {verdict.score_reason}")
        print("  Response did not meet the minimum quality threshold (3/5).")
        print(sep + "\n")


def _show_judge_verdict(judged) -> None:
    """Render the LLM-as-a-Judge panel with numeric scores and suggestion verdicts."""
    verdict = judged.verdict
    ar = judged.agent_result

    _RISK_COLOR = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red", "CRITICAL": "bold red"}

    if _RICH:
        lines: list[str] = []

        # ── Judge failure warning ─────────────────────────────────────────────
        if verdict.judge_failed:
            reason = f": {verdict.failure_reason}" if verdict.failure_reason else ""
            lines.append(f"[bold red]⚠  Judge evaluation failed{reason}.[/bold red]")
            lines.append("[red]  Suggestions have NOT been safety-verified.[/red]")
            lines.append("")

        # ── Quality score block ───────────────────────────────────────────────
        if verdict.score > 0:
            sc = _score_color(verdict.score)
            lines.append("[bold]Quality Score[/bold]")
            lines.append(f"  [{sc}]{_stars(verdict.score)}  {verdict.score}/5[/{sc}]")
            if verdict.score_grounding > 0:
                gc = _score_color(verdict.score_grounding)
                lines.append(f"  [dim]└ Grounding   [{gc}]{_stars(verdict.score_grounding)}  {verdict.score_grounding}/5[/{gc}][/dim]")
            if verdict.score_specificity > 0:
                spc = _score_color(verdict.score_specificity)
                lines.append(f"  [dim]└ Specificity [{spc}]{_stars(verdict.score_specificity)}  {verdict.score_specificity}/5[/{spc}][/dim]")
            if verdict.score_relevance > 0:
                rc2 = _score_color(verdict.score_relevance)
                lines.append(f"  [dim]└ Relevance   [{rc2}]{_stars(verdict.score_relevance)}  {verdict.score_relevance}/5[/{rc2}][/dim]")
            if verdict.score_reason:
                lines.append(f"  [dim italic]\"{verdict.score_reason}\"[/dim italic]")
        lines.append("")

        # ── Suggestions section ───────────────────────────────────────────────
        n = len(verdict.verdicts)
        if n == 0:
            lines.append("[dim]No suggestions to evaluate.[/dim]")
        else:
            lines.append("[bold]Suggestions[/bold]")
            for v in verdict.verdicts:
                sug = ar.suggestions[v.index]
                orig_risk = ar.risk_levels[v.index]

                if v.approved:
                    escalated = v.adjusted_risk and v.adjusted_risk != orig_risk
                    if escalated:
                        risk_display = (
                            f"[{_RISK_COLOR.get(orig_risk, 'white')}]{orig_risk}[/{_RISK_COLOR.get(orig_risk, 'white')}]"
                            f"[dim]→[/dim]"
                            f"[{_RISK_COLOR.get(v.adjusted_risk, 'white')}]{v.adjusted_risk}[/{_RISK_COLOR.get(v.adjusted_risk, 'white')}]"
                        )
                    else:
                        rc = _RISK_COLOR.get(orig_risk, "white")
                        risk_display = f"[{rc}]{orig_risk}[/{rc}]"
                    lines.append(f"  [green]✓[/green]  {risk_display:<30}  {sug}")
                    if not v.factual and v.factuality_note:
                        lines.append(f"     [yellow]⚠ Hallucination detected:[/yellow] [dim]{v.factuality_note}[/dim]")
                else:
                    rc = _RISK_COLOR.get(v.adjusted_risk, "bold red")
                    lines.append(f"  [red]✗[/red]  [red]BLOCKED[/red]  [{rc}]{v.adjusted_risk}[/{rc}]  [dim]{sug}[/dim]")
                    if v.block_reason:
                        lines.append(f"     [dim red]↳ {v.block_reason}[/dim red]")

        lines.append("")

        # ── Router check ──────────────────────────────────────────────────────
        lines.append("[bold]Response checks[/bold]")
        router_icon = "[green]✓[/green]" if verdict.router_domain_correct else "[yellow]⚠[/yellow]"
        router_detail = (
            f"[dim]{verdict.router_note}[/dim]"
            if verdict.router_note and not verdict.router_domain_correct
            else f"[dim]Correctly routed to {ar.agent}[/dim]"
        )
        lines.append(f"  {router_icon}  [bold]Router[/bold]  {router_detail}")

        console.print(Panel(
            "\n".join(lines),
            title=f"[bold blue]LLM-as-a-Judge[/bold blue] [dim]({verdict.judge_model})[/dim]",
            border_style="blue",
            padding=(1, 2),
        ))

    else:
        # ── Plain-text fallback ───────────────────────────────────────────────
        sep = "-" * 60
        print(f"\n{sep}")
        print(f"LLM-AS-A-JUDGE  ({verdict.judge_model})")
        print(sep)

        if verdict.judge_failed:
            reason = f": {verdict.failure_reason}" if verdict.failure_reason else ""
            print(f"  ⚠  Judge evaluation failed{reason}.")
            print("     Suggestions have NOT been safety-verified.")
            print()

        if verdict.score > 0:
            print(f"  Quality  {verdict.score}/5  {_stars(verdict.score)}")
            if verdict.score_grounding:
                print(f"    Grounding:   {verdict.score_grounding}/5  Specificity: {verdict.score_specificity}/5  Relevance: {verdict.score_relevance}/5")
            if verdict.score_reason:
                print(f"    \"{verdict.score_reason}\"")
            print()

        n = len(verdict.verdicts)
        if n == 0:
            print("  No suggestions to evaluate.")
        else:
            print("  Suggestions:")
            for v in verdict.verdicts:
                sug = ar.suggestions[v.index]
                orig_risk = ar.risk_levels[v.index]
                if v.approved:
                    escalated = v.adjusted_risk and v.adjusted_risk != orig_risk
                    risk_label = f"{orig_risk}→{v.adjusted_risk}" if escalated else orig_risk
                    print(f"  ✓  [{risk_label}]  {sug}")
                    if not v.factual and v.factuality_note:
                        print(f"     ⚠ Hallucination: {v.factuality_note}")
                else:
                    print(f"  ✗  BLOCKED [{v.adjusted_risk}]  {sug}")
                    if v.block_reason:
                        print(f"     ↳ {v.block_reason}")

        print()
        router_ok = "✓" if verdict.router_domain_correct else "⚠"
        print(f"  {router_ok}  Router  {verdict.router_note or f'Correctly routed to {ar.agent}'}")
        print(sep)


def _spinner_call(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) with a spinner if Rich is available."""
    if _RICH:
        with Live(Spinner("dots", text="[cyan]Analysing and auditing your system…[/cyan]"),
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

    _MAX_HISTORY_TURNS = 6  # keep last 3 exchanges (6 messages: 3 user + 3 assistant)
    conversation_history: list[dict] = []

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
            result = _spinner_call(
                handle, user_input, client, MODEL,
                judge_model=JUDGE_MODEL, verbose=False,
                history=conversation_history[-_MAX_HISTORY_TURNS:] or None,
            )
            _show_raw_data(result.agent_result.raw_data_summary, result.agent_result.agent)
            if result.is_answer_blocked:
                _show_blocked_answer(result)
            else:
                _show_answer(result.agent_result.full_response)
                # Only record successful (non-blocked) turns in session memory
                conversation_history.append({"role": "user", "content": user_input})
                conversation_history.append({"role": "assistant", "content": result.agent_result.analysis})
            _show_judge_verdict(result)
            from src.history import save_entry
            save_entry(user_input, result)
        except Exception as exc:
            _print(f"\n⚠️  Error: {exc}", "bold red")
            _print("Make sure Ollama is running and the model is available.", "dim")

        _print("")  # blank line between turns


if __name__ == "__main__":
    main()
