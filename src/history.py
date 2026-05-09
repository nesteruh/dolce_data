"""
Conversation history logger.

Appends one JSON record per query to logs/history.jsonl (JSONL format).
Each record captures the timestamp, user question, agent response, and the
full LLM-as-a-Judge evaluation so every session is auditable and searchable.

The file path is configurable via the HISTORY_FILE env var.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from src.judge import JudgedResult

DEFAULT_HISTORY_FILE = "logs/history.jsonl"


def save_entry(
    user_prompt: str,
    judged: JudgedResult,
    history_file: str | None = None,
) -> None:
    """
    Append one JSONL record to the history file.
    Never raises — any I/O failure is silently ignored so the CLI keeps running.
    """
    try:
        _write_entry(user_prompt, judged, history_file or os.getenv("HISTORY_FILE", DEFAULT_HISTORY_FILE))
    except Exception:
        pass


def _write_entry(user_prompt: str, judged: JudgedResult, path: str) -> None:
    ar = judged.agent_result
    verdict = judged.verdict

    # Extract OS name from the first "OS: ..." line in raw_data_summary
    os_name = "unknown"
    for line in ar.raw_data_summary.splitlines():
        if line.strip().startswith("OS:"):
            os_name = line.strip().removeprefix("OS:").strip()
            break

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": user_prompt,
        "domain": judged.domain,
        "agent": ar.agent,
        "os": os_name,
        "assistant": {
            "response": ar.full_response,
            "suggestions": [
                {"text": text, "risk": risk}
                for text, risk in zip(ar.suggestions, ar.risk_levels)
            ],
        },
        "judge": {
            "model": verdict.judge_model,
            "score": verdict.score,
            "score_reason": verdict.score_reason,
            "score_grounding": verdict.score_grounding,
            "score_specificity": verdict.score_specificity,
            "score_relevance": verdict.score_relevance,
            "answer_blocked": verdict.answer_blocked,
            "router_domain_correct": verdict.router_domain_correct,
            "router_note": verdict.router_note,
            "judge_failed": verdict.judge_failed,
            "failure_reason": verdict.failure_reason,
            "blocked_count": sum(1 for v in verdict.verdicts if not v.approved),
            "suggestions": [
                {
                    "text": ar.suggestions[v.index],
                    "original_risk": ar.risk_levels[v.index],
                    "approved": v.approved,
                    "adjusted_risk": v.adjusted_risk,
                    "block_reason": v.block_reason,
                    "factual": v.factual,
                    "factuality_note": v.factuality_note,
                }
                for v in verdict.verdicts
            ],
        },
    }

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
