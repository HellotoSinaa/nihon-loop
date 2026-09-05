"""Nihon Loop coach.

Handles:
  - parsing free-text /chat messages into protocol intents (deterministic,
    works with zero LLM keys)
  - rendering short, phase-aware replies from templates
  - optionally polishing the reply's wording through an LLM if a key is
    configured (Anthropic first, then Gemini) — the protocol mechanics
    never depend on the LLM being available or succeeding.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx

COACH_POLICY_PROMPT = """You are Nihon, operator of nihon.chatbot.
You run Nihon Loop, a Kaizen PDCA protocol for human+agent systems.
You never dump generic productivity advice.
You always know the cycle phase and ask only for the missing artifact.
In CHECK you output:
- waste found
- one root cause
- exactly one proposed standard change
- the metric that must move
In ACT you either update the standard or say why the change was rejected.
If the user asks you to "optimize everything", refuse and pick the single biggest waste.
Keep replies short. Always name the current phase and the one next action.
Never invent metrics that were not supplied as artifacts."""


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()

_PERSIAN_RANGE = re.compile(r"[\u0600-\u06FF]")


def is_persian(text: str) -> bool:
    return bool(_PERSIAN_RANGE.search(text or ""))


def llm_available() -> bool:
    return bool(ANTHROPIC_API_KEY or GEMINI_API_KEY)


async def llm_polish(reply_text: str, user_message: str) -> str:
    """Best-effort rewrite of a template reply through a configured LLM.

    Falls back to the original template text on any error or missing key,
    so the loop never depends on network access to function.
    """
    if not llm_available():
        return reply_text

    prompt = (
        f"{COACH_POLICY_PROMPT}\n\n"
        f"User message: {user_message}\n\n"
        f"Draft reply (keep the same facts and phase/next-action, "
        f"just improve tone, and match the user's language): {reply_text}"
    )

    try:
        if ANTHROPIC_API_KEY:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-6",
                        "max_tokens": 400,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
                text = "\n".join(blocks).strip()
                return text or reply_text
        elif GEMINI_API_KEY:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                )
                resp.raise_for_status()
                data = resp.json()
                text = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                return text or reply_text
    except Exception:
        return reply_text

    return reply_text


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------

Intent = Literal[
    "new_system",
    "new_cycle",
    "submit_do",
    "run_check",
    "accept",
    "reject",
    "status",
    "help",
    "unknown",
]


@dataclass
class ParsedIntent:
    intent: Intent
    payload: dict[str, Any]


_NEW_SYSTEM_RE = re.compile(r"^\s*(?:new|start)\s+system[:\s]+(.+)$", re.IGNORECASE)
_NEW_CYCLE_RE = re.compile(r"^\s*(?:new|start)\s+cycle[:\s]+(.+)$", re.IGNORECASE)
_JOB_RE = re.compile(r"^\s*job[:\s]+(.+)$", re.IGNORECASE)
_DONE_RE = re.compile(
    r"^\s*done\s+(?:tool_calls[:=]?\s*)?(\d+)\s+(?:wait[:=]?\s*)?([\d.]+)\s+(?:errors[:=]?\s*)?(\d+)"
    r"(?:\s+failed)?(?:\s+notes?[:\s]+(.*))?$",
    re.IGNORECASE,
)
_CHECK_RE = re.compile(r"^\s*check\s*$", re.IGNORECASE)
_ACCEPT_RE = re.compile(r"^\s*accept\b(.*)$", re.IGNORECASE)
_REJECT_RE = re.compile(r"^\s*reject\b(.*)$", re.IGNORECASE)
_STATUS_RE = re.compile(r"^\s*status\s*$", re.IGNORECASE)
_HELP_RE = re.compile(r"^\s*(help|\?)\s*$", re.IGNORECASE)


def parse_chat_intent(message: str) -> ParsedIntent:
    m = _NEW_SYSTEM_RE.match(message)
    if m:
        return ParsedIntent("new_system", {"name": m.group(1).strip()})

    m = _NEW_CYCLE_RE.match(message) or _JOB_RE.match(message)
    if m:
        return ParsedIntent("new_cycle", {"job": m.group(1).strip()})

    m = _DONE_RE.match(message)
    if m:
        tool_calls, wait, errors, notes = m.groups()
        failed = "failed" in message.lower()
        return ParsedIntent(
            "submit_do",
            {
                "tool_calls": int(tool_calls),
                "human_wait_seconds": float(wait),
                "errors": int(errors),
                "notes": notes.strip() if notes else None,
                "job_failed": failed,
            },
        )

    if _CHECK_RE.match(message):
        return ParsedIntent("run_check", {})

    m = _ACCEPT_RE.match(message)
    if m:
        return ParsedIntent("accept", {"note": m.group(1).strip() or None})

    m = _REJECT_RE.match(message)
    if m:
        return ParsedIntent("reject", {"note": m.group(1).strip() or None})

    if _STATUS_RE.match(message):
        return ParsedIntent("status", {})

    if _HELP_RE.match(message):
        return ParsedIntent("help", {})

    return ParsedIntent("unknown", {})


# ---------------------------------------------------------------------------
# Reply templates (English + Persian)
# ---------------------------------------------------------------------------

HELP_EN = (
    "Nihon Loop commands:\n"
    "- new system: <name>\n"
    "- new cycle: <job>\n"
    "- done <tool_calls> <wait_seconds> <errors> [failed] [notes: ...]\n"
    "- check\n"
    "- accept / reject [note]\n"
    "- status"
)
HELP_FA = (
    "دستورهای Nihon Loop:\n"
    "- new system: <نام>\n"
    "- new cycle: <کار>\n"
    "- done <tool_calls> <wait_seconds> <errors> [failed] [notes: ...]\n"
    "- check\n"
    "- accept / reject [یادداشت]\n"
    "- status"
)


def render(*, persian: bool, en: str, fa: str) -> str:
    return fa if persian else en
