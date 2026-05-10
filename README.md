# Dolce Data — Computer Assistant Agent System

A local, privacy-first multi-agent system that diagnoses your computer's health in plain language. Ask a question in natural language, and specialist agents collect real system data, analyze it with a local LLM, and give you actionable suggestions you can execute with one click.

Runs entirely on your machine. No cloud, no telemetry.

---

## Quick Summary

| What it does | How |
|---|---|
| Answers questions about storage, battery, CPU/RAM, network, startup, and more | 8 specialist agents, each with a focused system prompt |
| Collects real system data | `psutil`, `ioreg`, `pmset`, `top`, `system_profiler`, `upower`, PowerShell |
| Runs a local LLM for analysis | Ollama (default: `llama3.2`) via OpenAI-compatible API |
| Executes suggested actions | In-UI action buttons with risk confirmation for HIGH/CRITICAL operations |
| Accepts voice input | Spotlight-style UI with faster-whisper local STT (auto-submits on stop) |
| Renders a structured CLI | Rich panels — Raw Data · Assistant |

---

## Features

- **Natural-language diagnosis** — ask anything from "why is my disk full?" to "what's slowing my startup?"
- **8 specialist agents** — each domain has its own prompt, data collector, and response format
- **Spotlight-style UI** — frameless floating window (`app_ui.py`) with streamed token output
- **Voice input** — click the mic, speak, click stop; transcription runs locally with faster-whisper and the prompt executes automatically
- **In-UI action execution** — suggested actions appear as clickable buttons with risk labels; HIGH/CRITICAL actions require confirmation
- **macOS Energy Impact** — battery agent collects `top -l 2 -o power` data (same source as Activity Monitor) and `pmset` drain history
- **Offline semantic search** — index and search local documents with ChromaDB + Ollama embeddings
- **Query history** — all queries and suggestions are logged to `logs/history.jsonl`
- **Cross-platform** — macOS, Linux, and Windows (data collection adapts per OS)
- **Graceful fallbacks** — if a collector errors it returns `(no data)` and continues; if a component fails, the specialist's answer still displays

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   User Input                    │
│                                                 │
│   Text prompt          Voice (mic recording)    │
│   via CLI  ──────────────────────  via UI       │
│                           │                     │
│                    Local transcription          │
│                    (offline STT model)          │
└───────────────────────────┬─────────────────────┘
                            │ natural-language query
                            ▼
              ┌─────────────────────────┐
              │         Router          │
              │                         │
              │  Reads the query and    │
              │  decides which domain   │
              │  (or domains) it covers │
              │  based on keywords      │
              └────────────┬────────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
   ┌────────────┐  ┌────────────┐  ┌────────────┐
   │ Specialist │  │ Specialist │  │ Specialist │  ...up to 8 domains
   │   Agent    │  │   Agent    │  │   Agent    │
   │            │  │            │  │            │
   │  Collects  │  │  Collects  │  │  Collects  │
   │  live data │  │  live data │  │  live data │
   │  from OS   │  │  from OS   │  │  from OS   │
   └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
         │               │               │
         └───────────────┼───────────────┘
                         │ raw system data
                         ▼
            ┌────────────────────────┐
            │      Local LLM         │
            │                        │
            │  Receives system data  │
            │  + user question,      │
            │  produces analysis,    │
            │  suggestions & actions │
            └───────────┬────────────┘
                        │
                        ▼
          ┌─────────────────────────────┐
          │          Output             │
          │                             │
          │  Raw system data collected  │
          │  Natural-language answer    │
          │  Suggested actions with     │
          │  risk level (LOW / HIGH)    │
          │  One-click action execution │
          └─────────────────────────────┘
