"""
Specialist agents: Storage, Battery, Health, Network, Startup, Activity, File, System, Document.
Each agent:
  1. Collects real system data (or runs a search tool) via the appropriate subsystem
  2. Builds a structured context prompt
  3. Calls the LLM to produce a polished, user-facing response
  4. Returns a formatted AgentResult
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import textwrap
from dataclasses import dataclass, field

from openai import OpenAI

from src.collectors import (
    StorageData,
    BatteryData,
    HealthData,
    NetworkData,
    StartupData,
    UserActivityData,
    FileContextData,
    collect_storage,
    collect_battery,
    collect_health,
    collect_network,
    collect_startup,
    collect_user_activity,
    collect_file_context,
    detect_os,
)
from src.actions import parse_actions


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
    actions: list = field(default_factory=list)  # parsed Action objects


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
           ACTION_<type>: <payload>   (optional — one per suggestion, only if a concrete command applies)
           Available types: shell  open_app  open_file  kill_process  force_quit_app
                            restart_process  delete_file  mkdir  create_file  copy_file
                            move_file  compress  extract  list_directory  set_permissions
           Use ACTION_shell for any terminal/shell command. Use specific types when applicable.
           Payload rules (CRITICAL — wrong payloads break execution):
           • shell:              the exact shell command string.
                                 On macOS use quoted names: killall "Google Chrome" not killall google-chrome
           • kill_process:       ONLY the process name or PID — e.g. "Google Chrome", NOT "kill Google Chrome"
           • force_quit_app:     ONLY the process name or PID — e.g. "Google Chrome", NOT "force quit Google Chrome"
           • open_app:           ONLY the app name — e.g. "iTerm2", NOT "openapp iTerm2; openapp WhatsApp"
                                 One ACTION_open_app line per app.
           • file ops:           path  (or  source | destination  for copy/move).
        4. One short closing sentence.
      Keep under 350 words.

    NEVER suggest deleting system files, disabling security features, or any
    action that could destabilise the OS.

    CRITICAL: Only mention file names, folder names, process names, or sizes
    that appear VERBATIM in the data. NEVER invent values, names, or examples.
    If a section says "(no data)", skip it.
""")


def _llm_call(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    json_mode: bool = True,
    on_token=None,
) -> str:
    kwargs: dict = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if on_token is None:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
            **kwargs,
        )
        return response.choices[0].message.content or ""
    # Streaming path — json_mode ignored (all diagnostic agents use json_mode=False).
    chunks: list[str] = []
    for chunk in client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        stream=True,
    ):
        delta = chunk.choices[0].delta.content or ""
        if delta:
            chunks.append(delta)
            on_token(delta)
    return "".join(chunks)


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
    on_token=None,
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

    llm_text = _llm_call(client, model, _STORAGE_SYSTEM, user_msg, json_mode=False, on_token=on_token)
    suggestions, risks = _parse_suggestions(llm_text)
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="StorageAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
        actions=actions,
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
    2. Structure diagnostic answers in this order:
       a. One sentence: current charge %, charging status, and time remaining.
       b. "The main battery drain is caused by:" — list the specific app names from
          the data with their CPU% or Energy Impact values.
       c. SUGGESTION lines using SUGGESTION [RISK:<level>]: format.
       d. One closing sentence.
    3. If BATTERY DRAIN HISTORY is present, add one sentence on whether the drain
       rate appears to be accelerating, steady, or slowing.
    4. Never fabricate app names, process names, or values. Use only what is in the data.
""")


def run_battery_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    on_token=None,
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

    llm_text = _llm_call(client, model, _BATTERY_SYSTEM, user_msg, json_mode=False, on_token=on_token)
    suggestions, risks = _parse_suggestions(llm_text)
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="BatteryAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
        actions=actions,
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

    CRITICAL: If the user explicitly names a specific app or process to kill/quit
    (e.g. "kill Google Chrome", "quit Safari"), target EXACTLY that app — do NOT
    substitute a different process even if it has higher CPU or RAM usage.
    Use ACTION_kill_process with the exact name the user mentioned.
""")


