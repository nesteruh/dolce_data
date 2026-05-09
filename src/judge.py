"""
LLM-as-a-Judge safety and quality layer.

Each suggestion from a specialist agent is evaluated concurrently by a smarter
judge model (default: llama3.1:8b). A separate concurrent call evaluates overall
response quality, router accuracy, and relevance. All judge calls are fire-and-forget
with a passthrough fallback so the main pipeline never crashes.
"""

from __future__ import annotations

import json
import textwrap
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from openai import OpenAI

from src.agents import AgentResult


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SuggestionVerdict:
    index: int
    approved: bool
    adjusted_risk: str   # may be escalated above the original; never lowered
    block_reason: str    # empty string if approved
    factual: bool        # False if cited paths/sizes not found verbatim in raw data
    factuality_note: str # explanation when factual=False


@dataclass
class JudgeVerdict:
    verdicts: list[SuggestionVerdict]
    router_domain_correct: bool
    router_note: str
    response_relevant: bool
    relevance_note: str
    overall_quality: str   # GOOD | ACCEPTABLE | POOR
    quality_note: str
    judge_model: str
    judge_failed: bool = False
    failure_reason: str = ""


@dataclass
class JudgedResult:
    agent_result: AgentResult
    verdict: JudgeVerdict
    domain: str = ""   # router domain: storage | battery | health | network | startup

    @property
    def approved_suggestions(self) -> list[tuple[str, str]]:
        """List of (suggestion_text, adjusted_risk) for approved suggestions."""
        return [
            (self.agent_result.suggestions[v.index], v.adjusted_risk)
            for v in self.verdict.verdicts
            if v.approved
        ]

    @property
    def blocked_suggestions(self) -> list[tuple[str, str]]:
        """List of (suggestion_text, block_reason) for blocked suggestions."""
        return [
            (self.agent_result.suggestions[v.index], v.block_reason)
            for v in self.verdict.verdicts
            if not v.approved
        ]

    @property
    def has_blocks(self) -> bool:
        return any(not v.approved for v in self.verdict.verdicts)

    @property
    def has_warnings(self) -> bool:
        risk_escalated = any(
            v.approved and v.adjusted_risk != self.agent_result.risk_levels[v.index]
            for v in self.verdict.verdicts
        )
        factuality_issues = any(
            v.approved and not v.factual
            for v in self.verdict.verdicts
        )
        return (
            risk_escalated
            or factuality_issues
            or not self.verdict.router_domain_correct
            or not self.verdict.response_relevant
            or self.verdict.overall_quality == "POOR"
            or self.verdict.judge_failed
        )


# ─────────────────────────────────────────────────────────────────────────────
# System prompts
# ─────────────────────────────────────────────────────────────────────────────

