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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from openai import OpenAI

from src.actions import parse_actions

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
    actions: list = field(default_factory=list)  # list[Action] — executable actions


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
           ACTION_<type>: <payload>          ← optional, only when concrete
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

    ACTION LINES — optional, add one immediately after a SUGGESTION when a
    specific executable action is available. Format: ACTION_<type>: <payload>

    Process / app:
      ACTION_shell: <command>          ACTION_open_app: <name>
      ACTION_open_file: <path>         ACTION_kill_process: <PID number ONLY>

    Files (use | to separate parts):
      ACTION_create_file: <path> | <content>    ACTION_copy_file: <src> | <dst>
      ACTION_move_file: <src> | <dst>           ACTION_rename_file: <path> | <new name>
      ACTION_delete_file: <path>                ACTION_mkdir: <path>
      ACTION_compress: <src> | <out.zip>        ACTION_extract: <arc> | <dst>
      ACTION_list_directory: <path>             ACTION_set_permissions: <path> | <mode>

    System settings (no | needed unless shown):
      ACTION_set_volume: <0-100>                ACTION_set_brightness: <0-100>
      ACTION_toggle_dark_mode:                  ACTION_toggle_do_not_disturb: <on|off>
      ACTION_toggle_wifi: <on|off>              ACTION_toggle_bluetooth: <on|off>
      ACTION_connect_wifi: <SSID> | <password>  ACTION_disconnect_wifi:
      ACTION_lock_screen:                       ACTION_sleep_now:
      ACTION_set_sleep_timer: <minutes>         ACTION_empty_trash:
      ACTION_send_notification: <title> | <body>

    ACTION rules:
    - ACTION_kill_process payload = ONE integer PID from the data. NEVER a command string.
      Wrong: ACTION_kill_process: killall Chrome    Right: ACTION_kill_process: 10695
    - One process per action — use separate SUGGESTION + ACTION pairs for multiple.
    - Only reference values that appear VERBATIM in the data (PIDs, paths, names).
    - No ACTION line is better than a wrong one.

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
    json_mode: bool = True,
) -> str:
    messages: list[dict] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user})
    kwargs: dict = dict(model=model, messages=messages, temperature=0.1)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
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


def _parse_suggestions(text: str) -> tuple[list[str], list[str]]:
    """Parse SUGGESTION [RISK:X]: lines from plain-text LLM output."""
    suggestions: list[str] = []
    risks: list[str] = []
    for line in text.splitlines():
        m = re.match(r"SUGGESTION\s+\[RISK:(\w+)\]:\s*(.*)", line.strip())
        if m:
            risk = m.group(1).upper()
            if risk not in ("LOW", "MEDIUM", "HIGH"):
                risk = "LOW"
            text_part = m.group(2).strip()
            if text_part:
                suggestions.append(text_part)
                risks.append(risk)
    return suggestions, risks


def _trim_routing_table(table: str, max_ipv4: int = 10) -> str:
    """Reduce a verbose routing table to the entries useful for LLM diagnosis.

    Keeps: section headers, default gateway, host /32, and up to max_ipv4 other
    IPv4 entries. Drops the IPv6 block entirely — it adds tokens without aiding
    network diagnostics for typical user queries.
    """
    lines = table.splitlines()
    kept: list[str] = []
    ipv4_count = 0
    in_ipv6 = False
    for line in lines:
        stripped = line.strip()
        if stripped in ("Routing tables", "Internet:", "Internet6:"):
            if stripped == "Internet6:":
                in_ipv6 = True
            kept.append(line)
            continue
        if in_ipv6:
            continue
        if stripped.startswith("Destination"):
            kept.append(line)
            continue
        if line.startswith("default") or "/32" in line:
            kept.append(line)
            continue
        if ipv4_count < max_ipv4:
            kept.append(line)
            ipv4_count += 1
    header_prefixes = ("Routing", "Internet", "Destination")
    total = sum(1 for l in lines if l.strip() and not l.strip().startswith(header_prefixes))
    shown = sum(1 for l in kept if l.strip() and not l.strip().startswith(header_prefixes))
    if total > shown:
        kept.append(f"  … {total - shown} more entries omitted")
    return "\n".join(kept)


