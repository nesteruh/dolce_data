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
    NetworkData,
    StartupData,
    collect_storage,
    collect_battery,
    collect_health,
    collect_network,
    collect_startup,
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
    You are a specialist computer assistant. Analyse the system data provided
    and respond directly to the user in this exact format:

    1. ONE or TWO sentences directly answering the user's question.
    2. A brief plain-language explanation of the root cause.
    3. A numbered list of suggestions — most impactful first. Each suggestion
       must be on its own line in this format:
       SUGGESTION [RISK:<LOW|MEDIUM|HIGH>]: <specific action>
    4. One short, encouraging closing sentence.

    Keep the total response under 300 words. Be direct. Avoid jargon unless
    you explain it in plain language immediately after.
    NEVER suggest deleting system files, disabling security features, or any
    action that could destabilise the OS.

    CRITICAL: Base your entire response STRICTLY on the system data provided.
    Only mention specific file names, folder names, process names, project names,
    or sizes that appear VERBATIM in the data below. NEVER invent examples,
    placeholder names (like "Project A"), or sizes that are not in the data.
    If a section says "(no data)" or is empty, acknowledge that and skip it.
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

        Diagnose why the battery may be draining quickly. Identify the biggest
        energy consumers, comment on battery health if data is available, and
        give specific suggestions.
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

        Diagnose what is slowing the system down. Identify the top resource
        consumers, explain whether their usage is normal or abnormal, and give
        specific, prioritised suggestions. For any process you suggest
        terminating, explain briefly what it is.
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


# ─────────────────────────────────────────────────────────────────────────────
# Network Agent
# ─────────────────────────────────────────────────────────────────────────────

_NETWORK_SYSTEM = _SYSTEM_BASE + textwrap.dedent("""\

    You are the NETWORK AGENT. You specialise in diagnosing network issues using
    real system data: active connections, listening ports, bandwidth usage, DNS
    configuration, firewall status, and VPN detection.

    STRICT RULES:
    - Base every finding on the actual data provided. Do NOT invent issues.
    - NEVER suggest generic steps like "restart your router" or "check your ISP".
    - If the firewall is disabled, always flag it as a finding.
    - If a VPN is active, mention it — it often causes slowdowns.
    - If a specific process has an unusually high number of connections, name it.
    - If the data shows nothing unusual, say so clearly and briefly.
    - Suggestions must reference actual process names, ports, or settings from the data.
""")


def run_network_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
) -> AgentResult:
    os_name = os_name or detect_os()
    data: NetworkData = collect_network(os_name)

    raw_summary = textwrap.dedent(f"""\
        OS: {os_name}

        === NETWORK INTERFACES ===
        {data.interfaces or '(no data)'}

        === ROUTING TABLE ===
        {data.routing_table or '(no data)'}

        === WIFI STATUS ===
        {data.wifi_status or '(not applicable)'}

        === DNS CONFIGURATION ===
        {data.dns_config or '(no data)'}

        === FIREWALL STATUS ===
        {data.firewall_status or '(no data)'}
        {data.firewall_apps or ''}
        {data.firewall_iptables or ''}
        {data.firewall_rules or ''}

        === VPN / TUNNEL DETECTION ===
        {data.vpn_detection or '(none detected)'}

        === PROXY CONFIGURATION ===
        {data.proxy_config or '(not applicable)'}

        === ACTIVE CONNECTIONS (with processes) ===
        {data.active_connections or '(no data)'}
        {data.connections_named or ''}

        === LISTENING PORTS ===
        {data.listening_ports or '(no data)'}

        === BANDWIDTH BY PROCESS / ADAPTER ===
        {data.bandwidth or '(no data)'}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Here is the real network data collected from this machine right now:
        {raw_summary}

        Diagnose the network situation based solely on this data. Identify which
        processes are using the most connections or bandwidth, whether the firewall
        is enabled, whether a VPN is active and could be causing slowdown, and
        whether any port is exposed unexpectedly. Give specific, data-driven
        suggestions. Use the SUGGESTION format for each action.
    """)

    llm_text = _llm_call(client, model, _NETWORK_SYSTEM, user_msg)
    suggestions, risks = _parse_suggestions(llm_text)

    return AgentResult(
        agent="NetworkAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Startup Agent
# ─────────────────────────────────────────────────────────────────────────────

_STARTUP_SYSTEM = _SYSTEM_BASE + textwrap.dedent("""\

    You are the STARTUP AGENT. You specialise in login items, launch agents,
    background services, and boot time optimisation.

    STRICT RULES:
    - Base every finding on the actual list of startup items provided.
    - Classify each item: is it an OS component, a hardware driver, a third-party
      app helper, or unknown?
    - NEVER suggest disabling OS core services (launchd, systemd, winlogon, svchost,
      lsass, csrss) or security agents (antivirus, firewall daemons).
    - If an item's binary is missing, flag it as orphaned and safe to remove.
    - For each item you suggest disabling, explain what it does and why it is safe.
    - If boot time data is available, highlight the slowest services.
""")


def run_startup_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
) -> AgentResult:
    os_name = os_name or detect_os()
    data: StartupData = collect_startup(os_name)

    raw_summary = textwrap.dedent(f"""\
        OS: {os_name}

        === USER-LEVEL STARTUP ITEMS ===
        {data.user_items or '(none found)'}

        === SYSTEM-LEVEL STARTUP ITEMS ===
        {data.system_items or '(none found)'}
        {data.system_daemons or ''}

        === CURRENTLY RUNNING STARTUP ITEMS ===
        {data.running_items or '(none found)'}

        === SCHEDULED TASKS (startup-triggered) ===
        {data.scheduled_tasks or '(not applicable)'}

        === AUTO-START SERVICES ===
        {data.auto_services or '(not applicable)'}

        === BOOT TIME SUMMARY ===
        {data.boot_time_summary or '(not available on this OS)'}

        === SLOWEST SERVICES AT BOOT ===
        {data.boot_time_per_service or '(not available on this OS)'}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Here is the startup and service data collected from this machine:
        {raw_summary}

        Analyse which startup items are third-party vs OS components, identify
        any that are resource-heavy or unnecessary, flag orphaned entries, and
        highlight what is slowing down boot if timing data is available.
        Give specific, safe suggestions. Use the SUGGESTION format for each action.
    """)

    llm_text = _llm_call(client, model, _STARTUP_SYSTEM, user_msg)
    suggestions, risks = _parse_suggestions(llm_text)

    return AgentResult(
        agent="StartupAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
    )