def run_health_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    on_token=None,
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

    llm_text = _llm_call(client, model, _HEALTH_SYSTEM, user_msg, json_mode=False, on_token=on_token)
    suggestions, risks = _parse_suggestions(llm_text)
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="HealthAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
        actions=actions,
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
    on_token=None,
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

    llm_text = _llm_call(client, model, _NETWORK_SYSTEM, user_msg, json_mode=False, on_token=on_token)
    suggestions, risks = _parse_suggestions(llm_text)
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="NetworkAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
        actions=actions,
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
    on_token=None,
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

    llm_text = _llm_call(client, model, _STARTUP_SYSTEM, user_msg, json_mode=False, on_token=on_token)
    suggestions, risks = _parse_suggestions(llm_text)
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="StartupAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
        actions=actions,
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
""")


def run_activity_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    on_token=None,
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

    llm_text = _llm_call(client, model, _ACTIVITY_SYSTEM, user_msg, json_mode=False, on_token=on_token)
    suggestions, risks = _parse_suggestions(llm_text)
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="ActivityAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
        actions=actions,
    )


# ─────────────────────────────────────────────────────────────────────────────
# File Agent
# ─────────────────────────────────────────────────────────────────────────────

_FILE_AGENT_SYSTEM = textwrap.dedent("""\
    You are a file management assistant. The user wants to perform a file operation.
    Use the provided paths (home, desktop, downloads, documents, cwd) to resolve
    relative or shorthand paths like "desktop" or "~/".

    For each step required, output exactly:
      SUGGESTION [RISK:<LOW|MEDIUM|HIGH>]: <plain-English description of what this step does>
      ACTION_<type>: <payload>

    Available action types and payload format:
      create_file      path | text content
      copy_file        source | destination
      move_file        source | destination
      rename_file      path | new name
      delete_file      path                    (risk: HIGH)
      mkdir            path
      list_directory   path
      compress         source | output.zip
      extract          archive | destination
      open_file        path
      shell            exact shell command string

    Rules:
    - Only reference paths from the context or paths explicitly stated by the user.
    - Use absolute paths in ACTION payloads (resolve ~ using the home path provided).
    - One SUGGESTION + one ACTION per step. No prose, no extra sections.
    - Risk must be HIGH for delete operations, MEDIUM for move/rename, LOW otherwise.
""")


def run_file_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    on_token=None,
) -> AgentResult:
    os_name = os_name or detect_os()
    ctx: FileContextData = collect_file_context(os_name)

    context_str = textwrap.dedent(f"""\
        OS: {os_name}
        Home:      {ctx.home}
        Desktop:   {ctx.desktop}
        Downloads: {ctx.downloads}
        Documents: {ctx.documents}
        Current directory: {ctx.cwd}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        Available paths:
        {context_str}
    """)

    llm_text = _llm_call(client, model, _FILE_AGENT_SYSTEM, user_msg, json_mode=False, on_token=on_token)
    suggestions, risks = _parse_suggestions(llm_text)
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="FileAgent",
        raw_data_summary=context_str,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
        actions=actions,
    )


# ─────────────────────────────────────────────────────────────────────────────
# System Agent
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_AGENT_SYSTEM = textwrap.dedent("""\
    You are a system settings and process management assistant. The user wants to
    change system settings or manage running processes. For each action, output:

      SUGGESTION [RISK:<LOW|MEDIUM|HIGH>]: <plain-English description>
      ACTION_<type>: <payload>

    Available action types and payload format:
      set_volume             0-100
      set_brightness         0-100
      toggle_wifi            on | off | (empty to toggle)
      connect_wifi           SSID | password
      disconnect_wifi        (empty)
      toggle_bluetooth       on | off | (empty to toggle)
      toggle_dark_mode       (empty)
      toggle_do_not_disturb  on | off | (empty to toggle)
      set_sleep_timer        minutes
      lock_screen            (empty)
      sleep_now              (empty)
      empty_trash            (empty)
      send_notification      Title | body message
      open_app               app name
      kill_process           ONLY the process/app name — e.g. "Google Chrome"
      force_quit_app         ONLY the process/app name — e.g. "Google Chrome"
      restart_process        ONLY the process/app name — e.g. "Finder"
      shell                  exact shell command string

    CRITICAL payload rules:
    - kill_process / force_quit_app / restart_process: payload is the app or process
      name ONLY — never include verbs like "kill", "quit", or "terminate".
      Example: "Google Chrome", NOT "kill Google Chrome".
    - Use kill_process for graceful quit; use force_quit_app when the user says
      "force quit" or "force kill".
    - On macOS, if using shell for killall, always quote multi-word names:
      killall "Google Chrome", NOT killall Google Chrome.

    Rules:
    - One SUGGESTION + one ACTION per action. No prose, no extra sections.
    - Risk: LOW for settings, MEDIUM for kill_process, HIGH for force_quit_app.
