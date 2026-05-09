"""
Router agent.
Classifies the user prompt and dispatches to the correct specialist agent.
The final LLM pass produces a polished, user-facing answer with suggestions.
"""

from __future__ import annotations

import textwrap

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
    # If all scores are 0, fall back to health (most general)
    return best if scores[best] > 0 else "health"


# ─────────────────────────────────────────────────────────────────────────────
# Final synthesis LLM call
# ─────────────────────────────────────────────────────────────────────────────

_SYNTHESIS_SYSTEM = textwrap.dedent("""\
    You are a friendly, expert computer assistant presenting results to the user.
    A specialist agent has already analysed the system and produced findings.
    Your job is to:
    1. Give a SHORT, clear headline answer to the user's question (1–2 sentences).
    2. Explain the root cause in plain language.
    3. Present the suggestions in a numbered list, each with its risk level in brackets.
    4. End with one encouraging closing line.
    Keep the total response under 300 words. Be direct. No jargon unless explained.
""")


def _synthesise(
    client: OpenAI,
    model: str,
    user_prompt: str,
    result: AgentResult,
) -> str:
    suggestions_block = "\n".join(
        f"  {i+1}. [{risk}] {sug}"
        for i, (sug, risk) in enumerate(zip(result.suggestions, result.risk_levels))
    ) or "  (No specific suggestions — system looks healthy.)"

    user_msg = textwrap.dedent(f"""\
        User's question: "{user_prompt}"

        Agent: {result.agent}
        Agent analysis:
        {result.analysis}

        Parsed suggestions (with risk levels):
        {suggestions_block}

        Now write the final, friendly response to the user.
    """)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYNTHESIS_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
    )
    return response.choices[0].message.content or ""


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def handle(
    user_prompt: str,
    client: OpenAI,
    model: str = "llama3.2",
    verbose: bool = False,
) -> str:
    """
    Main pipeline:
      1. Detect OS
      2. Classify prompt → domain
      3. Run specialist agent (collects data + LLM analysis)
      4. Synthesise a polished final answer
      5. Return the final answer string
    """
    os_name = detect_os()
    domain = _classify(user_prompt)

    if verbose:
        print(f"\n[Router] OS={os_name} | Domain={domain}\n")

    # Dispatch to specialist
    dispatch = {
        "storage": run_storage_agent,
        "battery": run_battery_agent,
        "health":  run_health_agent,
    }
    runner = dispatch[domain]
    result: AgentResult = runner(user_prompt, client, model, os_name)

    if verbose:
        print(f"[{result.agent}] Raw data collected.")
        print(f"[{result.agent}] Suggestions found: {len(result.suggestions)}\n")

    # Final synthesis pass
    final_answer = _synthesise(client, model, user_prompt, result)
    return final_answer
