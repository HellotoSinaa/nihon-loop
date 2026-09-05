"""Nihon Loop protocol engine.

Pure functions implementing the PDCA / Kaizen rules. No HTTP, no ORM,
no I/O. Everything here is deterministic and unit-testable in isolation,
so the loop keeps working even with zero LLM keys configured.

Hard rules encoded here:
  - One improvement per cycle. `propose_change` always returns exactly one.
  - No change without a before/after metric — callers must supply metrics.
  - Root cause over blame: `five_whys` chains at most 5 "why" steps and
    never names a person as the cause.
  - Never locally-optimize a metric if the job failed: `propose_change`
    forces the "make the job succeed" fix when job_failed is True,
    regardless of which waste category is numerically largest.
"""
from __future__ import annotations

from dataclasses import dataclass

WASTE_CATEGORIES = (
    "extra_tool_calls",
    "human_wait_seconds",
    "repeated_errors",
    "rework",
    "context_thrash",
    "overproduction",
)


class ProtocolError(ValueError):
    """Raised when a caller violates a hard rule of the loop."""


class BudgetExceededError(ProtocolError):
    pass


@dataclass
class DoArtifacts:
    tool_calls: int = 0
    human_wait_seconds: float = 0.0
    errors: int = 0
    notes: str | None = None


def validate_do_submission(
    *, tools_used: int, tool_budget: int, job_failed: bool, job_failed_reason: str | None
) -> None:
    """DO must respect the tool budget unless the overrun is explained.

    A failed job with no reason given is treated as an unexplained overrun,
    since silently blowing a budget on a broken run is exactly the waste
    this protocol exists to catch.
    """
    if tools_used > tool_budget and not (job_failed and job_failed_reason):
        raise BudgetExceededError(
            f"tools_used ({tools_used}) exceeds tool_budget ({tool_budget}); "
            "either stay within budget or set job_failed=true with job_failed_reason"
        )


def compute_waste(
    *,
    artifacts: DoArtifacts,
    tool_budget: int,
    tools_used: int,
    job_failed: bool,
    prior_cycle_job: str | None = None,
    current_job: str = "",
) -> dict[str, float]:
    """Derive the waste taxonomy from real DO artifacts.

    Every value is computed from something the caller measured — nothing
    here is guessed. If artifacts are all zero, waste is all zero; the
    caller (coach/API layer) is responsible for asking for gemba data
    instead of inventing waste when evidence is thin.
    """
    extra_tool_calls = max(0, tools_used - tool_budget)

    # rework: the same job description repeated across consecutive cycles
    # is the clearest, least-guessy signal of redoing work.
    rework = 1 if (prior_cycle_job and current_job and prior_cycle_job.strip() == current_job.strip()) else 0

    # context_thrash: notes mentioning switching context is the only signal
    # we have without instrumenting the tool-call stream itself; keep it
    # conservative (0/1) rather than inventing a magnitude.
    context_thrash = 0
    if artifacts.notes:
        lowered = artifacts.notes.lower()
        if any(kw in lowered for kw in ("switch", "context switch", "tab switch", "re-read", "reread")):
            context_thrash = 1

    # overproduction: only flag it if notes explicitly say work went beyond
    # what was asked — again, conservative and evidence-bound.
    overproduction = 0
    if artifacts.notes:
        lowered = artifacts.notes.lower()
        if any(kw in lowered for kw in ("nobody asked", "out of scope", "extra scope", "gold-plat")):
            overproduction = 1

    return {
        "extra_tool_calls": float(extra_tool_calls),
        "human_wait_seconds": float(artifacts.human_wait_seconds),
        "repeated_errors": float(artifacts.errors),
        "rework": float(rework),
        "context_thrash": float(context_thrash),
        "overproduction": float(overproduction),
        "job_failed": 1.0 if job_failed else 0.0,
    }


def pick_biggest_waste(waste: dict[str, float]) -> str | None:
    """Return the single largest waste category, ignoring job_failed itself.

    Returns None if every category is zero — meaning there is no evidence
    of waste, and the correct move is to ask for more gemba data, not to
    invent an improvement.
    """
    candidates = {k: v for k, v in waste.items() if k in WASTE_CATEGORIES and v > 0}
    if not candidates:
        return None
    # Normalize human_wait_seconds down (seconds vs. counts aren't
    # comparable 1:1); a simple log-ish scale keeps one long wait from
    # always dominating small integer counts by sheer unit mismatch.
    def score(k: str, v: float) -> float:
        if k == "human_wait_seconds":
            return v / 30.0  # ~30s treated as "one unit" of waste
        return v

    return max(candidates, key=lambda k: score(k, candidates[k]))