# ─────────────────────────────────────────────────────────────────────────────
# Fast-report helpers (zero-LLM structured parsing + scoring)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_cpu_table(text: str) -> list[tuple[str, float]]:
    """Parse fixed-width CPU process table → list of (name, cpu_pct)."""
    result = []
    for line in text.strip().splitlines()[1:]:
        if len(line) < 52:
            continue
        name = line[8:43].strip()
        try:
            result.append((name, float(line[43:51].strip())))
        except ValueError:
            pass
    return result


def _parse_ram_table(text: str) -> list[tuple[str, float]]:
    """Parse fixed-width RAM process table → list of (name, rss_mb)."""
    result = []
    for line in text.strip().splitlines()[1:]:
        if len(line) < 50:
            continue
        name = line[8:43].strip()
        try:
            rss_mb = float(line[43:57].strip().split()[0])
            result.append((name, rss_mb))
        except (ValueError, IndexError):
            pass
    return result


def _parse_energy_table(text: str) -> list[tuple[str, float]]:
    """Parse battery energy-consumer table → list of (name, cpu_pct)."""
    result = []
    for line in text.strip().splitlines()[1:]:
        if len(line) < 37:
            continue
        name = line[:35].strip()
        try:
            result.append((name, float(line[35:].strip().rstrip('%'))))
        except ValueError:
            pass
    return result


def _parse_memory_overview(text: str) -> tuple[float, float, float, float]:
    """Return (ram_used_gb, ram_total_gb, ram_pct, swap_pct) from memory_overview string."""
    ram_used = ram_total = ram_pct = swap_pct = 0.0
    for line in text.splitlines():
        m = re.search(r"RAM:.*?(\d+\.?\d*)\s*GB used / (\d+\.?\d*)\s*GB total.*?\((\d+\.?\d*)%\)", line)
        if m:
            ram_used, ram_total, ram_pct = float(m.group(1)), float(m.group(2)), float(m.group(3))
        m = re.search(r"Swap:.*?\((\d+\.?\d*)%\)", line)
        if m:
            swap_pct = float(m.group(1))
    return ram_used, ram_total, ram_pct, swap_pct


def _parse_disk_usage(volumes: str) -> tuple[float, float, float] | None:
    """Return (used_gb, total_gb, pct) for the first volume with data."""
    for line in volumes.strip().splitlines()[1:]:
        if len(line) < 53:
            continue
        try:
            total_gb = float(line[16:28].strip().split()[0])
            used_gb  = float(line[28:40].strip().split()[0])
            pct      = float(line[52:].strip().rstrip('%'))
            return used_gb, total_gb, pct
        except (ValueError, IndexError):
            pass
    return None


def _parse_battery_status(text: str) -> tuple[float | None, bool]:
    """Return (pct, is_charging) or (None, False) if no battery."""
    m = re.match(r"(\d+\.?\d*)\s*%", text or "")
    if not m:
        return None, False
    charging = "charging" in text.lower() and "not charging" not in text.lower()
    return float(m.group(1)), charging


def _parse_uptime_hours(text: str) -> float:
    """Parse '507h 18m since last boot' → 507.3."""
    m = re.match(r"(\d+)h\s+(\d+)m", text or "")
    return int(m.group(1)) + int(m.group(2)) / 60 if m else 0.0


def _score_cpu(cpu_pct: float) -> float:
    """0–10 impact score. 50 % CPU → 10."""
    return min(10.0, cpu_pct / 5.0)


def _score_ram(rss_mb: float, total_ram_mb: float) -> float:
    """0–10 impact score. 10 % of total RAM → 10."""
    if total_ram_mb <= 0:
        return 0.0
    return min(10.0, rss_mb / (total_ram_mb * 0.1))


def _impact_bar(score: float, width: int = 8) -> str:
    filled = round(score / 10.0 * width)
    return "█" * filled + "░" * (width - filled)


def _impact_level(score: float) -> str:
    return "HIGH" if score >= 7.0 else ("MEDIUM" if score >= 4.0 else "LOW")


