"""
Command Registry
================
Parses the OS-specific markdown command files in agents/shared/commands/
and provides a unified interface to look up and execute commands by CMD_ID.

This makes the markdown command files the single source of truth for:
  - Agent instruction files  (reference by CMD_ID)
  - Python runtime           (this module executes by CMD_ID)

Usage:
    from src.command_registry import CommandRegistry

    registry = CommandRegistry()           # auto-detects OS
    result = registry.run("health.cpu_overview")
    result = registry.run("health.graceful_kill", pid=1234)
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CommandEntry:
    cmd_id: str
    purpose: str
    risk: str            # NONE | MEDIUM | HIGH | FORBIDDEN
    requires: str
    command: str         # raw command string (may contain <param> placeholders)


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────

# Matches:  ### CMD_ID: `storage.disk_overview`
_CMD_HEADER = re.compile(r"###\s+CMD_ID:\s+`([^`]+)`")

# Matches:  - **Purpose**: some text
_FIELD = re.compile(r"-\s+\*\*(\w+)\*\*:\s+(.*)")

# Matches a fenced code block (bash, powershell, or plain)
_CODE_BLOCK = re.compile(r"```(?:bash|powershell|sh|zsh|)?\s*\n(.*?)```", re.DOTALL)


def _parse_command_file(path: Path) -> dict[str, CommandEntry]:
    """
    Parse a markdown command file and return {cmd_id: CommandEntry}.
    Only entries whose CMD_ID does NOT start with 'forbidden.' are included.
    """
    text = path.read_text(encoding="utf-8")
    entries: dict[str, CommandEntry] = {}

    # Split into sections at each ### CMD_ID: header
    sections = re.split(r"(?=###\s+CMD_ID:)", text)

    for section in sections:
        header_match = _CMD_HEADER.search(section)
        if not header_match:
            continue

        cmd_id = header_match.group(1).strip()
        if cmd_id.startswith("forbidden."):
            continue  # Never register forbidden commands

        # Extract fields
        fields: dict[str, str] = {}
        for m in _FIELD.finditer(section):
            fields[m.group(1).lower()] = m.group(2).strip()

        # Extract command body from the first code block in this section
        code_match = _CODE_BLOCK.search(section)
        raw_command = code_match.group(1).strip() if code_match else ""

        # Strip leading comment lines (lines starting with #)
        command_lines = [
            ln for ln in raw_command.splitlines()
            if not ln.strip().startswith("#")
        ]
        command = "\n".join(command_lines).strip()

        entries[cmd_id] = CommandEntry(
            cmd_id=cmd_id,
            purpose=fields.get("purpose", ""),
            risk=fields.get("risk", "NONE").upper(),
            requires=fields.get("requires", ""),
            command=command,
        )

    return entries


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

_COMMANDS_DIR = Path(__file__).parent.parent / "agents" / "shared" / "commands"

_OS_FILE_MAP = {
    "macos":   _COMMANDS_DIR / "macos_commands.md",
    "linux":   _COMMANDS_DIR / "linux_commands.md",
    "windows": _COMMANDS_DIR / "windows_commands.md",
}


def _detect_os() -> str:
    p = sys.platform
    if p == "darwin":
        return "macos"
    if p.startswith("linux"):
        return "linux"
    if p == "win32":
        return "windows"
    return "unknown"


class CommandRegistry:
    """
    Loads the OS-appropriate command file on construction.
    Provides .get(), .run(), and .list() methods.
    """

    def __init__(self, os_name: str | None = None) -> None:
        self.os_name = os_name or _detect_os()
        md_path = _OS_FILE_MAP.get(self.os_name)
        if md_path is None or not md_path.exists():
            raise FileNotFoundError(
                f"No command file found for OS '{self.os_name}' at {md_path}"
            )
        self._commands: dict[str, CommandEntry] = _parse_command_file(md_path)

    # ── Lookup ──────────────────────────────────────────────────────────────

    def get(self, cmd_id: str) -> CommandEntry | None:
        """Return the CommandEntry for a CMD_ID, or None if not found."""
        return self._commands.get(cmd_id)

    def list(self, prefix: str = "") -> list[str]:
        """Return all CMD_IDs, optionally filtered by a domain prefix."""
        return [k for k in self._commands if k.startswith(prefix)]

    # ── Execution ───────────────────────────────────────────────────────────

    def run(
        self,
        cmd_id: str,
        timeout: int = 15,
        **params: str | int,
    ) -> str:
        """
        Look up cmd_id, substitute any <param> placeholders, and execute.

        Parameters
        ----------
        cmd_id  : CMD_ID string, e.g. 'health.top_cpu_procs'
        timeout : seconds before the subprocess is killed
        **params: keyword args that replace <param_name> in the command.
                  E.g.  registry.run("health.graceful_kill", pid=1234)

        Returns
        -------
        stdout string, or an error message prefixed with 'ERROR:'.

        Raises
        ------
        PermissionError  if the command risk is FORBIDDEN
        ValueError       if the CMD_ID is not found
        RuntimeError     if the risk is MEDIUM/HIGH and confirmed=True was
                         not passed as a param (safety guard)
        """
        entry = self._commands.get(cmd_id)
        if entry is None:
            raise ValueError(f"CMD_ID '{cmd_id}' not found in registry for OS '{self.os_name}'")

        if entry.risk == "FORBIDDEN":
            raise PermissionError(
                f"CMD_ID '{cmd_id}' is classified FORBIDDEN and cannot be executed."
            )

        # Safety guard: MEDIUM / HIGH commands require explicit confirmation flag
        if entry.risk in ("MEDIUM", "HIGH") and not params.get("confirmed"):
            raise RuntimeError(
                f"CMD_ID '{cmd_id}' has risk={entry.risk}. "
                "Pass confirmed=True only after obtaining explicit user approval."
            )

        # Substitute <placeholder> tokens in the command string
        command = entry.command
        for key, value in params.items():
            if key == "confirmed":
                continue
            command = command.replace(f"<{key}>", str(value))

        # Check for unresolved placeholders
        unresolved = re.findall(r"<\w+>", command)
        if unresolved:
            raise ValueError(
                f"CMD_ID '{cmd_id}' has unresolved placeholders: {unresolved}. "
                f"Provide them as keyword arguments."
            )

        return _shell_run(command, timeout=timeout)

    # ── Introspection ────────────────────────────────────────────────────────

    def describe(self, cmd_id: str) -> str:
        """Return a human-readable description of a command (no execution)."""
        entry = self._commands.get(cmd_id)
        if entry is None:
            return f"CMD_ID '{cmd_id}' not found."
        return (
            f"CMD_ID : {entry.cmd_id}\n"
            f"Purpose: {entry.purpose}\n"
            f"Risk   : {entry.risk}\n"
            f"Needs  : {entry.requires}\n"
            f"Command:\n{entry.command}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Internal shell runner
# ─────────────────────────────────────────────────────────────────────────────

def _shell_run(command: str, timeout: int = 15) -> str:
    """Execute a shell command string; return stdout or 'ERROR: ...' on failure."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout.strip()
        if result.returncode != 0 and not output:
            return f"ERROR: {result.stderr.strip() or 'command exited with code ' + str(result.returncode)}"
        return output
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"
    except Exception as exc:
        return f"ERROR: {exc}"