_SUGGESTION_SYSTEM = textwrap.dedent("""\
    You are a safety auditor for a computer diagnostic assistant.
    Evaluate ONLY the single suggestion provided. Respond with ONLY valid JSON — no other text.

    === SAFE TO DELETE OVERRIDE (HIGHEST PRIORITY) ===
    The RAW SYSTEM DATA contains a section called "SAFE TO DELETE". Any path that
    appears VERBATIM in that section has already been verified as safe by the system.
    You MUST approve suggestions that target ONLY paths listed in "SAFE TO DELETE".
    Do NOT block these suggestions — they are pre-approved regardless of their path prefix.
    Examples of safe paths that must NOT be blocked if they appear in SAFE TO DELETE:
      ~/Library/Caches  ~/Library/Logs  ~/Library/Saved Application State
      ~/.npm  ~/.docker  ~/.gradle  ~/.pip  ~/.cache  /tmp  /var/tmp

    === FORBIDDEN PATHS ===
    Block suggestions that target paths NOT in "SAFE TO DELETE" AND that touch:

    System directories:
      /System  /bin  /sbin  /usr/bin  /usr/lib  /usr/sbin  /etc  /var  /boot
      /lib  /lib64  /proc  /sys  /private/etc  /private/var
      C:\\Windows  C:\\Windows\\System32  C:\\Windows\\SysWOW64

    User-critical credential and config paths (block even if in SAFE TO DELETE):
      ~/.ssh  ~/.gnupg  ~/.aws  ~/.kube
      ~/Library/Keychains  ~/Library/Preferences/com.apple.*
      ~/.netrc  ~/.gitconfig  ~/.zshrc  ~/.bashrc  ~/.bash_profile  ~/.profile

    Block bulk deletions of entire home directory (rm -rf ~/ or equivalent).

    === FACTUALITY RULE ===
    A suggestion is factual ONLY if every path, filename, process name, and numeric
    size it mentions appears verbatim in the RAW SYSTEM DATA provided below.
    If the suggestion invents any path or value, mark factual as false.

    === RISK ESCALATION RULE ===
    - Escalate adjusted_risk to CRITICAL and set approved=false for forbidden paths above.
    - Escalate to HIGH if the suggestion involves deleting files NOT in SAFE TO DELETE.
    - Suggestions targeting SAFE TO DELETE paths keep their original risk level.
    - Never lower the risk level below what the agent assigned.

    === OUTPUT FORMAT ===
    Respond with ONLY this JSON object, nothing else:
    {"approved": true, "adjusted_risk": "LOW", "block_reason": "", "factual": true, "factuality_note": ""}

    Use approved=false and fill block_reason when blocking a suggestion.
    Use factual=false and fill factuality_note when a cited value is not in the raw data.
""")

_HOLISTIC_SYSTEM = textwrap.dedent("""\
    You are a quality auditor for a computer diagnostic assistant.
    Evaluate the agent's response against three criteria. Respond with ONLY valid JSON.

    1. ROUTER ACCURACY: Did the router correctly classify the user's question into the
       given domain? (storage / battery / health / network / startup)

    2. RESPONSE RELEVANCE: Does the agent's full response actually answer what the user asked?
       A response that lists generic tips without addressing the specific question is not relevant.

    3. OVERALL QUALITY: Rate the response.
       - GOOD: directly answers the question, suggestions are specific and actionable
       - ACCEPTABLE: mostly answers the question but is vague or incomplete
       - POOR: off-topic, generic, or fails to address the user's actual situation

    === OUTPUT FORMAT ===
    Respond with ONLY this JSON object, nothing else:
    {"router_domain_correct": true, "router_note": "", "response_relevant": true, "relevance_note": "", "overall_quality": "GOOD", "quality_note": ""}
""")


# ─────────────────────────────────────────────────────────────────────────────
# Judge class
# ─────────────────────────────────────────────────────────────────────────────

