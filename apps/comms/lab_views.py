"""Manual Ingestion Lab (spec 3A.3/3A.4): the same pipeline, hand-fed.

Preview never touches the database or a provider. Submission goes through the
shared IngestionSubmissionService, so fingerprints, duplicates, dispatch, and
demo-clock behavior are identical to the dataset seed.
"""

from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.comms import recovery
from apps.comms.forms import ManualAudioForm, ManualEmailForm
from apps.comms.ingestion import (
    DatasetNotReadyError,
    IngestionSubmissionService,
    SubmitCallCommand,
    SubmitEmailCommand,
)
from apps.comms.models import IngestionJob
from apps.freight.models import DatasetSnapshot

TERMINAL_STATUSES = {
    IngestionJob.Status.COMPLETED,
    IngestionJob.Status.NEEDS_REVIEW,
    IngestionJob.Status.FAILED,
}

STATUS_EXPLANATIONS = {
    "queued": "Accepted and waiting for a worker. Background processing continues on its own.",
    "processing": "A worker has claimed this job and is transcribing/extracting now.",
    "completed": "Processing finished. The result is linked below.",
    "needs_review": "Processing finished, but a human needs to confirm something.",
    "failed": "Processing failed safely. The original evidence is preserved; retry when ready.",
}


def _active_snapshot():
    return DatasetSnapshot.objects.filter(is_active=True).first()


def _demo_date(snapshot):
    if snapshot is None:
        return None
    return snapshot.as_of_at.astimezone(ZoneInfo(snapshot.display_timezone)).date()


def _page(request, *, email_form=None, audio_form=None, status=200):
    snapshot = _active_snapshot()
    return render(
        request,
        "comms/lab.html",
        {
            "email_form": email_form or ManualEmailForm(),
            "audio_form": audio_form or ManualAudioForm(),
            "snapshot": snapshot,
            "demo_date": _demo_date(snapshot),
            "max_wav_mb": settings.SEED_MAX_WAV_BYTES // (1024 * 1024),
            "max_wav_seconds": settings.SEED_MAX_WAV_SECONDS,
            "max_body": 50_000,
        },
        status=status,
    )


@login_required
def lab(request):
    return _page(request)


@login_required
@require_POST
def email_preview(request):
    form = ManualEmailForm(request.POST)
    return render(
        request,
        "comms/_email_preview.html",
        {
            "form": form,
            "preview": form.cleaned_data if form.is_valid() else None,
            "demo_date": _demo_date(_active_snapshot()),
        },
    )


def _submission_redirect(request, result):
    destination = reverse("lab_job", args=[result.job_id])
    if result.outcome == "existing":
        destination += "?existing=1"
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = destination
        return response
    return HttpResponseRedirect(destination, status=303)


@login_required
@require_POST
def email_submit(request):
    form = ManualEmailForm(request.POST)
    if not form.is_valid():
        return _page(request, email_form=form)
    data = form.cleaned_data
    command = SubmitEmailCommand(
        origin="manual",
        sender_email=data["sender_email"],
        sender_name=data["sender_name"],
        subject=data["subject"],
        body_text=data["body"],
        submitted_by=request.user.email or request.user.username,
        suspected_load_reference=data["suspected_load_reference"] or None,
        suspected_carrier_identity=data["suspected_carrier_identity"] or None,
    )
    try:
        result = IngestionSubmissionService().submit_email(command)
    except DatasetNotReadyError:
        return _page(request, email_form=form, status=503)
    return _submission_redirect(request, result)


@login_required
@require_POST
def audio_submit(request):
    declared_length = int(request.META.get("CONTENT_LENGTH") or 0)
    if declared_length > settings.SEED_MAX_WAV_BYTES + 1_000_000:
        return HttpResponse("The upload exceeds the size limit.", status=413)
    form = ManualAudioForm(request.POST, request.FILES)
    if not form.is_valid():
        return _page(request, audio_form=form)
    upload = form.cleaned_data["audio_file"]
    info = form.wav_info
    command = SubmitCallCommand(
        origin="manual",
        content=upload.read(),
        original_filename=upload.name[:255],
        mime_type="audio/wav",
        byte_size=info.byte_size,
        audio_format="wav",
        duration_seconds=info.duration_seconds,
        sample_rate=info.sample_rate,
        submitted_by=request.user.email or request.user.username,
    )
    try:
        result = IngestionSubmissionService().submit_call(command)
    except DatasetNotReadyError:
        return _page(request, audio_form=form, status=503)
    return _submission_redirect(request, result)


def _job_context(job):
    event = job.communication_event
    inquiry_id = IngestionSubmissionService._select_inquiry(event, job.status)
    inquiry = event.inquiries.filter(id=inquiry_id).first() if inquiry_id else None
    recording = getattr(event, "call_recording", None) if event.channel == "call" else None
    return {
        "job": job,
        "event": event,
        "email": getattr(event, "email_content", None) if event.channel == "email" else None,
        "recording": recording,
        "inquiry": inquiry,
        "polling": job.status not in TERMINAL_STATUSES,
        "explanation": STATUS_EXPLANATIONS.get(job.status, ""),
        "retry_available": job.status == IngestionJob.Status.FAILED
        and job.last_error_code not in recovery.PERMANENT_ERROR_CODES
        and job.retry_count < settings.MAX_JOB_RETRIES,
    }


@login_required
def job_detail(request, pk):
    job = get_object_or_404(IngestionJob.objects.select_related("communication_event"), pk=pk)
    context = _job_context(job)
    context["existing"] = request.GET.get("existing") == "1"
    return render(request, "comms/lab_job.html", context)


@login_required
def job_status(request, pk):
    # Every poll runs the bounded global sweep, closing dispatch crash windows.
    recovery.sweep_stale_jobs()
    job = get_object_or_404(IngestionJob.objects.select_related("communication_event"), pk=pk)
    return render(request, "comms/_job_status.html", _job_context(job))


@login_required
@require_POST
def job_retry(request, pk):
    job = get_object_or_404(IngestionJob, pk=pk)
    if _job_context(job)["retry_available"]:
        recovery.retry_jobs(recovery.RetrySelection(job_ids=[job.id]))
    return redirect("lab_job", pk=job.pk)
