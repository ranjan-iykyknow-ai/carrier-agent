"""Response drafting (spec 3H).

A bounded synchronous operation: build an immutable grounded context, make one
structured provider request, persist the draft with deterministic evidence
links and full accounting. The application never sends email; drafts have no
"sent" state. Internal load notes and private carrier notes are never part of
the drafting context.
"""

import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel

from apps.aiops import observability
from apps.aiops.models import AIOperation, AIProviderCall
from apps.aiops.prompts import draft_prompt
from apps.aiops.providers import (
    PRICING_VERSION,
    ProviderFailure,
    _openai_cost,
    _pricing_for,
    resolve_prompt_chain,
    strict_schema,
)
from apps.candidates.models import CarrierLoadCandidate
from apps.candidates.selectors import market_context, quote_as_per_mile
from apps.comms.models import IngestionJob
from apps.workspace.models import DraftResponse


class DraftError(Exception):
    def __init__(self, code: str, summary: str):
        super().__init__(summary)
        self.code = code
        self.summary = summary


class DraftProposal(BaseModel):
    model_config = {"extra": "forbid"}
    subject: str
    body: str


DRAFT_INSTRUCTIONS = {
    "provide_rate": "Provide the load's offered rate to the carrier.",
    "negotiate_rate": (
        "Respond to the carrier's position with a negotiation counterposition "
        "grounded in the offered rate and market band."
    ),
    "request_information": "Request the listed missing information from the carrier.",
    "confirm_next_steps": "Confirm what happens next without committing the load.",
    "decline": "Politely decline the carrier's offer for this load.",
    "defer": "Defer the decision politely, keeping the option open.",
}


def default_client():
    from openai import OpenAI

    return OpenAI(timeout=settings.PROVIDER_TIMEOUT_SECONDS, max_retries=0)


def build_draft_context(inquiry, draft_type) -> dict:
    load, carrier = inquiry.load, inquiry.carrier
    candidate = (
        CarrierLoadCandidate.objects.filter(carrier=carrier, load=load)
        .select_related("current_eligibility_assessment")
        .prefetch_related("current_eligibility_assessment__reasons")
        .first()
        if carrier and load
        else None
    )
    assessment = candidate.current_eligibility_assessment if candidate else None
    quote = inquiry.quotes.filter(is_current=True).first()
    market = market_context(load) if load else None

    context = {
        "draft_type": draft_type,
        "instruction": DRAFT_INSTRUCTIONS.get(draft_type, ""),
        "inquiry_summary": inquiry.summary,
        "availability": inquiry.availability_status,
        "load": None,
        "carrier": None,
        "current_quote": None,
        "eligibility": None,
        "market_band": market.band_position if market else None,
        "missing_information": [
            assessment_row.field_name
            for assessment_row in inquiry.field_assessments.filter(
                is_current=True, evidence_status="missing"
            )
        ],
        "open_questions": [
            question.question_text
            for question in inquiry.questions.filter(answer_status="unanswered")
        ],
    }
    if load:
        context["load"] = {
            "external_load_id": load.external_load_id,
            "origin": f"{load.origin_city}, {load.origin_state}",
            "destination": f"{load.destination_city}, {load.destination_state}",
            "equipment": load.equipment_type.display_name
            if load.equipment_type
            else load.equipment_type_raw,
            "pickup_date": str(load.pickup_date),
            "weight_lbs": load.weight_lbs,
            "offered_rate_usd": str(load.offered_rate_usd) if load.offered_rate_usd else None,
        }
    if carrier:
        contact = carrier.contacts.filter(is_primary=True).first()
        context["carrier"] = {
            "company_name": carrier.company_name,
            "mc_number": carrier.mc_number_raw,
            "contact_name": contact.name if contact else None,
        }
    if quote:
        context["current_quote"] = {
            "amount": str(quote.amount),
            "basis": quote.rate_basis,
            "role": quote.quote_type,
            "per_mile": str(quote_as_per_mile(quote, load)) if load else None,
        }
    if assessment:
        context["eligibility"] = {
            "final_status": assessment.final_status,
            "reasons": [
                {"code": reason.code, "severity": reason.severity}
                for reason in assessment.reasons.all()
            ],
        }
    return context


def _evidence_links(inquiry, context):
    """Deterministic links for the facts the application supplied to the model."""
    links = []
    if inquiry.load:
        links.append(("load", "load", f"load:{inquiry.load.external_load_id}", context["load"]))
    if inquiry.carrier:
        links.append(("carrier", "carrier", f"carrier:{inquiry.carrier_id}", context["carrier"]))
    quote = inquiry.quotes.filter(is_current=True).first()
    if quote:
        links.append(("current_quote", "quote", f"quote:{quote.id}", context["current_quote"]))
    if context["eligibility"]:
        candidate = CarrierLoadCandidate.objects.filter(
            carrier=inquiry.carrier, load=inquiry.load
        ).first()
        if candidate and candidate.current_eligibility_assessment_id:
            links.append(
                (
                    "eligibility_assessment",
                    "assessment",
                    f"assessment:{candidate.current_eligibility_assessment_id}",
                    context["eligibility"],
                )
            )
    return links


