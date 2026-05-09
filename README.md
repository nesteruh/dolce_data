# Dolce Data — Computer Assistant Agent System

A local, privacy-first multi-agent system that diagnoses your computer's health in plain language. Ask a question in natural language, and a specialist agent collects real system data, analyzes it with a local LLM, and gives you actionable suggestions — with a smarter judge model reviewing every answer before it reaches you.

Runs entirely on your machine. No cloud, no telemetry.

---

## Quick Summary

| What it does | How |
|---|---|
| Answers questions about storage, battery, CPU/RAM, network, and startup | 5 specialist agents, each with a focused system prompt |
| Collects real system data | `psutil`, `ioreg`, `pmset`, `top`, `system_profiler`, `upower`, PowerShell |
| Runs a local LLM for analysis | Ollama (default: `llama3.2`) via OpenAI-compatible API |
| Validates every response before display | LLM-as-a-Judge layer (`llama3.1:8b`) running in parallel |
| Blocks dangerous suggestions | Judge rejects anything touching system dirs, credential files, or forbidden paths |
| Renders a structured CLI | Rich panels — Raw Data · Assistant · Judge |

---

## Features

- **Natural-language diagnosis** — ask anything from "why is my disk full?" to "what's slowing my startup?"
- **Specialist agents** — each domain has its own prompt, data collector, and response format
- **LLM-as-a-Judge** — a second, smarter model audits every suggestion in parallel: safety, factuality, risk level, relevance, and quality
- **macOS Energy Impact** — battery agent collects `top -l 2 -o power` data (same source as Activity Monitor) and `pmset` drain history
- **Offline semantic search** — index and search local documents with ChromaDB + Ollama embeddings
- **Cross-platform** — macOS, Linux, and Windows (data collection adapts per OS)
- **Graceful fallbacks** — if the judge fails, the specialist's answer still displays; if a collector errors, it returns `(no data)` and continues

---

## Architecture

```
User prompt (CLI)
        │
        ▼
┌───────────────────┐
│   src/router.py   │  keyword classification → domain
│   RouterAgent     │  detects OS
└────────┬──────────┘
         │
    ┌────┴────────────────────────────────────┐
    │         Specialist Agents               │
    │  (src/agents.py + src/collectors.py)    │
    │                                         │
    │  StorageAgent  ──  collect_storage()    │
    │  BatteryAgent  ──  collect_battery()    │
    │  HealthAgent   ──  collect_health()     │
    │  NetworkAgent  ──  collect_network()    │
    │  StartupAgent  ──  collect_startup()    │
    └────────────────────┬────────────────────┘
                         │  AgentResult
                         ▼
         ┌───────────────────────────────┐
         │     src/judge.py              │
         │     LLM-as-a-Judge            │
         │                               │
         │  ThreadPoolExecutor:          │
         │  • 1 LLM call per suggestion  │  ← parallel
         │  • 1 holistic quality call    │  ← parallel
         └───────────────┬───────────────┘
                         │  JudgedResult
                         ▼
              ┌─────────────────────┐
              │     main.py CLI     │
              │                     │
              │  Raw Terminal Data  │  yellow panel
              │  Assistant          │  green panel
              │  LLM-as-a-Judge     │  blue panel
              └─────────────────────┘
```

### Data flow per query

1. **Router** classifies the prompt by keyword scoring → picks one domain
2. **Specialist agent** runs data collectors in parallel (`ThreadPoolExecutor`), builds a structured context, and calls the agent LLM (`llama3.2`, temperature 0.3)
3. **Parser** extracts `SUGGESTION [RISK:LOW|MEDIUM|HIGH]:` lines from the LLM response
4. **Judge** fires one LLM call per suggestion + one holistic call, all concurrently (`llama3.1:8b`, temperature 0.0)
5. **CLI** renders three panels: raw data, assistant answer, judge verdict

---

## Agents

| Agent | Trigger keywords | Data collected |
|---|---|---|
| **StorageAgent** | disk, space, cache, trash, full, clean | Volumes, largest dirs, caches, trash, safe-deletable items |
| **BatteryAgent** | battery, charge, drain, power, cycle | Battery status, cycle count, Energy Impact (`top`), drain history (`pmset log`), Bluetooth/Wi-Fi |
| **HealthAgent** | cpu, ram, memory, slow, lag, fan, hot | CPU/GPU/RAM usage, top processes, swap, uptime |
| **NetworkAgent** | network, internet, wifi, vpn, ping | Interfaces, active connections, listening ports, bandwidth, VPN detection |
| **StartupAgent** | startup, boot, login item, launch agent | Login items, launch agents/daemons, boot time |

