"""
Router agent.
Classifies the user prompt and dispatches to one or more specialist agents.
When a prompt spans multiple domains (e.g. "slow AND battery dying"), all
relevant agents run in parallel and their results are merged into one response.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
# import os  # JUDGE DISABLED (was used only for JUDGE_MODEL env var)

from openai import OpenAI

from src.agents import (
    AgentResult,
    run_storage_agent,
    run_battery_agent,
    run_health_agent,
    run_network_agent,
    run_startup_agent,
    run_activity_agent,
    run_file_agent,
    run_system_agent,
)
from src.collectors import detect_os
# from src.judge import JudgedResult, run_judge  # JUDGE DISABLED


# ─────────────────────────────────────────────────────────────────────────────
# Domain keyword mapping
# ─────────────────────────────────────────────────────────────────────────────

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "storage": [
        "disk", "storage", "space", "free space", "gb", "tb", "cache",
        "temp", "large file", "download", "trash", "full", "clean", "wipe",
        "no space", "running out", "file size", "stale", "old file",
    ],
    "battery": [
        "battery", "charge", "drain", "power", "charging", "unplugged",
        "battery health", "cycle", "watt", "energy", "low battery", "dies fast",
        "discharge", "plugged",
    ],
    "health": [
        "cpu", "gpu", "ram", "memory", "processor", "slow", "lag", "freeze",
        "hang", "performance", "resource", "process", "swap",
        "fan", "hot", "thermal", "speed", "fast", "responsive",
    ],
    "network": [
        "network", "internet", "connection", "bandwidth", "wifi", "wi-fi",
        "ethernet", "firewall", "vpn", "dns", "port", "latency", "online",
        "downloading", "uploading", "ip address", "proxy", "slow internet",
        "no internet", "connected", "packet", "ping", "slow connection",
        "network slow", "internet slow",
    ],
    "startup": [
        "startup", "boot", "login", "login item", "autostart", "launch agent",
        "launch daemon", "background service", "service", "slow boot",
        "starts automatically", "runs on startup", "disable on startup",
        "startup program", "slow login",
    ],
    "activity": [
        "recently opened", "recent files", "last opened", "what did i",
        "what have i", "i've been", "my history", "file history",
        "opened recently", "used recently", "command history", "shell history",
        "apps i use", "frequently used", "usage history", "recent activity",
        "what was i working on", "last week", "last month", "recently",
        "what i opened", "have i used", "did i open", "what i've been",
        "activity", "frequent", "my usage", "what i've done", "what i did",
        "app usage", "usage", "how i use", "what apps", "which apps",
    ],
    "file": [
        "create file", "make file", "new file", "create folder", "make folder",
        "new folder", "make directory", "copy file", "move file", "rename file",
        "rename folder", "delete file", "remove file", "on desktop",
        "on my desktop", "in downloads", "in documents", "save file",
        "write file", "list folder", "list directory", "open file", "show file",
        "find file", "zip file", "compress file", "extract file", "unzip",
        "archive", "duplicate", "set permissions", "chmod",
    ],
    "system": [
        "set volume", "volume to", "set brightness", "brightness to",
        "dark mode", "light mode", "lock screen", "sleep now",
        "turn off wifi", "turn on wifi", "toggle wifi", "wi-fi off", "wi-fi on",
        "bluetooth", "do not disturb", "dnd", "empty trash", "send notification",
        "sleep timer", "toggle bluetooth", "disconnect wifi",
        "mute", "unmute", "volume up", "volume down", "turn off screen",
        "enable wifi", "disable wifi", "enable bluetooth", "disable bluetooth",
        "night mode", "night shift", "focus mode", "airplane mode",
    ],
}

_AGENT_EMOJI: dict[str, str] = {
    "StorageAgent": "🗄️",
    "BatteryAgent": "🔋",
    "HealthAgent":  "💻",
    "NetworkAgent": "🌐",
    "StartupAgent": "🚀",
    "ActivityAgent":"🔃",
    "FileAgent":    "📂",
    "SystemAgent":  "⚙️",
}


# ─────────────────────────────────────────────────────────────────────────────
# Action-intent detection (catches "wifi off", "disable bluetooth", etc.)
# ─────────────────────────────────────────────────────────────────────────────

_ACTION_VERBS = {
    "turn off", "turn on", "switch off", "switch on", "shut off",
    "enable", "disable", "set", "toggle", "mute", "unmute",
    "lock", "sleep", "empty", "send",
}
_SYSTEM_CONTROLLABLES = {
    "wifi", "wi-fi", "bluetooth", "volume", "brightness",
    "screen", "display", "dark mode", "light mode",
    "do not disturb", "dnd", "trash", "notification",
}

# Verbs that indicate a user wants to kill/quit a specific process or app
_KILL_VERBS = {
    "kill", "force quit", "force-quit", "force_quit",
    "terminate", "quit app", "quit application", "close app",
    "close application", "end process", "kill process",
    "kill the", "quit the", "close the",
}


def _has_system_action_intent(lower: str) -> bool:
    """Return True when the prompt looks like a system-settings action command."""
    for verb in _ACTION_VERBS:
        if verb not in lower:
            continue
        for noun in _SYSTEM_CONTROLLABLES:
            if noun in lower:
                return True
    return False


def _has_kill_intent(lower: str) -> bool:
    """Return True when the prompt is asking to kill/quit a specific process or app."""
    return any(kw in lower for kw in _KILL_VERBS)


# ─────────────────────────────────────────────────────────────────────────────
# Multi-domain classifier
# ─────────────────────────────────────────────────────────────────────────────

def _classify(prompt: str) -> list[str]:
    """
    Return an ordered list of matched domains.

    file and system are action-only agents — they return exclusively and never
    run alongside diagnostic agents. For all other domains, return the top 2
    by score (falls back to ["health"] when nothing matches).
    """
    lower = prompt.lower()
    scores: dict[str, int] = {domain: 0 for domain in _DOMAIN_KEYWORDS}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                scores[domain] += 1

    if scores.get("file", 0) >= 1:
        return ["file"]
    if scores.get("system", 0) >= 1 or _has_system_action_intent(lower) or _has_kill_intent(lower):
        return ["system"]

    matched = [d for d, s in scores.items() if s >= 1 and d not in ("file", "system")]
    if not matched:
        return ["health"]

    matched.sort(key=lambda d: scores[d], reverse=True)
    return matched[:2]


# ─────────────────────────────────────────────────────────────────────────────
# Multi-agent result merger
# ─────────────────────────────────────────────────────────────────────────────

def _merge_results(results: list[AgentResult]) -> AgentResult:
    """Combine multiple AgentResults into one for multi-domain queries."""
    if len(results) == 1:
        return results[0]

    agent_line = " · ".join(
        f"{_AGENT_EMOJI.get(r.agent, '🤖')} {r.agent}" for r in results
    )

    # Merge raw data summaries under per-agent headers
    raw_parts = []
    for r in results:
        raw_parts.append(f"=== {r.agent.upper()} DATA ===\n{r.raw_data_summary}")
    merged_raw = "\n\n".join(raw_parts)

    # Merge formatted responses under per-agent section headers
    response_parts = [f"*Analysed by: {agent_line}*"]
    for r in results:
        emoji = _AGENT_EMOJI.get(r.agent, "🤖")
        response_parts.append(f"---\n\n### {emoji} {r.agent}\n\n{r.full_response}")
    merged_response = "\n\n".join(response_parts)

    # Merge suggestions, risk levels, and actions from all agents
    all_suggestions = [s for r in results for s in r.suggestions]
    all_risks = [rv for r in results for rv in r.risk_levels]
    all_actions = [a for r in results for a in r.actions]

    combined_agent_name = " + ".join(r.agent for r in results)

    return AgentResult(
        agent=combined_agent_name,
        raw_data_summary=merged_raw,
        analysis=merged_response,
        suggestions=all_suggestions,
        risk_levels=all_risks,
        full_response=merged_response,
        actions=all_actions,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

_DISPATCH: dict[str, object] = {
    "storage": run_storage_agent,
    "battery": run_battery_agent,
    "health":  run_health_agent,
    "network": run_network_agent,
    "startup": run_startup_agent,
    "activity":run_activity_agent,
    "file":    run_file_agent,
    "system":  run_system_agent,
}


def handle(
    user_prompt: str,
    client: OpenAI,
    model: str = "llama3.2",
    # judge_model: str | None = None,  # JUDGE DISABLED
    verbose: bool = False,
    on_token=None,
) -> AgentResult:
    """
    Main pipeline:
      1. Detect OS
      2. Classify prompt → domain
      3. Run specialist agent (collects data + LLM analysis + formatting)
      4. Return AgentResult
      # 4. Run judge layer (parallel per-suggestion evaluation + holistic check)  # JUDGE DISABLED
      # 5. Return JudgedResult wrapping AgentResult + JudgeVerdict  # JUDGE DISABLED
    """
    os_name = detect_os()
    domains = _classify(user_prompt)

    if verbose:
        tag = "Domain" if len(domains) == 1 else "Domains"
        print(f"\n[Router] OS={os_name} | {tag}={domains}\n")

    # ── Single domain: simple path, no threading overhead ──────────────────
    if len(domains) == 1:
        runner = _DISPATCH[domains[0]]
        # on_token enables streaming; only usable in single-domain (can't stream two agents at once)
        result: AgentResult = runner(user_prompt, client, model, os_name, on_token=on_token)
        if verbose:
            print(f"[{result.agent}] Suggestions: {len(result.suggestions)}\n")
        # _judge_model = judge_model or os.getenv("JUDGE_MODEL", model)  # JUDGE DISABLED
        # return run_judge(result, user_prompt, domains[0], client, _judge_model)  # JUDGE DISABLED
        return result

    # ── Multi-domain: run agents in parallel ────────────────────────────────
    if verbose:
        print(f"[Router] Dispatching to {len(domains)} agents in parallel…\n")

    results: list[AgentResult] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=len(domains)) as executor:
        future_to_domain = {
            executor.submit(_DISPATCH[domain], user_prompt, client, model, os_name): domain
            for domain in domains
        }
        for future in as_completed(future_to_domain):
            domain = future_to_domain[future]
            try:
                result = future.result()
                results.append(result)
                if verbose:
                    print(f"[{result.agent}] done — {len(result.suggestions)} suggestion(s)")
            except Exception as exc:
                errors.append(f"{domain}: {exc}")
                if verbose:
                    print(f"[Router] {domain} agent failed: {exc}")

    if not results:
        # from src.judge import JudgeVerdict  # JUDGE DISABLED
        _empty = AgentResult(
            agent="Router",
            raw_data_summary="",
            analysis="All agents failed to collect data.",
            suggestions=[],
            risk_levels=[],
            full_response="⚠️ All agents failed to collect data. Please check that system commands are available.",
        )
        # _empty_verdict = JudgeVerdict(  # JUDGE DISABLED
        #     verdicts=[], router_domain_correct=False, router_note="All agents failed",
        #     response_relevant=False, relevance_note="", overall_quality="POOR",
        #     quality_note="", judge_model="",
        # )
        # return JudgedResult(agent_result=_empty, verdict=_empty_verdict, domain=", ".join(domains))  # JUDGE DISABLED
        return _empty

    # Restore original domain order (classifier returns them sorted by score)
    domain_order = {d: i for i, d in enumerate(domains)}
    results.sort(key=lambda r: domain_order.get(
        r.agent.lower().replace("agent", ""), 99
    ))

    merged = _merge_results(results)

    if errors and verbose:
        note = f"\n\n*Note: some agents encountered errors: {'; '.join(errors)}*"
        merged = AgentResult(
            agent=merged.agent,
            raw_data_summary=merged.raw_data_summary,
            analysis=merged.analysis + note,
            suggestions=merged.suggestions,
            risk_levels=merged.risk_levels,
            full_response=merged.full_response + note,
        )

    # _judge_model = judge_model or os.getenv("JUDGE_MODEL", model)  # JUDGE DISABLED
    # judged = run_judge(merged, user_prompt, ", ".join(domains), client, _judge_model)  # JUDGE DISABLED

    # if verbose:  # JUDGE DISABLED
    #     blocked = [v for v in judged.verdict.verdicts if not v.approved]
    #     print(f"[Judge] {len(blocked)} suggestion(s) blocked. Model: {_judge_model}\n")

    # return judged  # JUDGE DISABLED
    return merged
