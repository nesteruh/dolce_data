"""
Router agent.
Classifies the user prompt and dispatches to the correct specialist agent.
"""

from __future__ import annotations

from openai import OpenAI

from src.agents import AgentResult, run_storage_agent, run_battery_agent, run_health_agent
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
}


def _classify(prompt: str) -> str:
    """
    Return 'storage', 'battery', or 'health' based on keyword overlap.
    Defaults to 'health' if ambiguous.
    """
    lower = prompt.lower()
    scores = {domain: 0 for domain in _DOMAIN_KEYWORDS}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                scores[domain] += 1
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] > 0 else "health"


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def handle(
    user_prompt: str,
    client: OpenAI,
    model: str = "llama3.2",
    verbose: bool = False,
) -> AgentResult:
    """
    Main pipeline:
      1. Detect OS
      2. Classify prompt → domain
      3. Run specialist agent (collects data + LLM analysis + formatting)
      4. Return the AgentResult (raw data + LLM analysis)
    """
    os_name = detect_os()
    domain = _classify(user_prompt)

    if verbose:
        print(f"\n[Router] OS={os_name} | Domain={domain}\n")

    dispatch = {
        "storage": run_storage_agent,
        "battery": run_battery_agent,
        "health":  run_health_agent,
    }
    result: AgentResult = dispatch[domain](user_prompt, client, model, os_name)

    if verbose:
        print(f"[{result.agent}] Raw data collected.")
        print(f"[{result.agent}] Suggestions found: {len(result.suggestions)}\n")

    return result