```

### How a query flows

1. **User sends a question** — typed in the CLI or spoken via the microphone in the UI (transcribed locally, no cloud)
2. **Router reads the question** and routes it to one or more specialist domains in parallel
3. **Each specialist agent collects live data** from the operating system relevant to its domain (disk, battery, CPU, network, etc.)
4. **The local LLM receives** the collected data alongside the original question and produces a plain-language analysis with concrete suggestions
5. **Output is rendered** — raw data for transparency, the assistant's answer, and (in the UI) clickable action buttons that execute the suggestions directly on the machine with risk confirmation for dangerous operations

---

## Agents

| Agent | Trigger keywords | Data collected |
|---|---|---|
| **StorageAgent** | disk, space, cache, trash, full, clean | Volumes, largest dirs, caches, trash, safe-deletable items |
| **BatteryAgent** | battery, charge, drain, power, cycle | Battery status, cycle count, Energy Impact (`top`), drain history (`pmset log`), Bluetooth/Wi-Fi |
| **HealthAgent** | cpu, ram, memory, slow, lag, fan, hot | CPU/GPU/RAM usage, top processes, swap, uptime |
| **NetworkAgent** | network, internet, wifi, vpn, ping | Interfaces, active connections, listening ports, bandwidth, VPN detection |
| **StartupAgent** | startup, boot, login item, launch agent | Login items, launch agents/daemons, boot time |
| **ActivityAgent** | activity, process, running, background | Running processes, resource usage, background tasks |
| **FileAgent** | file, find, locate, open, recent | File search and metadata |
| **SystemAgent** | system, info, specs, hardware, version | OS version, hardware specs, system information |

---

## UI — Spotlight-style Window

Launch with:

```bash
python app_ui.py
```

| Feature | Details |
|---|---|
| Keyboard | `Return` to submit, `Escape` to collapse/hide |
| Voice input | Click 🎙 to start recording, click 🔴 to stop — transcription runs locally and submits automatically |
| Streaming | LLM tokens stream live into the output area |
| Action buttons | Suggested actions appear as buttons with color-coded risk labels (LOW / MEDIUM / HIGH) |
| Risk confirmation | HIGH and CRITICAL actions show a confirmation dialog before executing |

Voice uses faster-whisper (`base` model, CPU, greedy decoding) — no API calls, fully offline.

---

## Semantic Search (optional)

A separate subsystem indexes local documents and answers questions using vector similarity.

```
agents/search/
├── main.py          # CLI: index a directory or run a query
├── config.py        # DB path, chunk size, Ollama embedding model
├── core/
│   ├── extractor.py # Text extraction from .txt, .pdf, .docx
│   ├── chunker.py   # 500-char sliding-window chunking
│   └── vector_store.py  # ChromaDB wrapper (Ollama embeddings: nomic-embed-text)
├── indexing/
│   └── indexer.py   # Directory traversal + upsert pipeline
└── retrieval/
    └── searcher.py  # Semantic search + deduplication
```

```bash
# Index a directory
python agents/search/main.py index --dir "/path/to/docs"

# Search indexed documents
python agents/search/main.py search --query "your question"
```

---

## Project Structure

```
dolce_data/
├── main.py                        # Interactive CLI entry point
├── app_ui.py                      # Spotlight-style PyQt6 UI launcher
├── requirements.txt
├── .env.example                   # Configuration template
├── logs/
│   └── history.jsonl              # Query history (JSONL)
├── src/
│   ├── agents.py                  # 8 specialist agents + shared LLM helpers
│   ├── router.py                  # Keyword classifier + multi-domain orchestration
│   ├── collectors.py              # System data collection (psutil, subprocess)
│   ├── actions.py                 # Action registry and executor (shell, file, app, system)
│   ├── command_registry.py        # Markdown-defined command catalog with risk gates
│   ├── client.py                  # Ollama/OpenAI client setup
│   ├── history.py                 # JSONL conversation logger
│   ├── judge.py                   # LLM-as-a-Judge (disabled, preserved for future use)
│   └── ui.py                      # PyQt6 Spotlight UI (voice, streaming, action buttons)
└── agents/
    ├── router/router_agent.md
    ├── storage/storage_agent.md
    ├── battery/battery_agent.md
    ├── health/health_agent.md
    ├── network/network_agent.md
    ├── startup/startup_agent.md
    ├── search/                    # Semantic search subsystem
    └── shared/
        ├── general_rules.md       # Global forbidden operations + risk levels
        └── commands/
            ├── macos_commands.md
            ├── linux_commands.md
            └── windows_commands.md