def generate_draft(inquiry, draft_type, *, actor_label: str, client=None) -> DraftResponse:
    if inquiry.review_status == "rejected":
        raise DraftError("rejected_inquiry", "A rejected inquiry cannot be drafted against.")
    if inquiry.load is None:
        raise DraftError("no_load", "Resolve the load reference before drafting a reply.")
    if draft_type not in DraftResponse.DraftType.values:
        raise DraftError("unknown_draft_type", "Unsupported draft type.")

    context = build_draft_context(inquiry, draft_type)
    prompt = resolve_prompt_chain(draft_prompt())
    model = settings.OPENAI_MODEL_DRAFTING
    job = IngestionJob.objects.filter(communication_event=inquiry.communication_event).first()

    operation = AIOperation.objects.create(
        operation_type=AIOperation.OperationType.DRAFT,
        usage_category=AIOperation.UsageCategory.DRAFTING,
        correlation_id=job.correlation_id if job else None,
        started_at=timezone.now(),
    )
    recorder = observability.start_generation(
        trace_seed=f"draft:{operation.id}",
        name=f"draft:{draft_type}",
        model=model,
        input=context,
        metadata={"prompt_version": prompt.version, "prompt_source": prompt.source},
    )
    call = AIProviderCall.objects.create(
        operation=operation,
        sequence=1,
        provider=AIProviderCall.Provider.OPENAI,
        operation_name=f"draft:{draft_type}",
        model=model,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        prompt_source=prompt.source,
        langfuse_trace_id=recorder.trace_id,
        langfuse_observation_id=recorder.observation_id,
        started_at=timezone.now(),
        pricing_version=PRICING_VERSION,
        pricing_snapshot=_pricing_for(model),
    )
    client = client or default_client()
    try:
        proposal, usage = _request_draft(client, model, prompt.text, context)
    except ProviderFailure as failure:
        recorder.finish(error=failure.code)
        _finish(call, operation, status="failed", error_code=failure.code)
        observability.flush_safely()
        raise DraftError(failure.code, failure.summary) from failure

    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    cost = _openai_cost(model, prompt_tokens or 0, completion_tokens or 0)
    recorder.finish(
        output=proposal,
        usage={"input": prompt_tokens or 0, "output": completion_tokens or 0},
        cost=cost,
    )
    _finish(
        call,
        operation,
        status="completed",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost=cost,
    )
    observability.flush_safely()

    with transaction.atomic():
        draft = DraftResponse.objects.create(
            inquiry=inquiry,
            candidate=CarrierLoadCandidate.objects.filter(
                carrier=inquiry.carrier, load=inquiry.load
            ).first(),
            load=inquiry.load,
            carrier=inquiry.carrier,
            draft_type=draft_type,
            generated_subject=proposal["subject"],
            generated_body=proposal["body"],
            current_subject=proposal["subject"],
            current_body=proposal["body"],
            context_snapshot=context,
            created_by=actor_label,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            model=model,
            langfuse_trace_id=recorder.trace_id,
            ai_operation=operation,
        )
        for fact_name, source_type, stable_id, value in _evidence_links(inquiry, context):
            draft.evidence_links.create(
                source_type=source_type,
                stable_source_id=stable_id,
                fact_name=fact_name,
                fact_value=value,
            )
    return draft


def _request_draft(client, model, system_text, context):
    import openai

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "draft_proposal",
            "strict": True,
            "schema": strict_schema(DraftProposal),
        },
    }
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": json.dumps(context)},
            ],
            response_format=response_format,
            reasoning_effort=settings.OPENAI_REASONING_EFFORT_DRAFTING,
        )
    except (openai.APITimeoutError, openai.APIConnectionError) as exc:
        raise ProviderFailure(
            "provider_timeout", "The drafting model did not respond in time.", transient=True
        ) from exc
    except openai.RateLimitError as exc:
        raise ProviderFailure(
            "provider_rate_limited", "OpenAI rate limit reached.", transient=True
        ) from exc
    except openai.APIStatusError as exc:
        raise ProviderFailure(
            "provider_error", "OpenAI rejected the drafting request.", transient=False
        ) from exc
    content = response.choices[0].message.content
    try:
        proposal = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProviderFailure(
            "provider_error", "The drafting model returned unusable output.", transient=False
        ) from exc
    if not proposal.get("body"):
        raise ProviderFailure(
            "provider_error", "The drafting model returned an empty draft.", transient=False
        )
    usage = {
        "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
        "completion_tokens": getattr(response.usage, "completion_tokens", None),
    }
    return proposal, usage


def _finish(call, operation, *, status, error_code=None, **fields):
    for target in (call, operation):
        for key, value in fields.items():
            setattr(target, key, value)
    now = timezone.now()
    call.status = status
    call.error_code = error_code
    call.completed_at = now
    if call.started_at:
        call.latency_ms = int((now - call.started_at).total_seconds() * 1000)
    call.save()
    if fields.get("prompt_tokens") is not None:
        operation.total_tokens = (fields.get("prompt_tokens") or 0) + (
            fields.get("completion_tokens") or 0
        )
    operation.provider_call_count = operation.provider_calls.count()
    operation.status = status
    operation.last_error_code = error_code
    operation.completed_at = now
    if operation.started_at:
        operation.latency_ms = int((now - operation.started_at).total_seconds() * 1000)
    operation.save()
