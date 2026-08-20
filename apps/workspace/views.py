"""Inquiry Review: source evidence beside extraction, with auditable broker actions."""

import re
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.aiops import observability
from apps.comms.models import IngestionJob
from apps.freight.models import Carrier, Load
from apps.inquiries.models import Inquiry
from apps.workspace import review as review_service

_DIGITS = re.compile(r"\D")

REASON_HINTS = {
    "missing_mc": "No MC number was stated, so identity cannot be verified deterministically.",
    "garbled_mc": "The stated MC number is unreadable or malformed.",
    "weak_carrier_match": "Only name similarity supports this carrier; confirm or correct it.",
    "conflicting_carrier_identity": "Exact identity signals point at different carriers.",
    "ambiguous_load_reference": "The stated load reference matches nothing in the snapshot.",
    "conflicting_load_reference": "Load references in this communication contradict each other.",
    "equipment_conflict": "Stated equipment conflicts with other evidence.",
    "ambiguous_rate": "A money amount was mentioned without a clear role or basis.",
    "missing_rate": "A rate discussion was detected but no usable amount was found.",
    "conflicting_availability": "Availability statements contradict each other.",
    "low_confidence_transcript_evidence": "A critical field relies on uncertain audio.",
    "quote_chronology_unresolved": "Conflicting quotes lack comparable timestamps.",
    "metadata_content_conflict": "Dataset annotations diverge from the message content.",
    "extraction_validation_failure": "The model output failed schema validation.",
}

FIELD_LABELS = {
    "carrier_identity": "Carrier identity",
    "load_reference": "Load reference",
    "equipment": "Equipment",
    "availability": "Availability",
    "intent": "Intent",
    "rate": "Rate",
    "question": "Question",
    "conditions": "Conditions",
}


def _highlight(text, ranges):
    """Split text into pieces with grounded-evidence ranges marked."""
    cleaned = []
    for start, end in ranges:
        if start is None or end is None:
            continue
        start, end = max(0, int(start)), min(len(text), int(end))
        if end > start:
            cleaned.append((start, end))
    merged: list[list[int]] = []
    for start, end in sorted(cleaned):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    pieces, cursor = [], 0
    for start, end in merged:
        if start > cursor:
            pieces.append({"text": text[cursor:start], "hit": False})
        pieces.append({"text": text[start:end], "hit": True})
        cursor = end
    if cursor < len(text):
        pieces.append({"text": text[cursor:], "hit": False})
    return pieces or [{"text": text, "hit": False}]


def _actor(request) -> str:
    return request.user.email or request.user.username


