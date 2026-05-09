"""
Specialist agents: Storage, Battery, Health.
Each agent:
  1. Collects real system data via collectors.py
  2. Builds a structured context prompt
  3. Calls the LLM to produce a polished, user-facing response
  4. Returns a formatted AgentResult
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

from openai import OpenAI

from src.collectors import (
    StorageData,
    BatteryData,
    HealthData,
    collect_storage,
    collect_battery,
    collect_health,
    detect_os,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    agent: str
    raw_data_summary: str        # what was actually collected
    analysis: str                # LLM interpretation
    suggestions: list[str]       # actionable suggestion strings
    risk_levels: list[str]       # parallel to suggestions: LOW / MEDIUM / HIGH
    full_response: str           # raw LLM text


# ─────────────────────────────────────────────────────────────────────────────
# Shared LLM helpers
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_BASE = textwrap.dedent("""\
    You are a specialist computer assistant. Answer the user's question using
    ONLY the system data provided below.

    RESPONSE LENGTH — match to the question type:
    • Simple status question ("what is X?", "current X?", "how much X?"):
      Answer in ONE sentence. No suggestions, no extra sections.
    • Diagnostic question ("why is X?", "how do I fix X?", "what should I do?"):
      Use this format:
        1. ONE or TWO sentences directly answering the question.
        2. Brief plain-language explanation of the root cause.
        3. Numbered suggestions — most impactful first, each on its own line:
           SUGGESTION [RISK:<LOW|MEDIUM|HIGH>]: <specific action>
        4. One short closing sentence.
      Keep under 300 words.

    NEVER suggest deleting system files, disabling security features, or any
    action that could destabilise the OS.

    CRITICAL: Only mention file names, folder names, process names, or sizes
    that appear VERBATIM in the data. NEVER invent values, names, or examples.
    If a section says "(no data)", skip it.
""")


def _llm_call(client: OpenAI, model: str, system: str, user: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content or ""


def _parse_suggestions(text: str) -> tuple[list[str], list[str]]:
    """Extract SUGGESTION lines into (suggestions, risk_levels) lists."""
    suggestions = []
    risks = []
    for line in text.splitlines():
        if line.strip().upper().startswith("SUGGESTION"):
            risk = "LOW"
            if "[RISK:HIGH]" in line.upper():
                risk = "HIGH"
            elif "[RISK:MEDIUM]" in line.upper():
                risk = "MEDIUM"
            content = line.split(":", 2)[-1].strip() if ":" in line else line
            suggestions.append(content)
            risks.append(risk)
    return suggestions, risks


# ─────────────────────────────────────────────────────────────────────────────
# Storage Agent
# ─────────────────────────────────────────────────────────────────────────────

_STORAGE_SYSTEM = _SYSTEM_BASE + textwrap.dedent("""\

    You are the STORAGE AGENT. You specialise in disk space, caches, large files,
    and cleanup opportunities.

    RULE: When suggesting what to delete, you MUST ONLY reference items that
    appear in the "SAFE TO DELETE" section of the data. Every suggestion must
    include the exact size shown and the delete command provided. Never invent
    paths, project names, or sizes. If a path shows "not found" or "0B", skip it.