```

---

## Installation

**Prerequisites:** Python 3.10+, [Ollama](https://ollama.com) running locally, PyQt6 for the GUI.

```bash
# 1. Clone and install dependencies
git clone <repo-url>
cd dolce_data
pip install -r requirements.txt
pip install PyQt6          # required for the Spotlight UI only

# 2. Pull the required model
ollama pull llama3.2

# 3. Configure (optional — defaults work with local Ollama)
cp .env.example .env

# 4a. Run the CLI
python main.py

# 4b. Run the Spotlight UI
python app_ui.py
```

---

## Configuration

All configuration is via environment variables (`.env` file or shell exports).

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API endpoint |
| `OLLAMA_API_KEY` | `ollama` | API key (Ollama ignores this, required by OpenAI SDK) |
| `AGENT_MODEL` | `llama3.2` | Model used by specialist agents |
| `HISTORY_FILE` | `logs/history.jsonl` | Query history location |

---

## Usage

### CLI

```bash
python main.py
```

Type `quit`, `exit`, or `q` to leave.

### Spotlight UI

```bash
python app_ui.py
```

Example questions:

```
Why is my battery draining so fast?
What is eating up my disk space?
Which apps are using the most memory?
My fan is very loud — what's happening?
Why is my internet connection so slow?
What programs start automatically when I log in?
I need at least 50 GB free — help me find it.
```

CLI output panels:

| Panel | Color | Content |
|---|---|---|
| Raw Terminal Data | Yellow | All system data collected for this query |
| Assistant | Green | LLM analysis + suggestions |

---

## Dependencies

| Package | Purpose |
|---|---|
| `openai` | OpenAI-compatible SDK used to call Ollama |
| `python-dotenv` | Load `.env` configuration |
| `psutil` | Cross-platform system metrics (CPU, RAM, disk, battery, network, processes) |
| `rich` | Terminal UI — panels, markdown rendering, spinner, prompt |
| `chromadb` | Local vector store for semantic search (SQLite backend) |
| `ollama` | Ollama Python client (used for embedding model in search subsystem) |
| `markitdown` | Markdown text extraction |
| `PyMuPDF` | PDF text extraction for semantic search indexing |
| `python-docx` | DOCX text extraction for semantic search indexing |
| `sounddevice` | Microphone input (UI voice feature) |
| `soundfile` | Audio file I/O (UI voice feature) |
| `faster-whisper` | Local speech-to-text — no API calls, runs fully offline |
| `PyQt6` | Spotlight-style UI (install separately: `pip install PyQt6`) |

---

## Safety Model

The system has three independent safety layers:

1. **Forbidden command list** — `agents/shared/commands/*.md` marks OS commands as `RISK: FORBIDDEN`; the `CommandRegistry` (`src/command_registry.py`) refuses to execute them.
2. **Risk gates** — `MEDIUM`/`HIGH` risk commands require explicit `confirmed=True`; the UI shows a confirmation dialog.
3. **Agent system prompts** — each specialist is instructed to only reference data that appears verbatim in the collected output, preventing hallucinated paths or sizes.

**Forbidden paths (never approved):**

- System: `/System`, `/bin`, `/sbin`, `/usr/bin`, `/etc`, `/var`, `C:\Windows\System32`
- User-critical: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.kube`, `~/.config`, `~/Library/Keychains`, `~/.gitconfig`, `~/.zshrc`

> **LLM-as-a-Judge** (`src/judge.py`) is implemented but currently disabled. It provides post-response validation — blocking suggestions that reference forbidden paths, fabricated values, or under-classified risks. It can be re-enabled by wiring it back into `main.py` and `router.py`.
