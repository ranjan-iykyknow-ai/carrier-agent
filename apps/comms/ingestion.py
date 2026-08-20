"""The shared ingestion contract (spec 3A.1).

Every source — dataset seed, manual email, manual WAV, future simulation —
enters the pipeline exclusively through IngestionSubmissionService. An accepted
submission atomically creates CommunicationEvent -> channel content ->
IngestionJob; a duplicate identifies the existing records; dispatch happens
only after commit. No provider call ever runs inside this boundary.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.comms.fingerprints import (
    audio_fingerprint,
    canonical_email_address,
    email_fingerprint,
)
from apps.comms.models import (
    CallRecording,
    CommunicationEvent,
    EmailContent,
    IngestionJob,
)
from apps.comms.tasks import process_ingestion_job
from apps.freight.models import DatasetSnapshot

logger = logging.getLogger(__name__)


class DatasetNotReadyError(Exception):
    """No active dataset snapshot exists; manual submissions are refused."""


class SubmissionInvariantError(Exception):
    """The command violates the origin/trust invariants of the boundary."""


@dataclass(frozen=True)
class SubmitEmailCommand:
    origin: str
    sender_email: str
    body_text: str
    submitted_by: str
    sender_name: str = ""
    recipient_emails: str | None = None
    subject: str = ""
    body_html: str | None = None
    occurred_at: datetime | None = None
    source_timestamp_raw: str | None = None
    source_timezone: str | None = None
    source_metadata: dict | None = None
    external_source_id: str | None = None
    dataset_snapshot_id: uuid.UUID | None = None
    import_batch_id: uuid.UUID | None = None
    suspected_load_reference: str | None = None
    suspected_carrier_identity: str | None = None
    dispatch_mode: str = "immediate"


@dataclass(frozen=True)
class SubmitCallCommand:
    origin: str
    content: bytes
    original_filename: str
    mime_type: str
    byte_size: int
    audio_format: str
    submitted_by: str
    duration_seconds: float | None = None
    sample_rate: int | None = None
    # Trusted adapters (seed) may prepare a content-addressed object themselves;
    # manual submissions never set this — the service writes the private object.
    storage_key: str | None = None
    external_source_id: str | None = None
    dataset_snapshot_id: uuid.UUID | None = None
    import_batch_id: uuid.UUID | None = None
    dispatch_mode: str = "immediate"


@dataclass(frozen=True)
class SubmissionResult:
    outcome: str  # "created" | "existing"
    job_id: uuid.UUID
    communication_event_id: uuid.UUID
    stable_evidence_id: str
    status: str
    correlation_id: uuid.UUID
    inquiry_id: uuid.UUID | None = None
    retry_available: bool = False
    extra: dict = field(default_factory=dict)


class IngestionSubmissionService:
    def submit_email(self, command: SubmitEmailCommand) -> SubmissionResult:
        received_at = timezone.now()
        self._enforce_invariants(command)
        snapshot = self._resolve_snapshot(command)

        fingerprint = email_fingerprint(command.sender_email, command.subject, command.body_text)
        existing = self._find_existing(fingerprint)
        if existing:
            return self._existing_result(existing)

        event_id = uuid.uuid4()
        if command.origin == "manual":
            stable_evidence_id = f"email:manual:{event_id}"
            occurred_at = self._demo_clock(snapshot, received_at)
        else:
            stable_evidence_id = f"email:{command.external_source_id}"
            occurred_at = command.occurred_at

        try:
            with transaction.atomic():
                event = CommunicationEvent.objects.create(
                    id=event_id,
                    dataset_snapshot=snapshot,
                    channel="email",
                    origin=command.origin,
                    external_source_id=command.external_source_id,
                    stable_evidence_id=stable_evidence_id,
                    source_timestamp_raw=command.source_timestamp_raw,
                    occurred_at=occurred_at,
                    source_timezone=command.source_timezone
                    or (snapshot.display_timezone if command.origin == "manual" else None),
                    received_at=received_at,
                    content_fingerprint=fingerprint,
                    import_batch_id=command.import_batch_id,
                )
                EmailContent.objects.create(
                    communication_event=event,
                    sender_email_raw=command.sender_email,
                    sender_email_normalized=canonical_email_address(command.sender_email),
                    sender_name=command.sender_name or None,
                    recipient_raw=command.recipient_emails,
                    recipient_normalized=(
                        canonical_email_address(command.recipient_emails)
                        if command.recipient_emails
                        else None
                    ),
                    subject=command.subject,
                    body_text=command.body_text,
                    body_html=command.body_html,
                    source_metadata=command.source_metadata or {},
                )
                job = self._create_job(event, command, fingerprint, received_at)
        except IntegrityError:
            # Two racing submissions resolve atomically to one record.
            existing = self._find_existing(fingerprint)
            if existing:
                return self._existing_result(existing)
            raise

        return self._created_result(event, job)

    def submit_call(self, command: SubmitCallCommand) -> SubmissionResult:
        received_at = timezone.now()
        self._enforce_invariants(command)
        snapshot = self._resolve_snapshot(command)

        fingerprint = audio_fingerprint(command.content)
        existing = self._find_existing(fingerprint)
        if existing:
            # A duplicate never writes a new storage object.
            return self._existing_result(existing)

        event_id = uuid.uuid4()
        if command.origin == "manual":
            stable_evidence_id = f"call:manual:{event_id}"
            occurred_at = self._demo_clock(snapshot, received_at)
        else:
            stable_evidence_id = f"call:{command.external_source_id}"
            occurred_at = None  # dataset calls carry no reliable source time

        # Private-object preparation happens outside the transaction; the
        # original filename never controls the storage key. A trusted adapter
        # may have prepared a content-addressed object already; compensation
        # deletes only objects this service wrote itself.
        service_wrote_object = command.storage_key is None
        storage_key = command.storage_key or default_storage.save(
            f"calls/{event_id}.wav", ContentFile(command.content)
        )
        try:
            with transaction.atomic():
                event = CommunicationEvent.objects.create(
                    id=event_id,
                    dataset_snapshot=snapshot,
                    channel="call",
                    origin=command.origin,
                    external_source_id=command.external_source_id,
                    stable_evidence_id=stable_evidence_id,
                    occurred_at=occurred_at,
                    source_timezone=(
                        snapshot.display_timezone if command.origin == "manual" else None
                    ),
                    received_at=received_at,
                    content_fingerprint=fingerprint,
                    import_batch_id=command.import_batch_id,
                )
                CallRecording.objects.create(
                    communication_event=event,
                    original_filename=command.original_filename,
                    storage_key=storage_key,
                    mime_type=command.mime_type,
                    byte_size=command.byte_size,
                    duration_seconds=command.duration_seconds,
                    sha256_checksum=hashlib.sha256(command.content).hexdigest(),
                    audio_format=command.audio_format,
                    sample_rate=command.sample_rate,
                    uploaded_at=received_at if command.origin == "manual" else None,
                )
                job = self._create_job(event, command, fingerprint, received_at)
        except IntegrityError:
            if service_wrote_object:
                default_storage.delete(storage_key)
            existing = self._find_existing(fingerprint)
            if existing:
                return self._existing_result(existing)
            raise
        except Exception:
            if service_wrote_object:
                default_storage.delete(storage_key)
            raise

        return self._created_result(event, job)

    # ----- shared internals -----

    def _enforce_invariants(self, command) -> None:
        if command.origin == "manual":
            problems = []
            if command.import_batch_id is not None:
                problems.append("import_batch_id")
            if command.external_source_id is not None:
                problems.append("external_source_id")
            if getattr(command, "source_metadata", None):
                problems.append("source_metadata")
            if getattr(command, "occurred_at", None) is not None:
                problems.append("occurred_at")
            if command.dataset_snapshot_id is not None:
                problems.append("dataset_snapshot_id")
            if command.dispatch_mode != "immediate":
                problems.append("dispatch_mode")
            if getattr(command, "storage_key", None) is not None:
                problems.append("storage_key")
            if problems:
                raise SubmissionInvariantError(
                    f"manual submissions cannot set trusted context: {', '.join(problems)}"
                )
        elif command.origin in {"dataset", "simulation"}:
            if not command.external_source_id:
                raise SubmissionInvariantError("trusted origins require external_source_id")
            if command.dataset_snapshot_id is None:
                raise SubmissionInvariantError("trusted origins require dataset_snapshot_id")
        else:
            raise SubmissionInvariantError(f"unknown origin {command.origin!r}")

    def _resolve_snapshot(self, command) -> DatasetSnapshot:
        if command.dataset_snapshot_id is not None:
            return DatasetSnapshot.objects.get(id=command.dataset_snapshot_id)
        snapshot = DatasetSnapshot.objects.filter(is_active=True).first()
        if snapshot is None:
            raise DatasetNotReadyError(
                "No active dataset snapshot exists; complete dataset seeding first."
            )
        return snapshot

    @staticmethod
    def _demo_clock(snapshot: DatasetSnapshot, received_at: datetime) -> datetime:
        """Demo date from the snapshot clock, real local time of day (spec 3A.1)."""
        tz = ZoneInfo(snapshot.display_timezone)
        demo_date = snapshot.as_of_at.astimezone(tz).date()
        local_time = received_at.astimezone(tz).time()
        return datetime.combine(demo_date, local_time, tzinfo=tz)

    def _create_job(self, event, command, fingerprint, received_at) -> IngestionJob:
        job = IngestionJob.objects.create(
            origin=command.origin,
            source_type=event.channel,
            communication_event=event,
            import_batch_id=command.import_batch_id,
            submitted_by=command.submitted_by,
            suspected_load_reference_raw=getattr(command, "suspected_load_reference", None),
            suspected_carrier_identity_raw=getattr(command, "suspected_carrier_identity", None),
            content_fingerprint=fingerprint,
            submitted_at=received_at,
        )
        if command.dispatch_mode == "immediate":
            transaction.on_commit(lambda: self._dispatch(job.id))
        return job

    @staticmethod
    def _dispatch(job_id: uuid.UUID) -> None:
        """Post-commit dispatch. Never raises: Django would propagate exceptions
        from non-robust on_commit callbacks into the caller."""
        try:
            process_ingestion_job.delay(str(job_id))
        except Exception:
            logger.exception("dispatch failed for job %s; marking queue_unavailable", job_id)
            IngestionJob.objects.filter(id=job_id, status=IngestionJob.Status.QUEUED).update(
                status=IngestionJob.Status.FAILED,
                last_error_code="queue_unavailable",
                last_error_summary="The task queue was unavailable at dispatch time.",
            )

    def _find_existing(self, fingerprint: str) -> CommunicationEvent | None:
        return (
            CommunicationEvent.objects.filter(content_fingerprint=fingerprint)
            .select_related("ingestion_job")
            .first()
        )

    def _existing_result(self, event: CommunicationEvent) -> SubmissionResult:
        job = event.ingestion_job
        return SubmissionResult(
            outcome="existing",
            job_id=job.id,
            communication_event_id=event.id,
            stable_evidence_id=event.stable_evidence_id,
            status=job.status,
            correlation_id=job.correlation_id,
            inquiry_id=self._select_inquiry(event, job.status),
            retry_available=job.status == IngestionJob.Status.FAILED,
        )

    def _created_result(self, event, job) -> SubmissionResult:
        return SubmissionResult(
            outcome="created",
            job_id=job.id,
            communication_event_id=event.id,
            stable_evidence_id=event.stable_evidence_id,
            status=job.status,
            correlation_id=job.correlation_id,
        )

    @staticmethod
    def _select_inquiry(event, job_status) -> uuid.UUID | None:
        """Deterministic navigation: lowest reviewable sequence when the job
        needs review, otherwise the lowest sequence overall."""
        inquiries = event.inquiries.order_by("sequence_number")
        if job_status == IngestionJob.Status.NEEDS_REVIEW:
            reviewable = inquiries.filter(review_status="needs_review").first()
            if reviewable:
                return reviewable.id
        first = inquiries.first()
        return first.id if first else None