def run_fast_report(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
    history: list[dict] | None = None,
) -> "AgentResult":
    """Instant health/battery/storage report — zero LLM calls.

    Collects data from all three collectors in parallel, parses the structured
    strings, computes per-process impact scores, and builds a rich Markdown
    report. Typical wall-clock time: 3–5 s (data collection only).
    """
    os_name = os_name or detect_os()

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_h = ex.submit(collect_health,  os_name)
        f_b = ex.submit(collect_battery, os_name)
        f_s = ex.submit(collect_storage, os_name)

    health  = f_h.result()
    battery = f_b.result()
    storage = f_s.result()

    cpu_procs    = _parse_cpu_table(health.top_cpu_procs    or "")
    ram_procs    = _parse_ram_table(health.top_mem_procs    or "")
    energy_procs = _parse_energy_table(battery.energy_consumers or "")
    ram_used, ram_total, ram_pct, swap_pct = _parse_memory_overview(health.memory_overview or "")
    total_ram_mb = ram_total * 1024
    disk_info    = _parse_disk_usage(storage.volumes or "")
    bat_pct, bat_charging = _parse_battery_status(battery.quick_status or "")
    uptime_h     = _parse_uptime_hours(health.uptime or "")

    # ── Overall health score (10 = perfect, 0 = maxed out) ──────────────────
    try:
        load1 = float((health.load_avg or "0").split("/")[0].split("(")[0].strip())
        cores = int((health.core_count or "1").split()[0])
        load_score = min(10.0, (load1 / max(1, cores)) * 10)
    except (ValueError, IndexError):
        load_score = 0.0
    stress = (load_score + ram_pct / 10.0 + swap_pct / 10.0) / 3.0
    health_score = round(max(0.0, 10.0 - stress), 1)
    health_label = "GOOD" if health_score >= 7 else ("MODERATE" if health_score >= 4 else "POOR")

    lines: list[str] = []

    # ── Header ───────────────────────────────────────────────────────────────
    uptime_str = f"{int(uptime_h)}h" if uptime_h else "unknown"
    lines.append("## System Health Report\n")
    lines.append(f"**Overall Status:** {health_label}  ·  Score: {health_score}/10  ·  Uptime: {uptime_str}\n")
    lines.append("---")

    # ── CPU ──────────────────────────────────────────────────────────────────
    load_display = (health.load_avg or "—").split("(")[0].strip()
    lines.append(f"\n### CPU  ·  Load: {load_display}\n")
    lines.append("| Impact | Process | CPU% | Risk |")
    lines.append("|--------|---------|------|------|")
    for name, cpu in cpu_procs[:8]:
        sc = _score_cpu(cpu)
        lines.append(f"| `{_impact_bar(sc)}` | {name} | {cpu:.1f}% | **{_impact_level(sc)}** |")

    # ── RAM ──────────────────────────────────────────────────────────────────
    ram_status = _impact_level(ram_pct / 10.0)
    lines.append(f"\n---\n\n### RAM  ·  {ram_used:.1f} GB / {ram_total:.1f} GB ({ram_pct:.1f}%)  ·  Swap: {swap_pct:.1f}%  ·  Risk: {ram_status}\n")
    lines.append("| Impact | Process | RAM | % of Total | Risk |")
    lines.append("|--------|---------|-----|-----------|------|")
    for name, rss_mb in ram_procs[:8]:
        sc = _score_ram(rss_mb, total_ram_mb)
        pct_of_total = (rss_mb / total_ram_mb * 100) if total_ram_mb else 0.0
        lines.append(f"| `{_impact_bar(sc)}` | {name} | {rss_mb:.0f} MB | {pct_of_total:.1f}% | **{_impact_level(sc)}** |")

    # ── Storage ──────────────────────────────────────────────────────────────
    lines.append("\n---\n\n### Storage\n")
    if disk_info:
        used_gb, total_gb, disk_pct = disk_info
        disk_risk = _impact_level(disk_pct / 10.0)
        lines.append(f"Disk: **{disk_pct:.1f}%** used — {used_gb:.1f} GB / {total_gb:.1f} GB  [{disk_risk}]")
    else:
        lines.append("_(no disk data)_")

    # ── Battery ──────────────────────────────────────────────────────────────
    lines.append("\n---\n\n### Battery\n")
    if bat_pct is not None:
        status_str = "Charging" if bat_charging else "Discharging"
        lines.append(f"**{bat_pct:.0f}%** ({status_str})")
        if energy_procs:
            lines.append("\nTop energy consumers:\n")
            for i, (name, cpu) in enumerate(energy_procs[:5], 1):
                sc = _score_cpu(cpu)
                lines.append(f"{i}. {name} — {cpu:.1f}% CPU [{_impact_level(sc)}]")
    else:
        lines.append("_(no battery / desktop system)_")

    # ── Recommendations ──────────────────────────────────────────────────────
    recs: list[tuple[str, str]] = []
    seen_names: set[str] = set()
    for name, cpu in cpu_procs[:5]:
        sc = _score_cpu(cpu)
        if sc >= 7 and name not in seen_names:
            recs.append(("HIGH",   f"Stop **{name}** ({cpu:.1f}% CPU) when not needed — it is continuously consuming CPU"))
            seen_names.add(name)
        elif sc >= 4 and name not in seen_names and len(recs) < 6:
            recs.append(("MEDIUM", f"Consider closing **{name}** ({cpu:.1f}% CPU) to reduce load"))
            seen_names.add(name)
    for name, rss_mb in ram_procs[:3]:
        sc = _score_ram(rss_mb, total_ram_mb)
        if sc >= 7 and name not in seen_names:
            recs.append(("HIGH",   f"**{name}** uses {rss_mb:.0f} MB RAM — close if not actively needed"))
            seen_names.add(name)
    if swap_pct > 80:
        recs.append(("MEDIUM", f"Swap at {swap_pct:.1f}% — restarting memory-heavy apps will relieve pressure"))
    if disk_info and disk_info[2] > 85:
        recs.append(("HIGH",   f"Disk at {disk_info[2]:.1f}% — free up space to avoid slowdowns"))
    elif disk_info and disk_info[2] > 70:
        recs.append(("LOW",    f"Disk at {disk_info[2]:.1f}% — consider cleaning up large files"))
    if bat_pct is not None and bat_pct < 20 and not bat_charging:
        recs.append(("HIGH",   "Battery below 20% — plug in charger"))
    if uptime_h > 168:
        recs.append(("LOW",    f"Running for {int(uptime_h)}h — a restart clears swap and refreshes system state"))
    elif uptime_h > 72:
        recs.append(("LOW",    f"Running for {int(uptime_h)}h — consider a restart to clear accumulated swap"))

    if recs:
        lines.append("\n---\n\n### Recommendations\n")
        for i, (risk, text) in enumerate(recs[:8], 1):
            lines.append(f"{i}. **[{risk}]** {text}")

    full_response = "\n".join(lines)

    return AgentResult(
        agent="FastReport",
        raw_data_summary=(
            f"Fast report — collected health + battery + storage in parallel\n"
            f"CPU processes: {len(cpu_procs)}  RAM processes: {len(ram_procs)}  "
            f"Battery: {f'{bat_pct:.0f}%' if bat_pct is not None else 'N/A'}  "
            f"Disk: {f'{disk_info[2]:.1f}%' if disk_info else 'N/A'}"
        ),
        analysis="",
        suggestions=[text for _, text in recs[:8]],
        risk_levels=[risk for risk, _ in recs[:8]],
        full_response=full_response,
    )


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
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="StorageAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(data),
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
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="BatteryAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(data),
        actions=actions,
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
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="HealthAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(data),
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

    llm_routing = _trim_routing_table(data.routing_table or "(no data)")

    llm_summary = textwrap.dedent(f"""\
        OS: {os_name}

        === NETWORK INTERFACES ===
        {data.interfaces or '(no data)'}

        === ROUTING TABLE (summarised) ===
        {llm_routing}

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
        {llm_summary}

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
    actions = parse_actions(llm_text, suggestions, risks)

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
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="StartupAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(data),
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
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="ActivityAgent",
        raw_data_summary=raw_summary,
        analysis=data.get("analysis", ""),
        suggestions=suggestions,
        risk_levels=risks,
        full_response=_format_full_response(data),
        actions=actions,
    )


# ─────────────────────────────────────────────────────────────────────────────
# File Agent
# ─────────────────────────────────────────────────────────────────────────────

_FILE_SYSTEM = textwrap.dedent("""\
    You are the FILE OPERATIONS AGENT. You execute file system tasks such as
    creating, writing, copying, moving, renaming, and deleting files and folders.

    For every task, break it into ordered steps. For EACH step write:
      SUGGESTION [RISK:<LOW|MEDIUM|HIGH>]: Step N — <plain description of what this step does>
      ACTION_<type>: <payload>

    Available action types and their payload format:
      ACTION_create_file: <path> | <text content>      ← creates file, writes content
      ACTION_copy_file:   <source> | <destination>     ← copies file or directory
      ACTION_move_file:   <source> | <destination>     ← moves or renames
      ACTION_delete_file: <path>                        ← deletes file or directory (HIGH)
      ACTION_mkdir:       <path>                        ← creates directory tree
      ACTION_open_file:   <path>                        ← opens file in default app
      ACTION_shell:       <command>                     ← any other shell operation

    Risk guide:
      LOW    — creating files, copying, making directories, opening files
      MEDIUM — overwriting existing files, moving, renaming
      HIGH   — deleting files or directories

    PATH RULES — use the exact paths provided in the context below.
    NEVER use paths outside the user's home directory unless explicitly asked.
    NEVER touch /System, /bin, /usr, /etc, /var, or any OS directory.
    NEVER delete files without HIGH risk label.

    After listing all steps, write a one-sentence summary of what was planned.
""")


def run_file_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
) -> AgentResult:
    os_name = os_name or detect_os()
    ctx: FileContextData = collect_file_context(os_name)

    raw_summary = textwrap.dedent(f"""\
        OS: {os_name}

        === PATH CONTEXT ===
        Home:      {ctx.home}
        Desktop:   {ctx.desktop}
        Downloads: {ctx.downloads}
        Documents: {ctx.documents}
        CWD:       {ctx.cwd}
    """)

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        {raw_summary}

        Plan and emit every step needed to complete this task.
        Use the exact paths from the context above.
        Each step must have a SUGGESTION line followed immediately by its ACTION line.
    """)

    llm_text = _llm_call(client, model, _FILE_SYSTEM, user_msg, json_mode=False)
    suggestions, risks = _parse_suggestions(llm_text)
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="FileAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
        actions=actions,
    )


