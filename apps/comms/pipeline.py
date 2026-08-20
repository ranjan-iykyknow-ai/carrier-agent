"""Worker orchestration (spec 3C).

Claims a queued job atomically, captures the retry generation, branches on
channel, reuses compatible transcripts/extractions deterministically, and hands
a valid proposal to the 3F finalization transaction. Provider adapters are
injected seams: the default implementations (OpenAI, Deepgram) live in
apps.aiops.providers, and tests replace them with stubs at this boundary.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.comms.models import IngestionJob, Transcript, TranscriptSegment
from apps.inquiries.extraction_schema import (
    SCHEMA_VERSION,
    ExtractionValidationError,
    parse_extraction,
)
from apps.inquiries.models import ExtractionRun
from apps.inquiries.reconciliation import STALE, finalize_communication

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """A safe, coded processing failure that fails the job visibly."""

    def __init__(self, code: str, summary: str, *, transient: bool = False):
        super().__init__(summary)
        self.code = code
        self.summary = summary
        self.transient = transient


def process_job(job_id: str, *, extractor=None, transcriber=None) -> None:
    claimed = _claim(job_id)
    if claimed is None:
        return
    job, generation = claimed

    try:
        extractor = extractor or _default_extractor()
        event = job.communication_event
        transcript = None
        if event.channel == "call":
            transcriber = transcriber or _default_transcriber()
            transcript = _ensure_transcript(job, generation, transcriber)
        run = _ensure_extraction(job, generation, extractor, transcript)
        status = finalize_communication(run, generation=generation)
        if status == STALE:
            logger.warning("job %s: stale generation %s at finalization", job.id, generation)
    except PipelineError as exc:
        _fail(job.id, generation, exc.code, exc.summary)
    except Exception:
        logger.exception("job %s: unexpected processing failure", job.id)
        _fail(job.id, generation, "internal_error", "Unexpected processing failure.")


def _claim(job_id: str):
    """Atomic queued -> processing transition; captures the retry generation."""
    with transaction.atomic():
        job = (
            IngestionJob.objects.select_for_update()
            .select_related("communication_event")
            .filter(id=job_id)
            .first()
        )
        if job is None or job.status != IngestionJob.Status.QUEUED:
            return None
        job.status = IngestionJob.Status.PROCESSING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at", "updated_at"])
        return job, job.retry_count


def _fail(job_id, generation, code, summary):
    with transaction.atomic():
        IngestionJob.objects.select_for_update().filter(
            id=job_id,
            status=IngestionJob.Status.PROCESSING,
            retry_count=generation,
        ).update(
            status=IngestionJob.Status.FAILED,
            last_error_code=code,
            last_error_summary=summary,
            completed_at=timezone.now(),
        )


def _fence_holds(job_id, generation) -> bool:
    return IngestionJob.objects.filter(
        id=job_id, status=IngestionJob.Status.PROCESSING, retry_count=generation
    ).exists()


# ---------------------------------------------------------------------------
# Transcription (3E)
# ---------------------------------------------------------------------------


def _ensure_transcript(job, generation, transcriber) -> Transcript:
    recording = job.communication_event.call_recording

    existing = Transcript.objects.filter(
        call_recording=recording,
        is_current=True,
        requested_model=transcriber.requested_model,
        requested_options=transcriber.requested_options,
    ).first()
    if existing is not None:
        return existing

    result, operation = transcriber.transcribe(
        recording,
        correlation_id=job.correlation_id,
        trace_seed=f"job:{job.correlation_id}:gen:{generation}",
    )
    segments = result.get("segments") or []
    if not result.get("raw_text") or not segments:
        raise PipelineError("transcription_failed", "The provider returned no usable speech.")
    previous = None
    for segment in segments:
        if previous is not None and float(segment.start_seconds) < float(previous):
            raise PipelineError("transcription_failed", "The provider returned unordered segments.")
        previous = segment.start_seconds

    with transaction.atomic():
        fence_holds = _fence_holds(job.id, generation)
        if fence_holds:
            Transcript.objects.filter(call_recording=recording, is_current=True).update(
                is_current=False
            )
        transcript = Transcript.objects.create(
            call_recording=recording,
            ingestion_job=job,
            retry_generation=generation,
            provider="deepgram",
            requested_model=transcriber.requested_model,
            requested_options=transcriber.requested_options,
            provider_metadata=result.get("provider_metadata") or {},
            provider_request_id=result.get("provider_request_id"),
            language=result.get("language"),
            raw_text=result["raw_text"],
            normalized_text=result.get("normalized_text") or result["raw_text"],
            provider_response=result.get("provider_response") or {},
            ai_operation=operation,
            # A stale execution persists its output as non-current diagnostics.
            is_current=fence_holds,
        )
        TranscriptSegment.objects.bulk_create(
            TranscriptSegment(
                transcript=transcript,
                sequence=segment.sequence,
                speaker_label=segment.speaker_label,
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                text=segment.text,
                confidence=segment.confidence,
            )
            for segment in segments
        )
    return transcript


# ---------------------------------------------------------------------------
# Extraction (3B/3D)
# ---------------------------------------------------------------------------


def _ensure_extraction(job, generation, extractor, transcript) -> ExtractionRun:
    event = job.communication_event
    prompt = extractor.resolve_prompt(event.channel)

    # Deterministic reuse: same schema + prompt version + model, current runs
    # only; call extraction additionally keys on the current transcript's PK.
    reuse = ExtractionRun.objects.filter(
        communication_event=event,
        is_current=True,
        validation_status=ExtractionRun.ValidationStatus.VALID,
        schema_version=SCHEMA_VERSION,
        prompt_version=prompt.version,
        model=extractor.model,
    )
    if transcript is not None:
        reuse = reuse.filter(transcript=transcript)
    existing = reuse.first()
    if existing is not None:
        return existing

    document = _build_document(event, transcript)
    raw_output, operation = extractor.extract(
        prompt,
        document,
        correlation_id=job.correlation_id,
        trace_seed=f"job:{job.correlation_id}:gen:{generation}",
    )
    raw_output = _strip_nul(raw_output)

    try:
        parse_extraction(raw_output)
        validation_status = ExtractionRun.ValidationStatus.VALID
        errors = None
    except ExtractionValidationError as exc:
        validation_status = ExtractionRun.ValidationStatus.INVALID
        errors = {"summary": str(exc)}

    with transaction.atomic():
        fence_holds = _fence_holds(job.id, generation)
        becomes_current = fence_holds and validation_status == (
            ExtractionRun.ValidationStatus.VALID
        )
        if becomes_current:
            ExtractionRun.objects.filter(communication_event=event, is_current=True).update(
                is_current=False
            )
        run = ExtractionRun.objects.create(
            communication_event=event,
            ingestion_job=job,
            retry_generation=generation,
            transcript=transcript,
            schema_version=SCHEMA_VERSION,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_source=prompt.source,
            model=extractor.model,
            raw_output=raw_output,
            validated_output=raw_output if validation_status == "valid" else None,
            validation_status=validation_status,
            validation_errors=errors,
            ai_operation=operation,
            is_current=becomes_current,
        )
    if validation_status == ExtractionRun.ValidationStatus.INVALID:
        raise PipelineError(
            "extraction_schema_failure",
            "The extraction did not match the versioned schema.",
        )
    return run


def _strip_nul(value):
    """PostgreSQL JSONB cannot store \\u0000; models occasionally emit it."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {key: _strip_nul(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    return value


def _build_document(event, transcript) -> dict:
    """The deterministic extraction input document (no hints, no metadata)."""
    if event.channel == "email":
        email = event.email_content
        return {
            "channel": "email",
            "sender_name": email.sender_name or "",
            "sender_email": email.sender_email_raw,
            "subject": email.subject,
            "body": email.body_text,
        }
    return {
        "channel": "call",
        "segments": [
            {
                "sequence": segment.sequence,
                "speaker": segment.speaker_label,
                "start": str(segment.start_seconds),
                "end": str(segment.end_seconds),
                "text": segment.text,
            }
            for segment in transcript.segments.all()
        ],
    }


def _default_extractor():
    from apps.aiops.providers import OpenAIExtractor

    return OpenAIExtractor(model=settings.OPENAI_MODEL_EXTRACTION)


def _default_transcriber():
    from apps.aiops.providers import DeepgramTranscriber

    return DeepgramTranscriber(model=settings.DEEPGRAM_MODEL)