""")


def run_storage_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
) -> AgentResult:
    os_name = os_name or detect_os()
    data: StorageData = collect_storage(os_name)

    # Format safe-to-delete items with real sizes (largest first)
    actionable = [
        item for item in data.safe_deletables
        if item.exists and item.size not in ("not found", "error", "timeout", "0B")
    ]
    if actionable:
        safe_lines = "\n".join(
            f"  • {item.description} ({item.path}): {item.size}  →  {item.delete_cmd}"
            for item in actionable
        )
        total_label = f"\n  Total recoverable: see sizes above"
    else:
        safe_lines = "  (no cleanable items found)"
        total_label = ""

    raw_summary = textwrap.dedent(f"""\
        OS: {os_name}

        === DISK VOLUMES ===
        {data.volumes or '(no data)'}

        === LIBRARY & APP AREAS (context only — do NOT suggest deleting these wholesale) ===
        {data.library_breakdown or '(no data)'}

        === LARGEST HOME DIRECTORIES ===
        {data.largest_dirs or '(no data)'}

        === SAFE TO DELETE (real sizes — suggest ONLY from this list) ===
{safe_lines}{total_label}

        === TRASH ===
        {data.trash_size or '(no data)'}

        === LARGE FILES IN DOWNLOADS (>50 MB — for manual review) ===
        {data.downloads_large or '(none found over 50 MB)'}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Here is the current system storage data:
        {raw_summary}

        Respond in this order:
        1. State how much space is actually free and how much can realistically be recovered.
           Be honest if the requested amount is not achievable.
        2. List items from "SAFE TO DELETE" ordered by size — include exact size and delete command for each.
        3. If "LARGE FILES IN DOWNLOADS" has entries, mention the user should review those manually.
        4. Note any Library areas that are unusually large (from the context section) but make clear
           those require manual judgment before deleting.
    """)

    llm_text = _llm_call(client, model, _STORAGE_SYSTEM, user_msg)
    suggestions, risks = _parse_suggestions(llm_text)

    return AgentResult(
        agent="StorageAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Battery Agent
# ─────────────────────────────────────────────────────────────────────────────

_BATTERY_SYSTEM = _SYSTEM_BASE + textwrap.dedent("""\

    You are the BATTERY AGENT. You specialise in battery health, charge cycles,
    energy-hungry processes, and power management settings. Help the user
    understand why their battery drains and what they can do about it.
""")


def run_battery_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
) -> AgentResult:
    os_name = os_name or detect_os()
    data: BatteryData = collect_battery(os_name)

    raw_summary = textwrap.dedent(f"""\
        OS: {os_name}

        === BATTERY STATUS ===
        {data.quick_status or '(no data)'}
        {data.health_detail or ''}

        === TOP CPU / ENERGY CONSUMERS ===
        {data.energy_consumers or '(no data)'}

        === POWER SETTINGS ===
        {data.power_settings or '(no data)'}

        === CONNECTIVITY (power drain factors) ===
        Bluetooth: {data.bluetooth or '(no data)'}
        Wi-Fi: {data.wifi or '(no data)'}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Here is the current battery and power data:
        {raw_summary}

        Answer the user's specific question using only the data above.
    """)

    llm_text = _llm_call(client, model, _BATTERY_SYSTEM, user_msg)
    suggestions, risks = _parse_suggestions(llm_text)

    return AgentResult(
        agent="BatteryAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Health Agent (CPU / GPU / RAM)
# ─────────────────────────────────────────────────────────────────────────────

_HEALTH_SYSTEM = _SYSTEM_BASE + textwrap.dedent("""\

    You are the HEALTH AGENT. You specialise in CPU, GPU, and RAM usage.
    Help the user understand which processes are consuming the most resources,
    why the system feels slow, and what they can safely do to improve performance.
    When suggesting to terminate a process, always include the process name and
    a brief explanation of what it does so the user can make an informed decision.
""")


def run_health_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
) -> AgentResult:
    os_name = os_name or detect_os()
    data: HealthData = collect_health(os_name)

    raw_summary = textwrap.dedent(f"""\
        OS: {os_name}

        === CPU OVERVIEW ===
        Model: {data.cpu_model or '(no data)'}
        Cores: {data.core_count or '(no data)'}
        Load averages (1/5/15 min): {data.load_avg or '(no data)'}
        Usage snapshot: {data.cpu_overview or '(no data)'}

        === TOP CPU PROCESSES ===
        {data.top_cpu_procs or '(no data)'}

        === MEMORY OVERVIEW ===
        {data.memory_overview or '(no data)'}
        Total RAM: {data.total_ram or '(no data)'}
        Summary: {data.memory_summary or '(no data)'}

        === TOP MEMORY PROCESSES ===
        {data.top_mem_procs or '(no data)'}

        === ZOMBIE PROCESSES ===
        {data.zombie_procs or '(none)'}

        === GPU INFO ===
        {data.gpu_info or '(no data)'}

        === UPTIME ===
        {data.uptime or '(no data)'}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Here is the current system health data:
        {raw_summary}

        Answer the user's specific question using only the data above.
    """)

    llm_text = _llm_call(client, model, _HEALTH_SYSTEM, user_msg)
    suggestions, risks = _parse_suggestions(llm_text)

    return AgentResult(
        agent="HealthAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
    )