# ─────────────────────────────────────────────────────────────────────────────
# System Settings Agent
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_SETTINGS_SYSTEM = textwrap.dedent("""\
    You are the SYSTEM SETTINGS AGENT. You execute system configuration changes:
    volume, brightness, Wi-Fi, Bluetooth, dark mode, Do Not Disturb, screen
    locking, sleep, trash, and desktop notifications.

    CRITICAL: Do NOT give manual instructions ("go to System Preferences...").
    For EVERY requested change, emit a SUGGESTION line + ACTION line pair.
    No exceptions — every step must be executable.

    For each change, write exactly:
      SUGGESTION [RISK:LOW]: Step N — <one sentence describing what this does>
      ACTION_<type>: <payload>

    Available action types and payload format:
      ACTION_set_volume: <0–100>
      ACTION_set_brightness: <0–100>
      ACTION_toggle_dark_mode:                     (no payload needed)
      ACTION_toggle_do_not_disturb: <on|off>
      ACTION_toggle_wifi: <on|off>
      ACTION_toggle_bluetooth: <on|off>
      ACTION_connect_wifi: <SSID> | <password>
      ACTION_disconnect_wifi:                      (no payload needed)
      ACTION_lock_screen:                          (no payload needed)
      ACTION_sleep_now:                            (no payload needed)
      ACTION_set_sleep_timer: <minutes>
      ACTION_empty_trash:                          (no payload needed)
      ACTION_send_notification: <title> | <body>
      ACTION_open_app: <exact app name>
      ACTION_shell: <command>

    Risk guide:
      LOW    — volume, brightness, dark mode, DND, lock screen, notification
      MEDIUM — sleep, disconnect Wi-Fi, sleep timer
      HIGH   — empty trash

    Execute ALL settings the user requested. List them in the order asked.
    After all steps, write one sentence summarising what was configured.
""")


def run_system_agent(
    user_prompt: str,
    client: OpenAI,
    model: str,
    os_name: str | None = None,
) -> AgentResult:
    os_name = os_name or detect_os()

    raw_summary = f"OS: {os_name}"

    user_msg = textwrap.dedent(f"""\
        The user asked: "{user_prompt}"

        OS: {os_name}

        Emit one SUGGESTION + ACTION pair for each setting change requested.
        Do not skip any. Do not give manual instructions.
    """)

    llm_text = _llm_call(client, model, _SYSTEM_SETTINGS_SYSTEM, user_msg,
                         json_mode=False)
    suggestions, risks = _parse_suggestions(llm_text)
    actions = parse_actions(llm_text, suggestions, risks)

    return AgentResult(
        agent="SystemAgent",
        raw_data_summary=raw_summary,
        analysis=llm_text,
        suggestions=suggestions,
        risk_levels=risks,
        full_response=llm_text,
        actions=actions,
    )
