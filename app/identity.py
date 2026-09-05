"""Generates the canonical skill.md and agent.json for the nihon.chatbot
Headless Domains identity. Both are derived from PUBLIC_BASE_URL so the
served files always point at wherever this instance is actually deployed,
not at the bare Handshake name (which most browsers/agents can't resolve
without a gateway).
"""
from __future__ import annotations

import os

VERSION = "0.1.0"
DOMAIN = "nihon.chatbot"


def public_base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "https://YOUR-DEPLOY-HOST").rstrip("/")


def build_agent_json() -> dict:
    base = public_base_url()
    return {
        "name": "Nihon Loop",
        "domain": DOMAIN,
        "version": VERSION,
        "description": (
            "A Kaizen loop coach for human+agent systems. Each cycle does one job "
            "against the current standard, measures waste from real work artifacts, "
            "and allows exactly one improvement into the next standard."
        ),
        "protocol": "nihon-loop",
        "capabilities": [
            "kaizen_coach",
            "pdca_loop",
            "waste_audit",
            "standard_update",
        ],
        "endpoints": {
            "chat": f"{base}/chat",
            "webhook": f"{base}/webhook",
            "status": f"{base}/status",
            "skill": f"{base}/skill.md",
            "loop": {
                "create_system": f"{base}/systems",
                "create_cycle": f"{base}/systems/{{id}}/cycles",
                "do": f"{base}/cycles/{{id}}/do",
                "check": f"{base}/cycles/{{id}}/check",
                "act": f"{base}/cycles/{{id}}/act",
            },
        },
        "protocols": ["http"],
        "webhook": {
            "default_path": "/webhook",
            "formats": ["generic", "powerlobster"],
            "secret_header": "X-PowerLobster-Secret",
        },
        "payments": {
            "inbound": "pending",
            "note": "No payment address on file yet for this identity.",
        },
        "squad": "powerlobster-squad-Nihon",
        "gateway_note": (
            "Handshake TLD names like nihon.chatbot do not resolve in stock "
            "browsers without a Handshake-aware resolver or gateway. This "
            f"service is always reachable at {base} over plain HTTPS."
        ),
    }


