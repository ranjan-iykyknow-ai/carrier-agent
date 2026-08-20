"""Runtime evals (spec 3I): proxy quality scores on live traces, failure-isolated."""

import pytest

from apps.aiops import observability, runtime_evals
from apps.workspace.models import AssistantRun
from tests.factories import (
    make_ai_operation,
    make_communication_event,
    make_email_content,
    make_extraction_run,
    make_ingestion_job,
    make_inquiry,
    make_provider_call,
    make_snapshot,
)

pytestmark = pytest.mark.django_db


class ScoreFake:
    def __init__(self, fail=False):
        self.fail = fail
        self.scores = []

    def create_score(self, **kwargs):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.scores.append(kwargs)

    def flush(self):
        pass


@pytest.fixture(autouse=True)
def _reset():
    yield
    observability.reset_client()


def build_scored_job(**inquiry_kwargs):
    snapshot = make_snapshot(is_active=True)
    event = make_communication_event(snapshot=snapshot)
    make_email_content(event=event)
    job = make_ingestion_job(event=event, status="needs_review")
    operation = make_ai_operation(operation_type="extraction")
    make_provider_call(operation=operation, langfuse_trace_id="a" * 32)
    make_extraction_run(
        event=event, ai_operation=operation, is_current=True, validation_status="valid"
    )
    inquiry = make_inquiry(
        event=event,
        review_status="needs_review",
        carrier_resolution_status="verified",
        load_resolution_status="unmatched",
        **inquiry_kwargs,
    )
    from apps.inquiries.models import InquiryFieldAssessment

    for field, status in (
        ("carrier_identity", "explicit"),
        ("equipment", "inferred"),
        ("availability", "explicit"),
        ("rate", "missing"),
    ):
        InquiryFieldAssessment.objects.create(
            inquiry=inquiry, field_name=field, evidence_status=status, is_current=True
        )
    return job


class TestIngestionScores:
    def test_scores_computed_and_pushed_with_trace(self):
        fake = ScoreFake()
        observability.set_client(fake)
        job = build_scored_job()

        scores = runtime_evals.score_ingestion_job(job)

        assert scores["extraction_valid"] == 1.0
        # explicit / (explicit + inferred): 2 of 3 graded fields.
        assert round(scores["grounding_rate"], 2) == 0.67
        assert scores["carrier_resolved"] == 1.0
        assert scores["load_resolved"] == 0.0
        assert scores["routed_to_review"] == 1.0
        pushed = {s["name"] for s in fake.scores}
        assert "grounding_rate" in pushed
        assert all(s["trace_id"] == "a" * 32 for s in fake.scores)

    def test_non_terminal_job_is_skipped(self):
        observability.set_client(ScoreFake())
        snapshot = make_snapshot(is_active=True)
        event = make_communication_event(snapshot=snapshot)
        make_email_content(event=event)
        job = make_ingestion_job(event=event, status="processing")

        assert runtime_evals.score_ingestion_job(job) is None

    def test_langfuse_failure_never_raises(self):
        observability.set_client(ScoreFake(fail=True))
        job = build_scored_job()

        scores = runtime_evals.score_ingestion_job(job)  # must not raise

        assert scores["extraction_valid"] == 1.0


class TestAssistantScores:
    def test_completed_run_scores_citation_validity(self):
        from tests.factories import (
            make_assistant_conversation,
            make_assistant_message,
            make_assistant_run,
        )

        fake = ScoreFake()
        observability.set_client(fake)
        conversation = make_assistant_conversation()
        message = make_assistant_message(conversation=conversation, role="assistant", sequence=2)
        run = make_assistant_run(
            conversation=conversation,
            assistant_message=message,
            status=AssistantRun.Status.COMPLETED,
            langfuse_trace_id="b" * 32,
        )
        run.tool_executions.create(sequence=1, tool_name="get_load", status="completed")
        run.tool_executions.create(sequence=2, tool_name="get_load", status="failed")
        message.citations.create(
            sequence=1, source_type="load", stable_source_id="load:1", display_label="L"
        )

        scores = runtime_evals.score_assistant_run(run)

        assert scores["answer_supported"] == 1.0
        assert scores["tool_success_rate"] == 0.5
        assert scores["citation_count"] == 1.0
        assert all(s["trace_id"] == "b" * 32 for s in fake.scores)

    def test_unsupported_run_scores_zero(self):
        from tests.factories import make_assistant_conversation, make_assistant_run

        observability.set_client(ScoreFake())
        conversation = make_assistant_conversation()
        run = make_assistant_run(
            conversation=conversation,
            status=AssistantRun.Status.FAILED,
            error_code="unsupported_citation",
            langfuse_trace_id="c" * 32,
        )

        scores = runtime_evals.score_assistant_run(run)

        assert scores["answer_supported"] == 0.0
