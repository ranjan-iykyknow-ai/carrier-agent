from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from apps.comms.models import CallRecording, CommunicationEvent, IngestionJob
from apps.freight.models import DatasetSnapshot

ATTENTION_STATUSES = {IngestionJob.Status.NEEDS_REVIEW, IngestionJob.Status.FAILED}

INTENT_LABELS = {
    "availability": "Availability",
    "rate_quote": "Rate inquiry",
    "rate_negotiation": "Rate counteroffer",
    "load_detail_question": "Load details",
    "compliance": "Compliance info",
    "confirmation": "Confirmation",
    "decline": "Decline",
    "factoring_or_payment": "Factoring / payment",
    "problem_or_exception": "Problem / exception",
    "general_inquiry": "General inquiry",
    "other": "Other",
}

STATE_CHIPS = {
    "queued": ("Queued", "chip-muted"),
    "processing": ("Processing", "chip-info"),
    "completed": ("Processed", "chip-ok"),
    "needs_review": ("Needs review", "chip-warn"),
    "failed": ("Failed · retryable", "chip-danger"),
}


def _select_inquiry(job, inquiries):
    """Deterministic review destination (spec 3A): the lowest sequence needing
    review for a needs_review job, otherwise the lowest sequence overall."""
    if not inquiries:
        return None
    if job and job.status == IngestionJob.Status.NEEDS_REVIEW:
        for inquiry in inquiries:
            if inquiry.review_status == "needs_review":
                return inquiry
    return inquiries[0]


def _row(event):
    job = getattr(event, "ingestion_job", None)
    inquiries = sorted(event.inquiries.all(), key=lambda inquiry: inquiry.sequence_number)
    inquiry = _select_inquiry(job, inquiries)
    email = getattr(event, "email_content", None) if event.channel == "email" else None
    state_label, state_class = STATE_CHIPS.get(job.status if job else "queued", ("", ""))
    return {
        "event": event,
        "email": email,
        "job": job,
        "inquiry": inquiry,
        "url": reverse("inquiry_review", args=[inquiry.pk]) if inquiry else None,
        "intent_label": INTENT_LABELS.get(inquiry.primary_intent) if inquiry else None,
        "state_label": state_label,
        "state_class": state_class,
        "when": event.occurred_at or event.received_at,
        "when_is_fallback": event.occurred_at is None,
    }


@login_required
def inbox(request):
    snapshot = DatasetSnapshot.objects.filter(is_active=True).first()
    events = (
        CommunicationEvent.objects.filter(dataset_snapshot=snapshot)
        .select_related("email_content", "ingestion_job")
        .prefetch_related("inquiries")
        if snapshot
        else CommunicationEvent.objects.none()
    )

    source = request.GET.get("source")
    if source in ("email", "call"):
        events = events.filter(channel=source)
    state = request.GET.get("state")
    if state in STATE_CHIPS:
        events = events.filter(ingestion_job__status=state)
    query = request.GET.get("q", "").strip()
    if query:
        from django.db.models import Q

        events = events.filter(
            Q(email_content__sender_email_raw__icontains=query)
            | Q(email_content__sender_name__icontains=query)
            | Q(email_content__subject__icontains=query)
            | Q(external_source_id__icontains=query)
            | Q(inquiries__carrier__company_name__icontains=query)
            | Q(inquiries__load__external_load_id__icontains=query)
        ).distinct()

    rows = [_row(event) for event in events]
    rows.sort(key=lambda r: r["when"], reverse=True)
    attention = [r for r in rows if r["job"] and r["job"].status in ATTENTION_STATUSES]
    regular = [r for r in rows if not (r["job"] and r["job"].status in ATTENTION_STATUSES)]

    return render(
        request,
        "comms/inbox.html",
        {
            "attention": attention,
            "regular": regular,
            "total": len(rows),
            "state": state or "",
            "source": source or "",
            "query": query,
            "state_options": STATE_CHIPS,
        },
    )


@login_required
def call_audio(request, pk):
    """Authenticated streaming of raw call evidence for review playback."""
    recording = get_object_or_404(CallRecording, pk=pk)
    try:
        stream = default_storage.open(recording.storage_key, "rb")
    except FileNotFoundError:
        raise Http404 from None
    return FileResponse(stream, content_type=recording.mime_type or "audio/wav")
