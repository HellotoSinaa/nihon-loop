"""Nihon Loop — FastAPI app.

Serves the PDCA/Kaizen loop API plus the nihon.chatbot Headless Domains
identity files (skill.md, agent.json) and a minimal chat webhook.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import coach, identity, protocol, webhooks
from app.db import get_db, init_db
from app.models import Cycle, Message, System
from app.schemas import (
    ActSubmit,
    ChatRequest,
    ChatResponse,
    CheckSubmit,
    CycleCreate,
    CycleOut,
    DoSubmit,
    StatusResponse,
    SystemCreate,
    SystemOut,
    WebhookResponse,
)

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Nihon Loop", version=APP_VERSION, lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Health / status / identity
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/status", response_model=StatusResponse)
def status(db: Session = Depends(get_db)) -> StatusResponse:
    open_cycles = db.execute(
        select(Cycle).where(Cycle.status != "closed")
    ).scalars().all()
    return StatusResponse(
        status="ok",
        agent="nihon.chatbot",
        protocol="nihon-loop",
        version=APP_VERSION,
        open_cycles=len(open_cycles),
    )


@app.get("/skill.md", response_class=PlainTextResponse)
def skill_md() -> str:
    return identity.build_skill_md()


@app.get("/agent.json")
def agent_json() -> JSONResponse:
    return JSONResponse(identity.build_agent_json())


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Systems
# ---------------------------------------------------------------------------


@app.post("/systems", response_model=SystemOut)
def create_system(body: SystemCreate, db: Session = Depends(get_db)) -> System:
    system = System(
        name=body.name,
        owner_ref=body.owner_ref,
        current_standard=body.current_standard or "1. No standard defined yet.",
    )
    db.add(system)
    db.commit()
    db.refresh(system)
    return system


@app.get("/systems/{system_id}", response_model=SystemOut)
def get_system(system_id: str, db: Session = Depends(get_db)) -> System:
    system = db.get(System, system_id)
    if not system:
        raise HTTPException(status_code=404, detail="system not found")
    return system


# ---------------------------------------------------------------------------
# Cycles — PLAN / DO / CHECK / ACT
# ---------------------------------------------------------------------------


def _get_system_or_404(db: Session, system_id: str) -> System:
    system = db.get(System, system_id)
    if not system:
        raise HTTPException(status_code=404, detail="system not found")
    return system


def _get_cycle_or_404(db: Session, cycle_id: str) -> Cycle:
    cycle = db.get(Cycle, cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="cycle not found")
    return cycle


def _require_status(cycle: Cycle, expected: str) -> None:
    if cycle.status != expected:
        raise HTTPException(
            status_code=409,
            detail=f"cycle is in status '{cycle.status}', expected '{expected}'",
        )


def _latest_cycle_with_job(db: Session, system_id: str, exclude_cycle_id: str) -> str | None:
    """Job text of the most recently closed cycle for this system, used to
    detect rework (the same job repeated back to back)."""
    prior = db.execute(
        select(Cycle)
        .where(Cycle.system_id == system_id, Cycle.id != exclude_cycle_id)
        .order_by(Cycle.created_at.desc())
    ).scalars().first()
    return prior.job if prior else None


@app.post("/systems/{system_id}/cycles", response_model=CycleOut)
def start_cycle(system_id: str, body: CycleCreate, db: Session = Depends(get_db)) -> Cycle:
    system = _get_system_or_404(db, system_id)
    cycle = Cycle(
        system_id=system.id,
        status="plan",
        job=body.job,
        standard_snapshot=system.current_standard,
        tool_budget=body.tool_budget,
    )
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


@app.get("/cycles/{cycle_id}", response_model=CycleOut)
def get_cycle(cycle_id: str, db: Session = Depends(get_db)) -> Cycle:
    return _get_cycle_or_404(db, cycle_id)


@app.post("/cycles/{cycle_id}/do", response_model=CycleOut)
def do_cycle(cycle_id: str, body: DoSubmit, db: Session = Depends(get_db)) -> Cycle:
    cycle = _get_cycle_or_404(db, cycle_id)
    _require_status(cycle, "plan")

    try:
        protocol.validate_do_submission(
            tools_used=body.tools_used,
            tool_budget=cycle.tool_budget,
            job_failed=body.job_failed,
            job_failed_reason=body.job_failed_reason,
        )
    except protocol.BudgetExceededError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cycle.artifacts_json = json.dumps(body.artifacts.model_dump())
    cycle.tools_used = body.tools_used
    cycle.job_failed = body.job_failed
    cycle.status = "do"
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


@app.post("/cycles/{cycle_id}/check", response_model=CycleOut)
def check_cycle(cycle_id: str, body: CheckSubmit, db: Session = Depends(get_db)) -> Cycle:
    cycle = _get_cycle_or_404(db, cycle_id)
    _require_status(cycle, "do")

    artifacts_dict = json.loads(cycle.artifacts_json or "{}")
    artifacts = protocol.DoArtifacts(
        tool_calls=artifacts_dict.get("tool_calls", 0),
        human_wait_seconds=artifacts_dict.get("human_wait_seconds", 0.0),
        errors=artifacts_dict.get("errors", 0),
        notes=artifacts_dict.get("notes"),
    )
    prior_job = _latest_cycle_with_job(db, cycle.system_id, cycle.id)

    waste = protocol.compute_waste(
        artifacts=artifacts,
        tool_budget=cycle.tool_budget,
        tools_used=cycle.tools_used,
        job_failed=cycle.job_failed,
        prior_cycle_job=prior_job,
        current_job=cycle.job,
    )
    proposal = protocol.propose_change(
        waste=waste,
        job_failed=cycle.job_failed,
        tool_budget=cycle.tool_budget,
        notes=artifacts.notes,
    )

    cycle.waste_json = json.dumps(waste)
    cycle.five_whys_json = json.dumps(proposal.root_cause_chain)
    cycle.proposed_change = proposal.proposed_change
    cycle.metrics_json = json.dumps(
        {"biggest_waste": proposal.biggest_waste, "rationale": proposal.rationale}
    )
    if proposal.metric_to_move:
        cycle.before_metric_name = proposal.metric_to_move
        cycle.before_metric_value = waste.get(proposal.metric_to_move, 0.0) if proposal.metric_to_move != "job_failed" else 1.0
    cycle.status = "check"
    if body.note:
        db.add(Message(system_id=cycle.system_id, cycle_id=cycle.id, role="user", content=body.note))

    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


@app.post("/cycles/{cycle_id}/act", response_model=CycleOut)
def act_cycle(cycle_id: str, body: ActSubmit, db: Session = Depends(get_db)) -> Cycle:
    cycle = _get_cycle_or_404(db, cycle_id)
    _require_status(cycle, "check")

    if body.accept:
        if not cycle.proposed_change:
            raise HTTPException(
                status_code=400,
                detail="no proposed_change on file for this cycle — nothing to accept",
            )
        system = _get_system_or_404(db, cycle.system_id)
        system.current_standard = protocol.format_standard_amendment(
            system.current_standard, cycle.proposed_change
        )
        cycle.accepted_change = cycle.proposed_change
        db.add(system)
    else:
        cycle.accepted_change = None

    if body.note:
        db.add(Message(system_id=cycle.system_id, cycle_id=cycle.id, role="user", content=body.note))

    cycle.status = "closed"
    cycle.closed_at = datetime.now(timezone.utc)
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def _latest_cycle_in_status(db: Session, system_id: str, status_: str) -> Cycle | None:
    return db.execute(
        select(Cycle)
        .where(Cycle.system_id == system_id, Cycle.status == status_)
        .order_by(Cycle.created_at.desc())
    ).scalars().first()


def _handle_chat_intent(
    db: Session, system_id: str | None, message: str, owner_ref: str | None = None
) -> dict:
    persian = coach.is_persian(message)
    parsed = coach.parse_chat_intent(message)
    intent, payload = parsed.intent, parsed.payload

    def R(en: str, fa: str) -> str:
        return coach.render(persian=persian, en=en, fa=fa)

    if intent == "new_system":
        system = System(name=payload["name"], owner_ref=owner_ref)
        db.add(system)
        db.commit()
        db.refresh(system)
        return {
            "system_id": system.id,
            "cycle_id": None,
            "phase": "plan",
            "reply": R(
                f"Created system '{system.name}' (id={system.id}).",
                f"سیستم «{system.name}» ساخته شد (id={system.id}).",
            ),
            "next_action": R("Start a cycle with: new cycle: <job>", "چرخه را با «new cycle: <کار>» شروع کن"),
        }

    if intent == "help":
        return {
            "system_id": system_id,
            "cycle_id": None,
            "phase": "n/a",
            "reply": coach.HELP_FA if persian else coach.HELP_EN,
            "next_action": R("Try: new system: <name>", "امتحان کن: new system: <نام>"),
        }

    if intent == "status":
        if not system_id:
            return {
                "system_id": None,
                "cycle_id": None,
                "phase": "n/a",
                "reply": R("No system yet.", "هنوز سیستمی نداریم."),
                "next_action": R("Create one with: new system: <name>", "با «new system: <نام>» بساز"),
            }
        system = db.get(System, system_id)
        if not system:
            raise HTTPException(status_code=404, detail="system not found")
        open_cycle = db.execute(
            select(Cycle)
            .where(Cycle.system_id == system_id, Cycle.status != "closed")
            .order_by(Cycle.created_at.desc())
        ).scalars().first()
        phase = open_cycle.status if open_cycle else "plan"
        return {
            "system_id": system_id,
            "cycle_id": open_cycle.id if open_cycle else None,
            "phase": phase,
            "reply": R(
                f"System '{system.name}'. Standard:\n{system.current_standard}",
                f"سیستم «{system.name}». استاندارد فعلی:\n{system.current_standard}",
            ),
            "next_action": R("Type 'help' for commands.", "برای دیدن دستورها بنویس: help"),
        }

    if not system_id:
        return {
            "system_id": None,
            "cycle_id": None,
            "phase": "n/a",
            "reply": R(
                "No system yet — create one first.",
                "هنوز سیستمی نساخته‌ای — اول یکی بساز.",
            ),
            "next_action": R("new system: <name>", "new system: <نام>"),
        }

    system = db.get(System, system_id)
    if not system:
        raise HTTPException(status_code=404, detail="system not found")

    if intent == "new_cycle":
        cycle = Cycle(
            system_id=system.id,
            status="plan",
            job=payload["job"],
            standard_snapshot=system.current_standard,
        )
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        return {
            "system_id": system.id,
            "cycle_id": cycle.id,
            "phase": "plan",
            "reply": R(
                f"Cycle {cycle.id} started — PLAN. Job: {cycle.job}",
                f"چرخه {cycle.id} شروع شد — PLAN. کار: {cycle.job}",
            ),
            "next_action": R(
                "Submit DO with: done <tool_calls> <wait_seconds> <errors> [failed] [notes: ...]",
                "با «done <تعداد_ابزار> <زمان_انتظار> <خطاها> [failed] [notes: ...]» ادامه بده",
            ),
        }

    if intent == "submit_do":
        cycle = _latest_cycle_in_status(db, system.id, "plan")
        if not cycle:
            return {
                "system_id": system.id,
                "cycle_id": None,
                "phase": "n/a",
                "reply": R("No cycle in PLAN to submit DO for.", "چرخه‌ای در فاز PLAN وجود ندارد."),
                "next_action": R("new cycle: <job>", "new cycle: <کار>"),
            }
        try:
            protocol.validate_do_submission(
                tools_used=payload["tool_calls"],
                tool_budget=cycle.tool_budget,
                job_failed=payload["job_failed"],
                job_failed_reason=payload.get("notes"),
            )
        except protocol.BudgetExceededError as exc:
            return {
                "system_id": system.id,
                "cycle_id": cycle.id,
                "phase": "plan",
                "reply": R(str(exc), "بودجهٔ ابزار رد شده و دلیلی ثبت نشده."),
                "next_action": R(
                    "Resubmit within budget, or add 'failed notes: <reason>'.",
                    "دوباره در محدودهٔ بودجه بفرست یا «failed notes: <دلیل>» اضافه کن.",
                ),
            }
        cycle.artifacts_json = json.dumps(
            {
                "tool_calls": payload["tool_calls"],
                "human_wait_seconds": payload["human_wait_seconds"],
                "errors": payload["errors"],
                "notes": payload.get("notes"),
            }
        )
        cycle.tools_used = payload["tool_calls"]
        cycle.job_failed = payload["job_failed"]
        cycle.status = "do"
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        return {
            "system_id": system.id,
            "cycle_id": cycle.id,
            "phase": "do",
            "reply": R("DO recorded.", "DO ثبت شد."),
            "next_action": R("Run CHECK with: check", "با «check» ادامه بده"),
        }

    if intent == "run_check":
        cycle = _latest_cycle_in_status(db, system.id, "do")
        if not cycle:
            return {
                "system_id": system.id,
                "cycle_id": None,
                "phase": "n/a",
                "reply": R("No cycle in DO to check.", "چرخه‌ای در فاز DO نیست."),
                "next_action": R("Submit DO first with: done ...", "اول DO را ثبت کن: done ..."),
            }
        checked = check_cycle(cycle.id, CheckSubmit(), db)  # reuse endpoint logic
        waste = json.loads(checked.waste_json or "{}")
        metrics = json.loads(checked.metrics_json or "{}")
        if checked.proposed_change:
            reply = R(
                f"CHECK — waste: {waste}. Root cause: {metrics.get('rationale')}. "
                f"Proposed change: {checked.proposed_change} (metric: {checked.before_metric_name})",
                f"CHECK — اتلاف: {waste}. علت ریشه‌ای: {metrics.get('rationale')}. "
                f"تغییر پیشنهادی: {checked.proposed_change} (متریک: {checked.before_metric_name})",
            )
            next_action = R("Accept or reject with: accept / reject [note]", "با «accept» یا «reject [یادداشت]» ادامه بده")
        else:
            reply = R(
                f"CHECK — no measurable waste in these artifacts: {waste}. No change proposed.",
                f"CHECK — در این داده‌ها اتلاف قابل اندازه‌گیری نبود: {waste}. تغییری پیشنهاد نشد.",
            )
            next_action = R("Close this cycle with: reject", "چرخه را با «reject» ببند")
        return {
            "system_id": system.id,
            "cycle_id": checked.id,
            "phase": "check",
            "reply": reply,
            "next_action": next_action,
        }

    if intent in ("accept", "reject"):
        cycle = _latest_cycle_in_status(db, system.id, "check")
        if not cycle:
            return {
                "system_id": system.id,
                "cycle_id": None,
                "phase": "n/a",
                "reply": R("No cycle in CHECK to act on.", "چرخه‌ای در فاز CHECK نیست."),
                "next_action": R("Run 'check' first.", "اول «check» را اجرا کن."),
            }
        try:
            acted = act_cycle(
                cycle.id, ActSubmit(accept=(intent == "accept"), note=payload.get("note")), db
            )
        except HTTPException as exc:
            return {
                "system_id": system.id,
                "cycle_id": cycle.id,
                "phase": "check",
                "reply": R(str(exc.detail), str(exc.detail)),
                "next_action": R("Run 'check' again or reject.", "دوباره «check» را اجرا کن یا «reject» بزن."),
            }
        if intent == "accept":
            reply = R(
                f"ACT — standard updated. Cycle {acted.id} closed.",
                f"ACT — استاندارد به‌روزرسانی شد. چرخهٔ {acted.id} بسته شد.",
            )
        else:
            reply = R(
                f"ACT — change rejected. Cycle {acted.id} closed, standard unchanged.",
                f"ACT — تغییر رد شد. چرخهٔ {acted.id} بسته شد، استاندارد بدون تغییر ماند.",
            )
        return {
            "system_id": system.id,
            "cycle_id": acted.id,
            "phase": "closed",
            "reply": reply,
            "next_action": R("Start the next cycle with: new cycle: <job>", "چرخهٔ بعدی را با «new cycle: <کار>» شروع کن"),
        }

    # unknown
    open_cycle = db.execute(
        select(Cycle)
        .where(Cycle.system_id == system.id, Cycle.status != "closed")
        .order_by(Cycle.created_at.desc())
    ).scalars().first()
    phase = open_cycle.status if open_cycle else "plan"
    return {
        "system_id": system.id,
        "cycle_id": open_cycle.id if open_cycle else None,
        "phase": phase,
        "reply": R(
            "I didn't recognize that command.",
            "این دستور را نشناختم.",
        ),
        "next_action": R("Type 'help' for the command list.", "برای دیدن دستورها بنویس: help"),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    result = _handle_chat_intent(db, body.system_id, body.message)
    if coach.llm_available():
        result["reply"] = await coach.llm_polish(result["reply"], body.message)
    if body.system_id or result.get("system_id"):
        db.add(
            Message(
                system_id=result.get("system_id") or body.system_id,
                cycle_id=result.get("cycle_id"),
                role="user",
                content=body.message,
            )
        )
        db.add(
            Message(
                system_id=result.get("system_id") or body.system_id,
                cycle_id=result.get("cycle_id"),
                role="agent",
                content=result["reply"],
            )
        )
        db.commit()
    return ChatResponse(**result)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


@app.post("/webhook", response_model=WebhookResponse)
async def webhook(request: Request, db: Session = Depends(get_db)) -> WebhookResponse:
    pl_secret_header = request.headers.get("X-PowerLobster-Secret")
    if request.headers.get("X-PowerLobster-Event"):
        webhooks.verify_powerlobster_secret(pl_secret_header)

    message, sender = await webhooks.normalize_webhook_body(request)

    system_id = None
    if sender:
        existing = db.execute(select(System).where(System.owner_ref == sender)).scalars().first()
        if existing:
            system_id = existing.id

    result = _handle_chat_intent(db, system_id, message, owner_ref=sender)
    if coach.llm_available():
        result["reply"] = await coach.llm_polish(result["reply"], message)

    return WebhookResponse(ok=True, reply=result["reply"])
