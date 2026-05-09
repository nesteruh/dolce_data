"""
Router agent.
Classifies the user prompt and dispatches to one or more specialist agents.
When a prompt spans multiple domains (e.g. "slow AND battery dying"), all
relevant agents run in parallel and their results are merged into one response.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

from src.agents import (
    AgentResult,
    run_storage_agent,
    run_battery_agent,
    run_health_agent,
    run_network_agent,
    run_startup_agent,
)
from src.collectors import detect_os


# ─────────────────────────────────────────────────────────────────────────────
# Domain keyword mapping
# ─────────────────────────────────────────────────────────────────────────────

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "storage": [
        "disk", "storage", "space", "free space", "gb", "tb", "cache",
        "temp", "large file", "download", "trash", "full", "clean", "wipe",
        "no space", "running out", "file size",
    ],
    "battery": [
        "battery", "charge", "drain", "power", "charging", "unplugged",
        "battery health", "cycle", "watt", "energy", "low battery", "dies fast",
        "discharge", "plugged",
    ],
    "health": [
        "cpu", "gpu", "ram", "memory", "processor", "slow", "lag", "freeze",
        "hang", "performance", "resource", "process", "activity", "swap",
        "fan", "hot", "thermal", "speed", "fast", "responsive",
    ],
    "network": [
        "network", "internet", "connection", "bandwidth", "wifi", "wi-fi",
        "ethernet", "firewall", "vpn", "dns", "port", "latency", "online",
        "downloading", "uploading", "ip address", "proxy", "slow internet",
        "no internet", "connected", "packet", "ping",
    ],
    "startup": [
        "startup", "boot", "login", "login item", "autostart", "launch agent",
        "launch daemon", "background service", "service", "slow boot",
        "starts automatically", "runs on startup", "disable on startup",
        "startup program", "slow login",
    ],
}

_AGENT_EMOJI: dict[str, str] = {
    "StorageAgent": "🗄️",
    "BatteryAgent": "🔋",
    "HealthAgent":  "💻",
    "NetworkAgent": "🌐",
    "StartupAgent": "🚀",
}


# ─────────────────────────────────────────────────────────────────────────────
# Multi-domain classifier
# ─────────────────────────────────────────────────────────────────────────────

def _classify_domains(prompt: str) -> list[str]:
    """
    Return an ordered list of matched domains.

    Every domain that scores at least 1 keyword hit is included.
    Using a flat threshold of 1 means explicit mentions (e.g. "cpu", "ram",
    "network" all in one sentence) always trigger their respective agents,
    even when one domain accumulates more keyword hits than another.
    Falls back to ["health"] when nothing matches at all.
    """
    lower = prompt.lower()
    scores: dict[str, int] = {domain: 0 for domain in _DOMAIN_KEYWORDS}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                scores[domain] += 1

    matched = [d for d, s in scores.items() if s >= 1]
    if not matched:
        return ["health"]

    # Sort by score descending so the most-relevant agent leads
    matched.sort(key=lambda d: scores[d], reverse=True)
    return matched


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

    # Merge suggestions and risk levels from all agents
    all_suggestions = [s for r in results for s in r.suggestions]
    all_risks = [rv for r in results for rv in r.risk_levels]

    combined_agent_name = " + ".join(r.agent for r in results)

    return AgentResult(
        agent=combined_agent_name,
        raw_data_summary=merged_raw,
        analysis=merged_response,
        suggestions=all_suggestions,
        risk_levels=all_risks,
        full_response=merged_response,
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
}


def handle(
    user_prompt: str,
    client: OpenAI,
    model: str = "llama3.2",
    verbose: bool = False,
) -> AgentResult:
    """
    Main pipeline:
      1. Detect OS
      2. Classify prompt → one or more domains
      3. Run matched specialist agents (in parallel for multi-domain)
      4. Merge into a single AgentResult
      5. Return AgentResult (caller uses .full_response and .raw_data_summary)
    """
    os_name = detect_os()
    domains = _classify_domains(user_prompt)

    if verbose:
        tag = "Domain" if len(domains) == 1 else "Domains"
        print(f"\n[Router] OS={os_name} | {tag}={domains}\n")

    # ── Single domain: simple path, no threading overhead ──────────────────
    if len(domains) == 1:
        runner = _DISPATCH[domains[0]]
        result: AgentResult = runner(user_prompt, client, model, os_name)
        if verbose:
            print(f"[{result.agent}] Suggestions: {len(result.suggestions)}\n")
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
        # Return a minimal AgentResult so main.py's attribute access never fails
        return AgentResult(
            agent="Router",
            raw_data_summary="",
            analysis="All agents failed to collect data.",
            suggestions=[],
            risk_levels=[],
            full_response="⚠️ All agents failed to collect data. Please check that system commands are available.",
        )

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

    return merged