""")


def run_system_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    on_token=None,
) -> AgentResult:
    os_name = os_name or detect_os()

    user_msg = f'The user asked: "{user_prompt}"\n\nOS: {os_name}'

    llm_text = _llm_call(client, model, _SYSTEM_AGENT_SYSTEM, user_msg, json_mode=False, on_token=on_token)
    suggestions, risks = _parse_suggestions(llm_text)
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="SystemAgent",
        raw_data_summary=f"OS: {os_name}",
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
        actions=actions,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Document Agent — knowledge base search & RAG
# ─────────────────────────────────────────────────────────────────────────────

# Absolute path to agents/search/ so its relative imports resolve correctly.
_SEARCH_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "agents", "search")
)


def _ensure_search_path() -> None:
    if _SEARCH_DIR not in sys.path:
        sys.path.insert(0, _SEARCH_DIR)


_DOCUMENT_SYSTEM = textwrap.dedent("""\
    You are the DOCUMENT AGENT. You help users find and understand files stored
    in their personal knowledge base (locally indexed documents).

    You receive structured search results and must respond ONLY based on what
    those results contain. Never invent file names, paths, or document content.

    TASK TYPES and their expected response style:

    INDEX: Confirm what was indexed (folder path + progress summary). 2-3 sentences.

    FILENAME SEARCH: List found files with full paths and match scores. If nothing
    found, say so and suggest trying a content search or checking the spelling.

    CONTENT SEARCH: List the relevant files (name + path). For each, add one sentence
    from its summary explaining why it matches. If nothing found, say the knowledge
    base has no files on that topic and suggest indexing the relevant folder first.

    RAG (question answering): Answer the question directly using ONLY the provided
    document content. Cite the source file after every fact: (source: filename).
    If the answer is not in the documents, say so — do not guess.

    FILE ANALYSIS: Report the file summary and PII status clearly. If the file is
    not indexed, say so and suggest indexing its parent folder.

    SUGGESTION [RISK:LOW]: <action>   — use only when a concrete next step helps.

    CRITICAL: Only reference file names, paths, and content that appear VERBATIM
    in the results. NEVER hallucinate file names, paths, or document content.