def five_whys(*, biggest_waste: str | None, job_failed: bool, notes: str | None) -> list[dict[str, str]]:
    """Produce a bounded root-cause chain (max 5 whys).

    Stays at the system/process level — these templates never name or
    blame the human operator, per the hard rule.
    """
    if biggest_waste is None:
        return []

    chain: list[dict[str, str]] = []
    symptom = "the job failed" if job_failed else f"{biggest_waste} showed up in this cycle"
    chain.append({"why": f"Why did {symptom}?", "because": "the current standard did not specify a check for this before work started."})
    chain.append({"why": "Why didn't the standard specify that check?", "because": "the standard was written before this failure mode was observed."})
    chain.append({"why": "Why wasn't it observed earlier?", "because": "no prior cycle measured this waste category directly."})
    chain.append({"why": "Why wasn't it measured?", "because": "the DO step didn't require capturing that artifact."})
    chain.append({"why": "Why didn't DO require it?", "because": "this is the first cycle where this waste category was the dominant one."})
    return chain[:5]


_CHANGE_TEMPLATES: dict[str, str] = {
    "extra_tool_calls": "Add an explicit stop-condition to the standard: halt and report once {budget} tool calls are used, instead of continuing past budget.",
    "human_wait_seconds": "Add an async checkpoint to the standard: notify the human and continue other queued work instead of blocking on a single response.",
    "repeated_errors": "Add a pre-flight check to the standard: validate inputs/preconditions before executing, so the same error class can't recur silently.",
    "rework": "Add a completion criterion to the standard: a job is not closed until its specific output artifact is named and verified, preventing the same job from reopening next cycle.",
    "context_thrash": "Add a single-context rule to the standard: gather all needed references before starting DO, instead of switching sources mid-task.",
    "overproduction": "Add a scope line to the standard: DO stops at the literal job description; anything beyond it is logged as a new job, not done inline.",
}


@dataclass
class ChangeProposal:
    biggest_waste: str | None
    root_cause_chain: list[dict[str, str]]
    proposed_change: str
    metric_to_move: str
    rationale: str


def propose_change(
    *,
    waste: dict[str, float],
    job_failed: bool,
    tool_budget: int,
    notes: str | None,
) -> ChangeProposal:
    """Produce exactly one proposed change for the next standard.

    If job_failed is True, the proposal always targets making the job
    succeed first — we never let a smaller local metric win while the
    primary job is broken.
    """
    if job_failed:
        chain = five_whys(biggest_waste="job_failed", job_failed=True, notes=notes)
        return ChangeProposal(
            biggest_waste="job_failed",
            root_cause_chain=chain,
            proposed_change=(
                "Add a precondition gate to the standard: do not start DO until the "
                "inputs that caused this failure are explicitly checked first."
            ),
            metric_to_move="job_failed",
            rationale="The job failed this cycle. Per protocol, no waste metric may be "
            "locally optimized while the job itself is failing — fix job success first.",
        )

    biggest = pick_biggest_waste(waste)
    if biggest is None:
        return ChangeProposal(
            biggest_waste=None,
            root_cause_chain=[],
            proposed_change="",
            metric_to_move="",
            rationale="No waste detected in the submitted artifacts. Insufficient evidence "
            "to propose a change this cycle — run another cycle and capture gemba data "
            "(tool calls, wait time, errors) before proposing anything.",
        )

    chain = five_whys(biggest_waste=biggest, job_failed=False, notes=notes)
    template = _CHANGE_TEMPLATES[biggest].format(budget=tool_budget)
    return ChangeProposal(
        biggest_waste=biggest,
        root_cause_chain=chain,
        proposed_change=template,
        metric_to_move=biggest,
        rationale=f"'{biggest}' was the largest waste category with real evidence this cycle.",
    )


def format_standard_amendment(current_standard: str, accepted_change: str) -> str:
    """Append accepted_change as the next additive, numbered line."""
    lines = [ln for ln in current_standard.strip().splitlines() if ln.strip()]
    next_n = len(lines) + 1
    new_line = f"{next_n}. {accepted_change.strip()}"
    return current_standard.rstrip() + "\n" + new_line if current_standard.strip() else new_line
