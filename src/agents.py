"""
Specialist agents: Storage, Battery, Health.
Each agent:
  1. Collects real system data via collectors.py
  2. Builds a structured context prompt
  3. Calls the LLM to produce a polished, user-facing response
  4. Returns a formatted AgentResult
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass

from openai import OpenAI

from src.collectors import (
    StorageData,
    BatteryData,
    HealthData,
    NetworkData,
    StartupData,
    UserActivityData,
    collect_storage,
    collect_battery,
    collect_health,
    collect_network,
    collect_startup,
    collect_user_activity,
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
    • Suggestion / overview question ("what are your suggestions?", "give me a report",
      "what should I do about X?", "how is my X?", "what is wrong with my X?",
      "what can I improve?"):
      Produce a STRUCTURED MINI-REPORT with at MINIMUM 3 suggestions (aim for 5):
        1. Executive summary: 2-3 sentences on the overall state of this domain.
        2. Findings: for EACH significant data point in the system data, one sentence
           naming the value and what it means.
        3. Numbered suggestions — at LEAST 3, ordered by impact:
           SUGGESTION [RISK:<LOW|MEDIUM|HIGH>]: <specific action>
           Each suggestion MUST include: what to do, the exact value/name from data,
           and why it matters. Never repeat the same suggestion.
        4. One closing sentence on expected benefit if suggestions are followed.

    NEVER suggest deleting system files, disabling security features, or any
    action that could destabilise the OS.

    CRITICAL: Only mention file names, folder names, process names, or sizes
    that appear VERBATIM in the data. NEVER invent values, names, or examples.
    If a section says "(no data)", skip it.
""")


_JSON_FORMAT = textwrap.dedent("""\

    === RESPONSE FORMAT ===
    You MUST respond with ONLY valid JSON — no prose, no markdown, no code fences.
    Use exactly this schema:
    {
      "analysis": "<summary of your diagnosis — 2-3 sentences for overview questions, 1-2 for specific questions>",
      "suggestions": [
        {
          "text": "<specific, actionable step the user should take>",
          "risk": "LOW|MEDIUM|HIGH",
          "rationale": "<one sentence explaining why this is recommended>"
        }
      ]
    }
    For simple status questions ("what is X?"), return an empty suggestions array.
    For suggestion/overview questions, you MUST return at LEAST 3 suggestions — aim for 5.
    CRITICAL: Only include file names, sizes, process names, and paths that appear
    VERBATIM in the system data above. Never invent values.
""")


def _llm_call(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    history: list[dict] | None = None,
) -> str:
    messages: list[dict] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user})
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def _parse_agent_json(data: dict) -> tuple[list[str], list[str]]:
    """Extract suggestions and risk levels from the structured JSON response."""
    suggestions: list[str] = []
    risks: list[str] = []
    for item in data.get("suggestions", []):
        text = str(item.get("text", "")).strip()
        risk = str(item.get("risk", "LOW")).upper()
        if risk not in ("LOW", "MEDIUM", "HIGH"):
            risk = "LOW"
        if text:
            suggestions.append(text)
            risks.append(risk)
    return suggestions, risks