""")


# ── Task discrimination ───────────────────────────────────────────────────────

_DOC_TASK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("index", re.compile(
        r'\b(index|reindex|re-index|scan\s+and\s+index|add\s+to\s+(index|knowledge))\b',
        re.IGNORECASE,
    )),
    ("file_analysis", re.compile(
        r'(?:analyze|tell\s+me\s+about|what\s+is\s+this|summarize|check|about)\s+'
        r'(?:the\s+file\s+)?([/~][^\s"\']+\.[a-zA-Z0-9]{1,6})',
        re.IGNORECASE,
    )),
    ("content_search", re.compile(
        r'(?:'
        r'(?:find|search\s+for|look\s+for|get)\s+(?:a\s+|an\s+|some\s+)?(?:file|document|doc)s?\s+'
        r'(?:about|on|related\s+to|that\s+(?:mention|discuss|contain|talk\s+about))'
        r'|(?:file|document|doc)s?\s+(?:that\s+)?(?:mention|discuss|contain|talk\s+about|about|on)\b'
        r'|looking\s+for\s+(?:a\s+|an\s+)?(?:file|document|doc)'
        r'|do\s+i\s+have\s+(?:a\s+|an\s+)?(?:file|document|anything)\s+(?:about|on|related\s+to|mention)'
        r'|which\s+(?:file|document)\s+(?:has|contains|mentions|discusses)'
        r'|(?:any|some)\s+(?:file|document)s?\s+(?:about|on|related\s+to)'
        r'|i(?:\'m|\s+am)\s+looking\s+for\s+(?:a\s+|an\s+)?(?:file|document)'
        r'|find\s+(?:a\s+)?(?:file|document)\s+(?:about|on|mentioning|discussing)'
        r'|i\s+want\s+(?:a\s+)?(?:file|document)\s+(?:about|mentioning|on)'
        r')',
        re.IGNORECASE,
    )),
    ("filename_search", re.compile(
        r'(?:where\s+is\s+(?:my\s+|the\s+)?|where\'s\s+(?:my\s+|the\s+)?'
        r'|find\s+(?:my\s+)?file\s+(?:named?\s+|called\s+)?'
        r'|locate\s+(?:my\s+|the\s+)?)',
        re.IGNORECASE,
    )),
]


def _extract_path(prompt: str) -> str | None:
    m = re.search(r'["\']([^"\']+)["\']', prompt)
    if m:
        return m.group(1).strip()
    m = re.search(r'([/~][^\s,;]+|[A-Za-z]:\\[^\s,;]+)', prompt)
    if m:
        return m.group(1).strip()
    return None


def _extract_filename_query(prompt: str) -> str:
    cleaned = re.sub(
        r'(?i)(?:where\s+is\s+(?:my\s+)?|where\'s\s+(?:my\s+)?'
        r'|find\s+(?:my\s+)?(?:file\s+)?(?:named?\s+|called\s+)?'
        r'|locate\s+(?:my\s+)?)',
        "",
        prompt,
    ).strip().rstrip("?").strip()
    return cleaned or prompt


def _extract_content_query(prompt: str) -> str:
    cleaned = re.sub(
        r'(?i)(?:'
        r'(?:find|search\s+for|look\s+for|get)\s+(?:a\s+|an\s+|some\s+)?'
        r'(?:file|document|doc)s?\s+(?:about|on|related\s+to|that\s+(?:mention|discuss|contain))'
        r'|(?:file|document|doc)s?\s+(?:that\s+)?(?:mention|discuss|contain|about|on)\b'
        r'|looking\s+for\s+(?:a\s+)?(?:file|document|doc)(?:\s+about)?'
        r'|do\s+i\s+have\s+(?:a\s+)?(?:file|document)\s+(?:about|on|related\s+to)?'
        r'|which\s+(?:file|document)\s+(?:has|contains|mentions|discusses)'
        r'|find\s+(?:a\s+)?(?:file|document)\s+(?:about|on|mentioning|discussing)'
        r'|i\s+want\s+(?:a\s+)?(?:file|document)\s+(?:about|mentioning|on)'
        r'|i(?:\'m|\s+am)\s+looking\s+for\s+(?:a\s+)?(?:file|document)(?:\s+(?:about|on|that))?'
        r')',
        "",
        prompt,
    ).strip().rstrip("?").strip()
    return cleaned or prompt


def _discriminate_doc_task(prompt: str) -> tuple[str, dict]:
    """Classify prompt into a document task type and extract its parameters."""
    for task, pattern in _DOC_TASK_PATTERNS:
        if pattern.search(prompt):
            if task == "index":
                return task, {"path": _extract_path(prompt)}
            if task == "file_analysis":
                m = re.search(r'([/~][^\s"\']+\.[a-zA-Z0-9]{1,6})', prompt)
                return task, {"path": m.group(1) if m else _extract_path(prompt)}
            if task == "content_search":
                return task, {"query": _extract_content_query(prompt)}
            if task == "filename_search":
                return task, {"query": _extract_filename_query(prompt)}
    return "rag", {"query": prompt}


# ── Agent entry point ─────────────────────────────────────────────────────────

def run_document_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    on_token=None,
) -> AgentResult:
    """Knowledge-base search and retrieval agent."""
    _ensure_search_path()

    task, params = _discriminate_doc_task(user_prompt)
    raw_lines: list[str] = [f"Task: {task.upper()}"]
    tool_data = ""

    if task == "index":
        folder = params.get("path")
        if not folder:
            tool_data = (
                "ERROR: No folder path detected in the prompt. "
                "Please provide a path, e.g. 'index my folder at /Users/alice/Documents'."
            )
        else:
            raw_lines.append(f"Path: {folder}")
            try:
                from indexing.indexer import build_index
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    build_index(folder)
                progress = buf.getvalue().strip()
                tool_data = (
                    f"Indexing complete.\nFolder: {folder}\n"
                    f"Progress log:\n{progress or '(no output)'}"
                )
                raw_lines.append(f"Progress:\n{progress}")
            except Exception as exc:
                tool_data = f"Indexing failed: {exc}"
                raw_lines.append(f"Error: {exc}")

    elif task == "file_analysis":
        path = params.get("path") or ""
        raw_lines.append(f"Path: {path}")
        try:
            from retrieval.searcher import get_file_analysis
            result = get_file_analysis(path)
            if result["indexed"]:
                pii_tag = "PII detected" if result["pii_detected"] else "No PII detected"
                tool_data = (
                    f"File: {result['file_name']}\n"
                    f"Path: {result['file_path']}\n"
                    f"Indexed: yes\n"
                    f"PII: {pii_tag}\n"
                    f"Summary: {result['summary'] or '(no summary available)'}"
                )
            else:
                tool_data = (
                    f"'{path}' is not in the knowledge base. "
                    "Index its parent folder first."
                )
            raw_lines.append(tool_data)
        except Exception as exc:
            tool_data = f"File analysis failed: {exc}"
            raw_lines.append(f"Error: {exc}")

    elif task == "content_search":
        query = params.get("query", user_prompt)
        raw_lines.append(f"Query: {query}")
        try:
            from retrieval.searcher import get_relevant_filenames
            results = get_relevant_filenames(query, k=5)
            if results:
                lines = [f"Found {len(results)} file(s) relevant to: '{query}'"]
                for i, r in enumerate(results, 1):
                    lines.append(f"{i}. {r['file_name']}\n   Path: {r['file_path']}")
                tool_data = "\n".join(lines)
            else:
                tool_data = f"No indexed files found about: '{query}'"
            raw_lines.append(tool_data)
        except Exception as exc:
            tool_data = f"Content search failed: {exc}"
            raw_lines.append(f"Error: {exc}")

    elif task == "filename_search":
        query = params.get("query", user_prompt)
        raw_lines.append(f"Query: {query}")
        try:
            from retrieval.searcher import fast_filename_search
            results = fast_filename_search(query, max_results=10)
            if results:
                lines = [f"Found {len(results)} file(s) matching '{query}':"]
                for r in results:
                    lines.append(
                        f"  • {r['file_name']} (score: {r['score']})\n"
                        f"    {r['file_path']}"
                    )
                tool_data = "\n".join(lines)
            else:
                tool_data = f"No files found matching '{query}' in the knowledge base."
            raw_lines.append(tool_data)
        except Exception as exc:
            tool_data = f"Filename search failed: {exc}"
            raw_lines.append(f"Error: {exc}")

    else:  # rag
        query = params.get("query", user_prompt)
        raw_lines.append(f"Query: {query}")
        try:
            from retrieval.searcher import get_document_context
            result = get_document_context(query, token_budget=1500)
            if result["context"]:
                tool_data = (
                    f"Retrieved context for: '{query}'\n"
                    f"Token estimate: {result['token_estimate']}\n\n"
                    f"{result['context']}"
                )
                raw_lines.append(f"Sources: {result.get('sources', [])}")
            else:
                tool_data = (
                    f"No relevant content found in the knowledge base for: '{query}'"
                )
            raw_lines.append(tool_data[:400])
        except Exception as exc:
            tool_data = f"RAG retrieval failed: {exc}"
            raw_lines.append(f"Error: {exc}")

    raw_data_summary = "\n".join(raw_lines)

    user_message = (
        f"User question: {user_prompt}\n\n"
        f"=== SEARCH RESULTS ===\n{tool_data}"
    )
    full_response = _llm_call(
        client, model, _DOCUMENT_SYSTEM, user_message,
        json_mode=False, on_token=on_token,
    )

    suggestions, risk_levels = _parse_suggestions(full_response)

    return AgentResult(
        agent="DocumentAgent",
        raw_data_summary=raw_data_summary,
        analysis=full_response,
        suggestions=suggestions,
        risk_levels=risk_levels,
        full_response=full_response,
        actions=[],
    )