@login_required
def inquiry_review(request, pk):
    inquiry = get_object_or_404(
        Inquiry.objects.select_related(
            "communication_event__dataset_snapshot",
            "carrier",
            "load",
            "equipment_type",
            "current_extraction",
        ),
        pk=pk,
    )
    event = inquiry.communication_event
    email = getattr(event, "email_content", None) if event.channel == "email" else None
    recording = getattr(event, "call_recording", None) if event.channel == "call" else None
    spans = list(event.evidence_spans.all())

    body_pieces = subject_pieces = None
    if email:
        body_pieces = _highlight(
            email.body_text,
            [(s.start_offset, s.end_offset) for s in spans if s.source_part == "body"],
        )
        subject_pieces = _highlight(
            email.subject,
            [(s.start_offset, s.end_offset) for s in spans if s.source_part == "subject"],
        )

    transcript, segments = None, []
    if recording:
        transcript = recording.transcripts.filter(is_current=True).first()
        cited_ids = {s.transcript_segment_id for s in spans if s.transcript_segment_id}
        if transcript:
            threshold = settings.TRANSCRIPT_LOW_CONFIDENCE_THRESHOLD
            segments = [
                {
                    "segment": segment,
                    "uncertain": segment.confidence is not None
                    and float(segment.confidence) < threshold,
                    "cited": segment.id in cited_ids,
                }
                for segment in transcript.segments.all()
            ]

    assessments = [
        {
            "assessment": assessment,
            "label": FIELD_LABELS.get(assessment.field_name, assessment.field_name),
            "spans": [
                link.evidence_span for link in assessment.evidence_links.all() if link.evidence_span
            ],
        }
        for assessment in inquiry.field_assessments.filter(is_current=True).prefetch_related(
            "evidence_links__evidence_span"
        )
    ]
    reasons = list(inquiry.review_reasons.order_by("created_at"))
    for reason in reasons:
        reason.hint = REASON_HINTS.get(reason.code, "")

    trace_id = None
    if inquiry.current_extraction and inquiry.current_extraction.ai_operation_id:
        traced = (
            inquiry.current_extraction.ai_operation.provider_calls.exclude(langfuse_trace_id=None)
            .order_by("-sequence")
            .first()
        )
        trace_id = traced.langfuse_trace_id if traced else None
    carrier_q = request.GET.get("carrier_q", "").strip()
    carrier_results = []
    if carrier_q:
        digits = _DIGITS.sub("", carrier_q)
        filters = Q(company_name__icontains=carrier_q) | Q(
            contacts__email_normalized__icontains=carrier_q.casefold()
        )
        if digits:
            filters |= Q(mc_number_normalized=digits) | Q(dot_number_normalized=digits)
        carrier_results = list(
            Carrier.objects.filter(dataset_snapshot=event.dataset_snapshot)
            .filter(filters)
            .distinct()
            .order_by("company_name")[:8]
        )

    return render(
        request,
        "workspace/inquiry_review.html",
        {
            "inquiry": inquiry,
            "event": event,
            "email": email,
            "recording": recording,
            "transcript": transcript,
            "segments": segments,
            "body_pieces": body_pieces,
            "subject_pieces": subject_pieces,
            "assessments": assessments,
            "quotes": inquiry.quotes.order_by("created_at"),
            "pending_reasons": [
                r for r in reasons if r.resolved_at is None and r.severity == "review"
            ],
            "warning_reasons": [r for r in reasons if r.severity == "informational"],
            "resolved_reasons": [
                r for r in reasons if r.resolved_at is not None and r.severity == "review"
            ],
            "carrier_matches": inquiry.carrier_matches.select_related("carrier").order_by(
                "-is_selected", "match_tier", "created_at"
            ),
            "load_matches": inquiry.load_matches.select_related("load").order_by(
                "-is_selected", "created_at"
            ),
            "actions": inquiry.review_actions.select_related(
                "previous_carrier", "new_carrier", "previous_load", "new_load"
            ).order_by("-created_at"),
            "siblings": event.inquiries.exclude(pk=inquiry.pk).order_by("sequence_number"),
            "job": IngestionJob.objects.filter(communication_event=event).first(),
            "carrier_q": carrier_q,
            "carrier_results": carrier_results,
            "trace_url": observability.trace_url(trace_id),
            "error": request.GET.get("error", ""),
            "questions": inquiry.questions.all(),
            "intents": inquiry.intents.order_by("-is_primary"),
        },
    )


@login_required
@require_POST
def inquiry_action(request, pk, action):
    inquiry = get_object_or_404(Inquiry.objects.select_related("communication_event"), pk=pk)
    note = request.POST.get("note", "").strip()
    actor = _actor(request)
    destination = reverse("inquiry_review", args=[inquiry.pk])
    try:
        if action == "approve":
            review_service.approve_extraction(inquiry, actor_label=actor, note=note)
        elif action == "reject":
            review_service.reject_extraction(inquiry, actor_label=actor, note=note)
        elif action == "reopen":
            review_service.reopen_review(inquiry, actor_label=actor, note=note)
        elif action == "correct-carrier":
            carrier = _find_carrier(request.POST.get("carrier_id", ""))
            review_service.correct_carrier(inquiry, carrier, actor_label=actor, note=note)
        elif action == "correct-load":
            load = _find_load(inquiry, request.POST.get("external_load_id", ""))
            review_service.correct_load(inquiry, load, actor_label=actor, note=note)
        else:
            raise Http404
    except review_service.ReviewError as error:
        return redirect(f"{destination}?{urlencode({'error': error.summary})}")
    return redirect(destination)


def _find_carrier(raw_id: str) -> Carrier:
    try:
        carrier = Carrier.objects.filter(pk=raw_id.strip()).first()
    except (ValidationError, ValueError):
        carrier = None
    if carrier is None:
        raise review_service.ReviewError("unknown_carrier", "select a carrier to confirm")
    return carrier


def _find_load(inquiry, raw_reference: str) -> Load:
    reference = _DIGITS.sub("", raw_reference)
    load = (
        Load.objects.filter(
            dataset_snapshot=inquiry.communication_event.dataset_snapshot_id,
            external_load_id=reference,
        ).first()
        if reference
        else None
    )
    if load is None:
        raise review_service.ReviewError(
            "unknown_load", f"no load in this snapshot matches “{raw_reference.strip()}”"
        )
    return load