def build_skill_md() -> str:
    base = public_base_url()
    return f"""# Nihon Loop — Kaizen / PDCA protocol skill

## What this is

Nihon Loop is a small operational PDCA (Plan-Do-Check-Act) coaching
protocol for human+agent systems. It is inspired by Kaizen but is **not**
a claim to implement the full Toyota Production System — it is a narrow,
strict loop with one job:

> Each cycle does one job against the current standard, measures waste
> from real artifacts, and allows exactly one improvement into the next
> standard.

## When to use it

Use Nihon Loop when a human+agent pair (or team) wants to improve a
recurring workflow (a coding task, a support queue, a content pipeline,
an agent's own tool use) by running short, measured cycles instead of
ad-hoc "let's be more efficient" advice.

Do not use it to generate a generic list of productivity tips — the
protocol refuses that by design (see Hard rules below).

## Cycle schema

A `Cycle` belongs to a `System` (the thing being improved) and moves
through five statuses:

```
plan -> do -> check -> act -> closed
```

Fields:

- `job` (text) — the one thing this cycle does.
- `standard_snapshot` (text) — the System's `current_standard` at the
  moment the cycle started.
- `tool_budget` / `tools_used` (int) — DO must stay within budget unless
  the overrun is explained via `job_failed` + `job_failed_reason`.
- `artifacts_json` — raw DO artifacts: `tool_calls`, `human_wait_seconds`,
  `errors`, `notes`.
- `waste_json` — waste computed from artifacts (see taxonomy below).
- `five_whys_json` — a root-cause chain, max 5 steps, only produced when
  waste or failure is present.
- `proposed_change` — exactly one proposed standard amendment.
- `accepted_change` — set only if ACT accepts the proposal.
- `before_metric_name` / `before_metric_value`,
  `after_metric_name` / `after_metric_value` — the metric that must move,
  before and after.

## Required inputs per phase

- **PLAN** (`POST /systems/{{id}}/cycles`): `job` (text), optional
  `tool_budget` (default 8).
- **DO** (`POST /cycles/{{id}}/do`): `artifacts` (`tool_calls`,
  `human_wait_seconds`, `errors`, `notes`), `tools_used`, optional
  `job_failed` + `job_failed_reason` if over budget.
- **CHECK** (`POST /cycles/{{id}}/check`): no required body. Computes
  waste from the DO artifacts already on file; refuses to invent waste
  that wasn't measured.
- **ACT** (`POST /cycles/{{id}}/act`): `accept` (bool), optional `note`.

## Waste taxonomy

- `extra_tool_calls` — tool calls beyond `tool_budget`.
- `human_wait_seconds` — measured wait time, not estimated.
- `repeated_errors` — error count from DO artifacts.
- `rework` — the same `job` text repeated across consecutive cycles.
- `context_thrash` — unnecessary switching between models/files/tabs,
  flagged only when DO notes explicitly mention it.
- `overproduction` — work nobody asked for, flagged only when DO notes
  explicitly mention it.

If none of these have real evidence in the artifacts, CHECK returns no
proposed change and asks for gemba data instead of guessing.

## The one-improvement rule

CHECK always returns **exactly one** `proposed_change`, never a list.
If the job failed this cycle, the proposed change always targets making
the job succeed next time — no waste metric may be locally optimized
while the job itself is broken. Every proposed change names the single
metric (`metric_to_move`) that must move before/after.

## HTTP endpoints

```
GET  /health
GET  /status
GET  /skill.md
GET  /agent.json
POST /systems
POST /systems/{{id}}/cycles
POST /cycles/{{id}}/do
POST /cycles/{{id}}/check
POST /cycles/{{id}}/act
POST /chat
POST /webhook
GET  /
```

This deployment is reachable at: `{base}`

## Example cycle (JSON)

```json
{{
  "system": {{"name": "support-queue-triage", "current_standard": "1. Triage every ticket within 1 business day."}},
  "cycle": {{"job": "Triage the Monday backlog", "tool_budget": 8}},
  "do": {{
    "artifacts": {{"tool_calls": 11, "human_wait_seconds": 240, "errors": 2, "notes": "had to re-read three tickets after a tab switch"}},
    "tools_used": 11
  }},
  "check_result": {{
    "waste": {{"extra_tool_calls": 3, "human_wait_seconds": 240, "repeated_errors": 2, "rework": 0, "context_thrash": 1, "overproduction": 0}},
    "biggest_waste": "human_wait_seconds",
    "proposed_change": "Add an async checkpoint to the standard: notify the human and continue other queued work instead of blocking on a single response.",
    "metric_to_move": "human_wait_seconds"
  }},
  "act": {{"accept": true}}
}}
```

## How another agent should call this

1. `POST {base}/systems` with `{{"name": "...", "current_standard": "..."}}`
   to get a `system_id`.
2. `POST {base}/systems/{{system_id}}/cycles` with `{{"job": "..."}}` to
   open a cycle in `plan`.
3. Do the work, then `POST {base}/cycles/{{cycle_id}}/do` with the real
   artifacts you measured.
4. `POST {base}/cycles/{{cycle_id}}/check` to get the waste report and
   the single proposed change.
5. `POST {base}/cycles/{{cycle_id}}/act` with `{{"accept": true}}` or
   `{{"accept": false, "note": "..."}}` to close the cycle.

Alternatively, drive the whole thing conversationally via
`POST {base}/chat` with `{{"message": "..."}}` — it accepts short
commands (`new system: ...`, `new cycle: ...`, `done <calls> <wait>
<errors> [failed] [notes: ...]`, `check`, `accept`/`reject`, `status`)
and always replies with the current phase and the one next action.
"""
