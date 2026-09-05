"""Pydantic v2 schemas for request/response bodies."""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Systems
# ---------------------------------------------------------------------------


class SystemCreate(BaseModel):
    name: str
    owner_ref: str | None = None
    current_standard: str | None = None


class SystemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_ref: str | None
    current_standard: str
    created_at: dt.datetime


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------


class CycleCreate(BaseModel):
    job: str
    tool_budget: int = 8


class DoArtifacts(BaseModel):
    tool_calls: int = 0
    human_wait_seconds: float = 0
    errors: int = 0
    notes: str | None = None


class DoSubmit(BaseModel):
    artifacts: DoArtifacts
    tools_used: int
    job_failed: bool = False
    job_failed_reason: str | None = None


class CheckSubmit(BaseModel):
    """CHECK normally needs no body — it derives everything from DO artifacts.

    Present so a caller can optionally force a re-check or attach a note.
    """

    note: str | None = None


class ActSubmit(BaseModel):
    accept: bool
    note: str | None = None


class CycleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    system_id: str
    status: str
    job: str
    standard_snapshot: str
    tool_budget: int
    tools_used: int
    artifacts_json: str | None
    metrics_json: str | None
    waste_json: str | None
    five_whys_json: str | None
    proposed_change: str | None
    accepted_change: str | None
    before_metric_name: str | None
    before_metric_value: float | None
    after_metric_name: str | None
    after_metric_value: float | None
    job_failed: bool
    created_at: dt.datetime
    closed_at: dt.datetime | None


# ---------------------------------------------------------------------------
# Chat / webhook
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    system_id: str | None = None
    message: str
    channel: Literal["web", "telegram", "powerlobster"] = "web"


class ChatResponse(BaseModel):
    system_id: str | None
    cycle_id: str | None
    phase: str
    reply: str
    next_action: str


class GenericWebhook(BaseModel):
    message: str
    sender: str | None = None


class PowerLobsterWebhook(BaseModel):
    id: str | None = None
    sender_handle: str | None = None
    sender_name: str | None = None
    content: str
    created_at: str | None = None


class WebhookResponse(BaseModel):
    ok: bool
    reply: str


class StatusResponse(BaseModel):
    status: str
    agent: str
    protocol: str
    version: str
    open_cycles: int


class AnyJSON(BaseModel):
    """Escape hatch for endpoints that just pass through a dict."""

    model_config = ConfigDict(extra="allow")
    data: dict[str, Any] = Field(default_factory=dict)
