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
