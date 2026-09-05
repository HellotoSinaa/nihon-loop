from app import protocol


def test_validate_do_submission_within_budget_ok():
    protocol.validate_do_submission(
        tools_used=5, tool_budget=8, job_failed=False, job_failed_reason=None
    )  # should not raise


def test_validate_do_submission_over_budget_raises():
    try:
        protocol.validate_do_submission(
            tools_used=10, tool_budget=8, job_failed=False, job_failed_reason=None
        )
        assert False, "expected BudgetExceededError"
    except protocol.BudgetExceededError:
        pass


def test_validate_do_submission_over_budget_explained_ok():
    protocol.validate_do_submission(
        tools_used=10, tool_budget=8, job_failed=True, job_failed_reason="hit an infinite retry loop"
    )  # should not raise


def test_compute_waste_all_zero_when_no_evidence():
    waste = protocol.compute_waste(
        artifacts=protocol.DoArtifacts(tool_calls=3, human_wait_seconds=0, errors=0, notes=None),
        tool_budget=8,
        tools_used=3,
        job_failed=False,
    )
    assert waste["extra_tool_calls"] == 0
    assert waste["human_wait_seconds"] == 0
    assert waste["repeated_errors"] == 0
    assert waste["rework"] == 0
    assert waste["context_thrash"] == 0
    assert waste["overproduction"] == 0


def test_compute_waste_extra_tool_calls():
    waste = protocol.compute_waste(
        artifacts=protocol.DoArtifacts(tool_calls=11, human_wait_seconds=0, errors=0),
        tool_budget=8,
        tools_used=11,
        job_failed=False,
    )
    assert waste["extra_tool_calls"] == 3


def test_compute_waste_rework_detected_on_repeated_job():
    waste = protocol.compute_waste(
        artifacts=protocol.DoArtifacts(),
        tool_budget=8,
        tools_used=1,
        job_failed=False,
        prior_cycle_job="Fix the flaky login test",
        current_job="Fix the flaky login test",
    )
    assert waste["rework"] == 1


def test_compute_waste_context_thrash_from_notes():
    waste = protocol.compute_waste(
        artifacts=protocol.DoArtifacts(notes="had to context switch between three tabs"),
        tool_budget=8,
        tools_used=1,
        job_failed=False,
    )
    assert waste["context_thrash"] == 1


def test_pick_biggest_waste_none_when_all_zero():
    waste = {k: 0.0 for k in protocol.WASTE_CATEGORIES}
    assert protocol.pick_biggest_waste(waste) is None


def test_pick_biggest_waste_picks_largest():
    waste = {
        "extra_tool_calls": 1,
        "human_wait_seconds": 300,  # scores as 10 units
        "repeated_errors": 2,
        "rework": 0,
        "context_thrash": 0,
        "overproduction": 0,
    }
    assert protocol.pick_biggest_waste(waste) == "human_wait_seconds"


def test_five_whys_capped_at_five():
    chain = protocol.five_whys(biggest_waste="repeated_errors", job_failed=False, notes=None)
    assert len(chain) <= 5
    assert len(chain) > 0


def test_five_whys_empty_when_no_waste():
    chain = protocol.five_whys(biggest_waste=None, job_failed=False, notes=None)
    assert chain == []


def test_propose_change_job_failed_overrides_waste_metric():
    # Even with large extra_tool_calls waste, a failed job must always
    # produce the "fix the job" proposal, never a local metric optimization.
    waste = {
        "extra_tool_calls": 50,
        "human_wait_seconds": 0,
        "repeated_errors": 0,
        "rework": 0,
        "context_thrash": 0,
        "overproduction": 0,
        "job_failed": 1.0,
    }
    proposal = protocol.propose_change(waste=waste, job_failed=True, tool_budget=8, notes=None)
    assert proposal.metric_to_move == "job_failed"
    assert "precondition" in proposal.proposed_change.lower()


def test_propose_change_no_evidence_returns_empty_proposal():
    waste = {k: 0.0 for k in protocol.WASTE_CATEGORIES}
    proposal = protocol.propose_change(waste=waste, job_failed=False, tool_budget=8, notes=None)
    assert proposal.proposed_change == ""
    assert proposal.biggest_waste is None


def test_propose_change_single_improvement_only():
    waste = {
        "extra_tool_calls": 5,
        "human_wait_seconds": 200,
        "repeated_errors": 3,
        "rework": 1,
        "context_thrash": 1,
        "overproduction": 1,
    }
    proposal = protocol.propose_change(waste=waste, job_failed=False, tool_budget=8, notes=None)
    # Exactly one proposed change - a single string, not a list.
    assert isinstance(proposal.proposed_change, str)
    assert proposal.proposed_change.count("\n") == 0 or proposal.proposed_change.strip() != ""
    assert proposal.metric_to_move in protocol.WASTE_CATEGORIES


def test_format_standard_amendment_appends_numbered_line():
    standard = "1. Triage every ticket within 1 business day."
    updated = protocol.format_standard_amendment(standard, "Add an async checkpoint.")
    assert updated.splitlines()[-1].startswith("2. ")
    assert "Add an async checkpoint." in updated


def test_format_standard_amendment_from_empty_standard():
    updated = protocol.format_standard_amendment("", "First rule.")
    assert updated == "1. First rule."
