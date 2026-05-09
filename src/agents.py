"""
Specialist agents: Storage, Battery, Health.
Each agent:
  1. Collects real system data via collectors.py
  2. Builds a structured context prompt
  3. Calls the LLM to produce analysis + suggestions
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
    You are a specialist computer assistant agent. Your job is to:
    1. Analyse the raw system data provided to you.
    2. Explain clearly what is causing any issues.
    3. List specific, actionable suggestions — each on its own line starting with
       "SUGGESTION [RISK:<LOW|MEDIUM|HIGH>]: <action>".
    4. Be concise. No filler. Focus on the most impactful items first.
    5. NEVER suggest deleting system files, disabling security features, or
       any action that could destabilize the OS.
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
            # Format: SUGGESTION [RISK:LOW]: do something
            risk = "LOW"
            if "[RISK:HIGH]" in line.upper():
                risk = "HIGH"
            elif "[RISK:MEDIUM]" in line.upper():
                risk = "MEDIUM"
            # Strip the prefix to get plain text
            content = line.split(":", 2)[-1].strip() if ":" in line else line
            suggestions.append(content)
            risks.append(risk)
    return suggestions, risks


# ─────────────────────────────────────────────────────────────────────────────
# Storage Agent
# ─────────────────────────────────────────────────────────────────────────────

_STORAGE_SYSTEM = _SYSTEM_BASE + textwrap.dedent("""\

    You are the STORAGE AGENT. You specialise in disk space, caches, large files,
    and cleanup opportunities. Help the user understand their storage situation
    and what they can safely free up.
""")


def run_storage_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
) -> AgentResult:
    os_name = os_name or detect_os()
    data: StorageData = collect_storage(os_name)

    raw_summary = textwrap.dedent(f"""\
        OS: {os_name}

        === DISK VOLUMES ===
        {data.volumes or '(no data)'}

        === LARGEST DIRECTORIES IN HOME ===
        {data.largest_dirs or '(no data)'}

        === CACHE SIZES ===
        {data.cache_size or '(no data)'}
        {data.cache_breakdown or ''}

        === TRASH SIZE ===
        {data.trash_size or '(no data)'}

        === DOWNLOADS OLDER THAN 30 DAYS ===
        {data.downloads_old or '(none found)'}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Here is the current system storage data:
        {raw_summary}

        Analyse this data, explain the storage situation, identify what is using the most
        space, and provide specific suggestions. Use the SUGGESTION format for each action.
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
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Here is the current battery and power data:
        {raw_summary}

        Diagnose why the battery may be draining quickly. Identify the biggest energy
        consumers, comment on battery health if data is available, and give specific
        suggestions. Use the SUGGESTION format for each action.
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

        === CPU OVERVIEW (load avg, cores, model) ===
        {data.cpu_overview or '(no data)'}

        === TOP CPU PROCESSES ===
        {data.top_cpu_procs or '(no data)'}

        === MEMORY OVERVIEW ===
        {data.memory_overview or '(no data)'}

        === TOP MEMORY PROCESSES ===
        {data.top_mem_procs or '(no data)'}

        === GPU INFO ===
        {data.gpu_info or '(no data)'}

        === UPTIME ===
        {data.uptime or '(no data)'}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Here is the current system health data:
        {raw_summary}

        Diagnose what is slowing the system down. Identify the top resource consumers,
        explain whether their usage is normal or abnormal, and give specific, prioritised
        suggestions. For any process you suggest terminating, explain briefly what it is.
        Use the SUGGESTION format for each action.
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