def _format_full_response(data: dict, fallback: str = "") -> str:
    """Reconstruct a human-readable markdown response from the JSON result."""
    parts: list[str] = []
    analysis = str(data.get("analysis", "")).strip()
    if analysis:
        parts.append(analysis)
    suggestion_lines: list[str] = []
    count = 0
    for item in data.get("suggestions", []):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        count += 1
        risk = str(item.get("risk", "LOW")).upper()
        rationale = str(item.get("rationale", "")).strip()
        line = f"{count}. **[{risk}]** {text}"
        if rationale:
            line += f"  \n   *{rationale}*"
        suggestion_lines.append(line)
    if suggestion_lines:
        parts.append("\n**Suggestions:**")
        parts.extend(suggestion_lines)
    if not parts and fallback:
        parts.append(fallback)
    return "\n".join(parts)


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
""") + _JSON_FORMAT


def _parse_months(prompt: str) -> int:
    """Extract a time period from the user prompt; default 6 months."""
    p = prompt.lower()
    m = re.search(r'(\d+)\s*(year|month|week)', p)
    if not m:
        return 6
    n, unit = int(m.group(1)), m.group(2)
    if unit == "year":
        return n * 12
    if unit == "week":
        return max(1, round(n / 4))
    return n


def run_storage_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    history: list[dict] | None = None,
) -> AgentResult:
    os_name = os_name or detect_os()
    stale_months = _parse_months(user_prompt)
    data: StorageData = collect_storage(os_name, stale_months=stale_months)

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

        === STALE LARGE FILES (>50 MB, not modified in {stale_months}+ months) ===
        {data.stale_files or '(none found)'}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Here is the current system storage data:
        {raw_summary}

        Respond in this order:
        1. State how much space is actually free and how much can realistically be recovered.
           Be honest if the requested amount is not achievable.
        2. List items from "SAFE TO DELETE" ordered by size — include exact size and delete command for each.
        3. If "STALE LARGE FILES" has entries, list them by size — these are safe candidates for
           manual deletion. Mention when each was last modified.
        4. If "LARGE FILES IN DOWNLOADS" has entries, mention those for manual review.
        5. Note any Library areas that are unusually large (from the context section) but make clear
           those require manual judgment before deleting.
    """)

    llm_text = _llm_call(client, model, _STORAGE_SYSTEM, user_msg, history)
    data = json.loads(llm_text)
    suggestions, risks = _parse_agent_json(data)

    return AgentResult(
        agent="StorageAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(data),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Battery Agent
# ─────────────────────────────────────────────────────────────────────────────

_BATTERY_SYSTEM = _SYSTEM_BASE + textwrap.dedent("""\

    You are the BATTERY AGENT. You specialise in battery health, charge cycles,
    energy-hungry processes, and power management settings.

    CRITICAL RESPONSE RULES:
    1. ALWAYS check TOP CPU / ENERGY CONSUMERS and ENERGY IMPACT sections first.
       If any process shows CPU% above 5% or a notable Energy Impact value, name it
       explicitly as a primary drain cause — never ignore this data.
    2. In the "analysis" field, structure your diagnosis in this order:
       a. One sentence: current charge %, charging status, and time remaining.
       b. "The main battery drain is caused by:" — list the specific app names from
          the data with their CPU% or Energy Impact values.
       c. If BATTERY DRAIN HISTORY is present, one sentence on drain trend.
    3. Never fabricate app names, process names, or values. Use only what is in the data.
""") + _JSON_FORMAT


def run_battery_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    history: list[dict] | None = None,
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

        === ENERGY IMPACT (Activity Monitor equivalent) ===
        {data.energy_impact or '(not available on this OS)'}

        === BATTERY DRAIN HISTORY (last ~1 hour) ===
        {data.drain_history or '(not available on this OS)'}

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

    llm_text = _llm_call(client, model, _BATTERY_SYSTEM, user_msg, history)
    data = json.loads(llm_text)
    suggestions, risks = _parse_agent_json(data)

    return AgentResult(
        agent="BatteryAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(data),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Health Agent (CPU / GPU / RAM)
# ─────────────────────────────────────────────────────────────────────────────

_HEALTH_SYSTEM = _SYSTEM_BASE + textwrap.dedent("""\

    You are the HEALTH AGENT. You specialise in CPU, GPU, and RAM usage.
    Help the user understand which processes are consuming the most resources,
    why the system feels slow, and what they can safely do to improve performance.
    When suggesting to terminate a process, include the process name and a brief
    explanation of what it does so the user can make an informed decision.
""") + _JSON_FORMAT


def run_health_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    history: list[dict] | None = None,
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

    llm_text = _llm_call(client, model, _HEALTH_SYSTEM, user_msg, history)
    data = json.loads(llm_text)
    suggestions, risks = _parse_agent_json(data)

    return AgentResult(
        agent="HealthAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(data),
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
""") + _JSON_FORMAT


def run_network_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    history: list[dict] | None = None,
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

        NOTE: Some sections may show "access denied" or "ERROR" — those sections have
        no usable data. Do NOT generate a suggestion for a section with no data.
        Only produce suggestions you can fully describe using values present above.
        If fewer than 3 actionable items exist, return only the ones you can justify.
    """)

    llm_text = _llm_call(client, model, _NETWORK_SYSTEM, user_msg, history)
    data = json.loads(llm_text)
    suggestions, risks = _parse_agent_json(data)

    return AgentResult(
        agent="NetworkAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(
            data,
            fallback="No actionable network issues detected from the available data.",
        ),
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
""") + _JSON_FORMAT


def run_startup_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    history: list[dict] | None = None,
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

    llm_text = _llm_call(client, model, _STARTUP_SYSTEM, user_msg, history)
    data = json.loads(llm_text)
    suggestions, risks = _parse_agent_json(data)

    return AgentResult(
        agent="StartupAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(data),
    )


# ─────────────────────────────────────────────────────────────────────────────
# User Activity Agent
# ─────────────────────────────────────────────────────────────────────────────

_ACTIVITY_SYSTEM = _SYSTEM_BASE + textwrap.dedent("""\

    You are the ACTIVITY AGENT. You specialise in analysing user activity patterns:
    recently opened files, frequently used applications, shell command history,
    and login sessions. Help the user understand what they have been working on,
    which tools they rely on most, and surface any patterns worth knowing
    (e.g. apps opened rarely despite being on boot, or file types that dominate
    recent work). Do NOT make assumptions about file contents — only reference
    names and timestamps visible in the data.
""") + _JSON_FORMAT


def run_activity_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    history: list[dict] | None = None,
) -> AgentResult:
    os_name = os_name or detect_os()
    data: UserActivityData = collect_user_activity(os_name)

    raw_summary = textwrap.dedent(f"""\
        OS: {os_name}

        === RECENTLY OPENED FILES (newest first) ===
        {data.recent_files or '(no data)'}

        === FREQUENTLY USED APPS ===
        {data.frequent_apps or '(no data)'}

        === RECENT SHELL COMMANDS (last 30 unique) ===
        {data.shell_history or '(no data)'}

        === LOGIN SESSIONS ===
        {data.last_logins or '(no data)'}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Here is the current user activity data:
        {raw_summary}

        Respond in this order:
        1. Summarise what the user has been working on recently based on file names and commands.
        2. Identify the most-used apps or file types.
        3. Flag anything unusual or worth the user's attention (e.g. rarely-used apps, repeated errors in commands).
        4. Provide suggestions only if they are directly supported by the data shown above.
    """)

    llm_text = _llm_call(client, model, _ACTIVITY_SYSTEM, user_msg, history)
    data = json.loads(llm_text)
    suggestions, risks = _parse_agent_json(data)

    return AgentResult(
        agent="ActivityAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(data),
    )
