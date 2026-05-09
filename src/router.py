"""
Router agent.
Classifies the user prompt and dispatches to the correct specialist agent.
"""

from __future__ import annotations

import os

from openai import OpenAI

from src.agents import (
    AgentResult,
    run_storage_agent,
    run_battery_agent,
    run_health_agent,
    run_network_agent,
    run_startup_agent,
    run_activity_agent,
)
from src.collectors import detect_os
from src.judge import JudgedResult, run_judge


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
    ],
}


def _classify(prompt: str) -> str:
    """
    Return the domain whose keywords best match the prompt.
    Ties are broken by domain priority (health is the last resort fallback).
    """
    lower = prompt.lower()
    scores = {domain: 0 for domain in _DOMAIN_KEYWORDS}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                scores[domain] += 1

    if all(s == 0 for s in scores.values()):
        return "health"

    # Tiebreak: health loses to any equally-scoring specific domain
    _priority = ["activity", "startup", "network", "storage", "battery", "health"]
    return max(scores, key=lambda d: (scores[d], -_priority.index(d)))


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def handle(
    user_prompt: str,
    client: OpenAI,
    model: str = "llama3.2",
    judge_model: str | None = None,
    verbose: bool = False,
) -> JudgedResult:
    """
    Main pipeline:
      1. Detect OS
      2. Classify prompt → domain
      3. Run specialist agent (collects data + LLM analysis + formatting)
      4. Run judge layer (parallel per-suggestion evaluation + holistic check)
      5. Return JudgedResult wrapping AgentResult + JudgeVerdict
    """
    os_name = detect_os()
    domain = _classify(user_prompt)

    if verbose:
        print(f"\n[Router] OS={os_name} | Domain={domain}\n")

    dispatch = {
        "storage":  run_storage_agent,
        "battery":  run_battery_agent,
        "health":   run_health_agent,
        "network":  run_network_agent,
        "startup":  run_startup_agent,
        "activity": run_activity_agent,
    }
    result: AgentResult = dispatch[domain](user_prompt, client, model, os_name)

    if verbose:
        print(f"[{result.agent}] Raw data collected.")
        print(f"[{result.agent}] Suggestions found: {len(result.suggestions)}\n")

    _judge_model = judge_model or os.getenv("JUDGE_MODEL", model)
    judged = run_judge(result, user_prompt, domain, client, _judge_model)

    if verbose:
        blocked = [v for v in judged.verdict.verdicts if not v.approved]
        print(f"[Judge] {len(blocked)} suggestion(s) blocked. Model: {_judge_model}\n")

    return judged
