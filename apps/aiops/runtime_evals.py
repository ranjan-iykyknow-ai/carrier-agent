"""Runtime evaluation scores (spec 3I).

Live traffic has no ground truth, so runtime evals score deterministic proxies
— schema validity, evidence-grounding rate, resolution outcomes, citation
support — and attach them to the unit of work's Langfuse trace, where they
trend over time next to cost and latency. Everything here is failure-isolated:
scoring can never break a pipeline job or an assistant turn.
"""

import logging

from apps.aiops import observability

logger = logging.getLogger(__name__)

TERMINAL_JOB_STATUSES = {"completed", "needs_review"}


def _push(trace_id: str | None, scores: dict) -> None:
    lf = observability.client()
    if lf is None or not trace_id:
        return
    for name, value in scores.items():
        try:
            lf.create_score(trace_id=trace_id, name=name, value=float(value), data_type="NUMERIC")
        except Exception:
            logger.warning("runtime score %s could not be delivered", name)
            return


def score_ingestion_job(job) -> dict | None:
    """Proxy quality for one terminal pipeline execution."""
    try:
        if job.status not in TERMINAL_JOB_STATUSES:
            return None
        from apps.inquiries.models import ExtractionRun, Inquiry, InquiryFieldAssessment

        extraction = ExtractionRun.objects.filter(
            communication_event=job.communication_event, is_current=True
        ).first()
        inquiries = list(Inquiry.objects.filter(communication_event=job.communication_event))
        assessments = InquiryFieldAssessment.objects.filter(
            inquiry__in=inquiries, is_current=True
        ).exclude(evidence_status="missing")
        graded = assessments.count()
        explicit = assessments.filter(evidence_status="explicit").count()
        scores = {
            "extraction_valid": 1.0
            if extraction is not None and extraction.validation_status == "valid"
            else 0.0,
            "grounding_rate": (explicit / graded) if graded else 0.0,
            "carrier_resolved": 1.0
            if any(i.carrier_resolution_status == "verified" for i in inquiries)
            else 0.0,
            "load_resolved": 1.0
            if any(i.load_resolution_status == "verified" for i in inquiries)
            else 0.0,
            "routed_to_review": 1.0 if job.status == "needs_review" else 0.0,
        }
        trace_id = None
        if extraction is not None and extraction.ai_operation_id:
            call = extraction.ai_operation.provider_calls.exclude(langfuse_trace_id=None).first()
            trace_id = call.langfuse_trace_id if call else None
        _push(trace_id, scores)
        return scores
    except Exception:
        logger.warning("ingestion runtime scoring failed", exc_info=True)
        return None


def score_assistant_run(run) -> dict | None:
    """Support/grounding proxies for one assistant turn."""
    try:
        executions = list(run.tool_executions.all())
        completed_tools = sum(1 for e in executions if e.status == "completed")
        citations = run.assistant_message.citations.count() if run.assistant_message_id else 0
        scores = {
            "answer_supported": 1.0 if run.status == "completed" else 0.0,
            "tool_success_rate": (completed_tools / len(executions)) if executions else 1.0,
            "citation_count": float(citations),
        }
        _push(run.langfuse_trace_id, scores)
        return scores
    except Exception:
        logger.warning("assistant runtime scoring failed", exc_info=True)
        return None
