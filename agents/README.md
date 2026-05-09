# 🤖 Agent System — Dolce Data Computer Assistant

This directory contains all instruction files for the multi-agent computer assistant system.

## Architecture

```
agents/
├── README.md                        # This file — system overview
├── router/
│   └── router_agent.md              # Router/orchestrator agent instructions
├── storage/
│   └── storage_agent.md             # Storage management agent instructions
├── battery/
│   └── battery_agent.md             # Battery health agent instructions
├── health/
│   └── health_agent.md              # CPU/GPU/RAM optimization agent instructions
├── shared/
│   ├── general_rules.md             # Global rules and forbidden operations
│   └── commands/
│       ├── macos_commands.md        # macOS command reference
│       ├── linux_commands.md        # Linux command reference
│       └── windows_commands.md      # Windows command reference
```

## Agent Responsibilities

| Agent          | Trigger Topics                                              |
|----------------|-------------------------------------------------------------|
| **Router**     | All prompts — routes to the correct specialist agent        |
| **Storage**    | Disk space, caches, large files, storage cleanup            |
| **Battery**    | Battery drain, charging issues, power-hungry apps           |
| **Health**     | CPU/GPU/RAM usage, slow performance, resource hogs          |

## Ground Rules

- Every agent **must** load `shared/general_rules.md` before acting.
- Every agent **must** select the OS-appropriate command file from `shared/commands/`.
- No agent may execute a destructive command without explicit user confirmation.