class Judge:
    def __init__(self, client: OpenAI, model: str, max_workers: int = 6) -> None:
        self.client = client
        self.model = model
        self.max_workers = max_workers

    def evaluate(
        self,
        result: AgentResult,
        user_prompt: str,
        domain: str,
    ) -> JudgeVerdict:
        """
        Evaluate all suggestions in parallel plus one holistic call.
        Returns a passthrough verdict (all approved) if anything goes wrong
        so the main pipeline never crashes.
        """
        try:
            n = len(result.suggestions)

            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                sug_futures: list[Future[SuggestionVerdict]] = [
                    pool.submit(
                        self._judge_single_suggestion,
                        i,
                        sug,
                        risk,
                        result.raw_data_summary,
                        user_prompt,
                    )
                    for i, (sug, risk) in enumerate(
                        zip(result.suggestions, result.risk_levels)
                    )
                ]
                holistic_future: Future[tuple] = pool.submit(
                    self._judge_holistic, result, user_prompt, domain
                )

            verdicts = [f.result() for f in sug_futures]
            router_ok, router_note, relevant, rel_note, quality, q_note, holistic_failed, holistic_reason = (
                holistic_future.result()
            )

            return JudgeVerdict(
                verdicts=verdicts,
                router_domain_correct=router_ok,
                router_note=router_note,
                response_relevant=relevant,
                relevance_note=rel_note,
                overall_quality=quality,
                quality_note=q_note,
                judge_model=self.model,
                judge_failed=holistic_failed,
                failure_reason=holistic_reason if holistic_failed else "",
            )
        except Exception as exc:
            return self._passthrough_verdict(
                len(result.suggestions), self.model,
                failed=True, reason=str(exc),
            )

    def _judge_single_suggestion(
        self,
        index: int,
        suggestion: str,
        risk: str,
        raw_data_summary: str,
        user_prompt: str,
    ) -> SuggestionVerdict:
        user_msg = textwrap.dedent(f"""\
            === SUGGESTION [{index}] ===
            [RISK:{risk}] {suggestion}

            === RAW SYSTEM DATA (for factuality check) ===
            {raw_data_summary}

            === USER'S ORIGINAL QUESTION ===
            {user_prompt}
        """)
        try:
            raw = self._llm_call(_SUGGESTION_SYSTEM, user_msg)
            data = _parse_json(raw)
            approved = bool(data.get("approved", True))
            orig_risk_rank = _RISK_RANK.get(risk.upper(), 0)
            raw_adjusted = str(data.get("adjusted_risk", risk)).upper()
            adjusted_risk = (
                raw_adjusted
                if _RISK_RANK.get(raw_adjusted, 0) >= orig_risk_rank
                else risk
            )
            return SuggestionVerdict(
                index=index,
                approved=approved,
                adjusted_risk=adjusted_risk,
                block_reason=str(data.get("block_reason", "")),
                factual=bool(data.get("factual", True)),
                factuality_note=str(data.get("factuality_note", "")),
            )
        except Exception:
            return SuggestionVerdict(
                index=index,
                approved=True,
                adjusted_risk=risk,
                block_reason="",
                factual=True,
                factuality_note="",
            )

    def _judge_holistic(
        self,
        result: AgentResult,
        user_prompt: str,
        domain: str,
    ) -> tuple[bool, str, bool, str, str, str, bool, str]:
        try:
            user_msg = textwrap.dedent(f"""\
                === USER'S QUESTION ===
                {user_prompt}

                === ROUTER CLASSIFIED THIS AS ===
                {domain}

                === AGENT THAT RESPONDED ===
                {result.agent}

                === AGENT'S FULL RESPONSE ===
                {result.full_response}
            """)
            raw = self._llm_call(_HOLISTIC_SYSTEM, user_msg)
            data = _parse_json(raw)
            return (
                bool(data.get("router_domain_correct", True)),
                str(data.get("router_note", "")),
                bool(data.get("response_relevant", True)),
                str(data.get("relevance_note", "")),
                str(data.get("overall_quality", "GOOD")).upper(),
                str(data.get("quality_note", "")),
                False,
                "",
            )
        except Exception as exc:
            return (True, "", True, "", "GOOD", "", True, str(exc))

    def _llm_call(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _passthrough_verdict(
        n: int,
        model: str,
        failed: bool = False,
        reason: str = "",
    ) -> JudgeVerdict:
        """Fully-approved verdict used when the judge fails entirely."""
        return JudgeVerdict(
            verdicts=[
                SuggestionVerdict(
                    index=i,
                    approved=True,
                    adjusted_risk="",
                    block_reason="",
                    factual=True,
                    factuality_note="",
                )
                for i in range(n)
            ],
            router_domain_correct=True,
            router_note="",
            response_relevant=True,
            relevance_note="",
            overall_quality="GOOD",
            quality_note="",
            judge_model=model,
            judge_failed=failed,
            failure_reason=reason,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_RISK_RANK: dict[str, int] = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


def _parse_json(raw: str) -> dict:
    """Extract the first JSON object from raw LLM output."""
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in judge output")
    return json.loads(raw[start:end])


# ─────────────────────────────────────────────────────────────────────────────
# Public convenience function
# ─────────────────────────────────────────────────────────────────────────────

def run_judge(
    result: AgentResult,
    user_prompt: str,
    domain: str,
    client: OpenAI,
    model: str,
) -> JudgedResult:
    """Instantiate Judge, run evaluation, wrap into JudgedResult."""
    judge = Judge(client=client, model=model)
    verdict = judge.evaluate(result, user_prompt, domain)
    return JudgedResult(agent_result=result, verdict=verdict, domain=domain)