---

## LLM-as-a-Judge

Every `AgentResult` passes through a judge before being displayed. The judge uses a more capable model (`llama3.1:8b` by default) and evaluates suggestions in parallel.

**What the judge checks per suggestion:**

| Check | Action |
|---|---|
| Forbidden path (system dirs, `~/.ssh`, `~/.aws`, etc.) | Block + red panel |
| Factuality (cited path/size not in raw data) | Warn + yellow panel |
| Risk level too low for the action described | Escalate risk level |

**Holistic checks (one concurrent call):**

| Check | Action |
|---|---|
| Router classified wrong domain | Yellow warning |
| Response doesn't answer the question | Yellow warning |
| Overall quality POOR | Yellow warning |

**Forbidden paths (never approved):**

- System: `/System`, `/bin`, `/sbin`, `/usr/bin`, `/etc`, `/var`, `C:\Windows\System32`
- User-critical: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.kube`, `~/.config`, `~/Library/Keychains`, `~/.gitconfig`, `~/.zshrc`

The judge degrades gracefully — if it fails entirely, a passthrough verdict is used and the specialist's answer is shown unchanged.

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

---

## Project Structure

```
dolce_data/
├── main.py                        # Interactive CLI entry point
├── requirements.txt
├── .env.example                   # Configuration template
├── src/
│   ├── agents.py                  # 5 specialist agents + shared LLM helpers
│   ├── router.py                  # Keyword classifier + judge integration
│   ├── collectors.py              # System data collection (psutil, subprocess)
│   ├── judge.py                   # LLM-as-a-Judge (parallel evaluation)
│   ├── client.py                  # Ollama/OpenAI client setup
│   └── command_registry.py        # Markdown-defined command catalog with risk gates
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

**Prerequisites:** Python 3.10+, [Ollama](https://ollama.com) running locally.

```bash
# 1. Clone and install dependencies
git clone <repo-url>
cd dolce_data
pip install -r requirements.txt

# 2. Pull the required models
ollama pull llama3.2        # specialist agent model
ollama pull llama3.1:8b     # judge model

# 3. Configure
cp .env.example .env
# Edit .env if needed (defaults work out of the box with Ollama on localhost)

# 4. Run
python main.py
```

---

## Configuration

All configuration is via environment variables (`.env` file or shell exports).

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API endpoint |
| `OLLAMA_API_KEY` | `ollama` | API key (Ollama ignores this, required by OpenAI SDK) |
| `AGENT_MODEL` | `llama3.2` | Model used by specialist agents |
| `JUDGE_MODEL` | `llama3.1:8b` | Model used by the judge (should be equal or smarter than `AGENT_MODEL`) |

To use a more capable judge, pull a larger model and set `JUDGE_MODEL`:

```bash
ollama pull llama3.3:70b
JUDGE_MODEL=llama3.3:70b python main.py
```

---

## Usage

```
python main.py
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

Each query produces three panels:

| Panel | Color | Content |
|---|---|---|
| Raw Terminal Data | Yellow | All system data collected for this query |
| Assistant | Green | LLM analysis + suggestions |
| LLM-as-a-Judge | Blue | Per-suggestion verdicts, risk levels, quality checks |

Type `quit` or `exit` to leave.

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

---

## Safety Model

The system has four independent safety layers:

1. **Forbidden command list** — `agents/shared/commands/*.md` marks OS commands as `RISK: FORBIDDEN`; the `CommandRegistry` (`src/command_registry.py`) refuses to execute them.
2. **Risk gates** — `MEDIUM`/`HIGH` risk commands require explicit `confirmed=True`; raises `RuntimeError` otherwise.
3. **Agent system prompts** — each specialist is instructed to only reference data that appears verbatim in the collected output, preventing hallucinated paths or sizes.
4. **LLM-as-a-Judge** — post-response validation blocks any suggestion that references forbidden paths, fabricated values, or under-classified risks before the user sees it.
