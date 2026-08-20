"""Step 2B — communication and ingestion models.

The communication layer preserves what arrived, independent of what an AI or a
broker later concludes. Raw evidence is append-only; carrier/load associations are
entity-resolution conclusions and live on Inquiry, never here.
"""

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.base import AppendOnlyModel, TimeStampedModel
from apps.freight.models import DatasetSnapshot, ImportBatch


class Channel(models.TextChoices):
    EMAIL = "email"
    CALL = "call"


class Origin(models.TextChoices):
    DATASET = "dataset"
    MANUAL = "manual"
    SIMULATION = "simulation"


class CommunicationEvent(AppendOnlyModel):
    """Immutable envelope shared by email and call evidence."""

    dataset_snapshot = models.ForeignKey(
        DatasetSnapshot, on_delete=models.PROTECT, related_name="communication_events"
    )
    channel = models.CharField(max_length=10, choices=Channel.choices)
    origin = models.CharField(max_length=15, choices=Origin.choices)
    external_source_id = models.CharField(max_length=100, null=True, blank=True)
    stable_evidence_id = models.CharField(max_length=200, unique=True)
    source_timestamp_raw = models.CharField(max_length=100, null=True, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    source_timezone = models.CharField(max_length=64, null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    content_fingerprint = models.CharField(max_length=64, unique=True)
    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="communication_events",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_snapshot", "external_source_id"],
                condition=Q(origin="dataset") & Q(external_source_id__isnull=False),
                name="comms_dataset_event_source_id_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["occurred_at"]),
            models.Index(fields=["channel", "origin"]),
        ]

    def __str__(self):
        return self.stable_evidence_id


class EmailContent(AppendOnlyModel):
    communication_event = models.OneToOneField(
        CommunicationEvent, on_delete=models.CASCADE, related_name="email_content"
    )
    sender_email_raw = models.CharField(max_length=254)
    sender_email_normalized = models.CharField(max_length=254)
    sender_name = models.CharField(max_length=200, blank=True, default="")
    recipient_raw = models.CharField(max_length=254, null=True, blank=True)
    recipient_normalized = models.CharField(max_length=254, null=True, blank=True)
    subject = models.TextField(blank=True, default="")
    body_text = models.TextField()
    body_html = models.TextField(null=True, blank=True)
    # Dataset annotations (mc_number, load_reference, equipment_mentioned, intent, ...)
    # are preserved here as explicitly untrusted values — never extraction output.
    source_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["sender_email_normalized"])]

    def __str__(self):
        return f"Email {self.communication_event.stable_evidence_id}"


class CallRecording(AppendOnlyModel):
    communication_event = models.OneToOneField(
        CommunicationEvent, on_delete=models.CASCADE, related_name="call_recording"
    )
    original_filename = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=500, unique=True)
    mime_type = models.CharField(max_length=100)
    byte_size = models.BigIntegerField()
    duration_seconds = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    sha256_checksum = models.CharField(max_length=64)
    audio_format = models.CharField(max_length=20)
    sample_rate = models.IntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["sha256_checksum"])]

    def __str__(self):
        return f"Recording {self.original_filename}"


class IngestionJob(TimeStampedModel):
    """Durable product-level lifecycle for one logical source submission."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        PROCESSING = "processing"
        COMPLETED = "completed"
        NEEDS_REVIEW = "needs_review"
        FAILED = "failed"

    origin = models.CharField(max_length=15, choices=Origin.choices)
    source_type = models.CharField(max_length=10, choices=Channel.choices)
    communication_event = models.OneToOneField(
        CommunicationEvent, on_delete=models.PROTECT, related_name="ingestion_job"
    )
    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ingestion_jobs",
    )
    submitted_by = models.CharField(max_length=200, null=True, blank=True)
    suspected_load_reference_raw = models.CharField(max_length=100, null=True, blank=True)
    suspected_carrier_identity_raw = models.CharField(max_length=200, null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.QUEUED)
    retry_count = models.PositiveIntegerField(default=0)
    content_fingerprint = models.CharField(max_length=64)
    correlation_id = models.UUIDField(null=True, blank=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=100, null=True, blank=True)
    last_error_summary = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["content_fingerprint"]),
        ]

    def __str__(self):
        return f"Job {self.id} ({self.status})"


class Transcript(AppendOnlyModel):
    """Append-only transcription result; a retranscription creates a new row."""

    call_recording = models.ForeignKey(
        CallRecording, on_delete=models.CASCADE, related_name="transcripts"
    )
    ingestion_job = models.ForeignKey(
        IngestionJob, on_delete=models.PROTECT, related_name="transcripts"
    )
    # Whole-job retry generation (job.retry_count captured at claim) of the execution
    # that produced this row — the write-time fence marker; no per-execution entity
    # exists in P0.
    retry_generation = models.PositiveIntegerField()
    provider = models.CharField(max_length=50)
    requested_model = models.CharField(max_length=100)
    # Requested transcription options identity — the reuse-comparison key; resolved
    # provider/diarizer metadata is explanation only and lives in provider_metadata.
    requested_options = models.JSONField(default=dict, blank=True)
    provider_metadata = models.JSONField(default=dict, blank=True)
    provider_request_id = models.CharField(max_length=200, null=True, blank=True)
    language = models.CharField(max_length=20, null=True, blank=True)
    raw_text = models.TextField()
    normalized_text = models.TextField()
    provider_response = models.JSONField(default=dict, blank=True)
    is_current = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["call_recording"],
                condition=Q(is_current=True),
                name="comms_one_current_transcript_per_recording",
            ),
        ]

    def __str__(self):
        return f"Transcript {self.id} ({'current' if self.is_current else 'superseded'})"


class TranscriptSegment(AppendOnlyModel):
    class SpeakerRole(models.TextChoices):
        BROKER = "broker"
        CARRIER = "carrier"

    transcript = models.ForeignKey(Transcript, on_delete=models.CASCADE, related_name="segments")
    sequence = models.PositiveIntegerField()
    speaker_label = models.CharField(max_length=50, blank=True, default="")
    # Never assume speaker 0 is the broker: the mapped role stays null while unknown.
    speaker_role = models.CharField(
        max_length=10, choices=SpeakerRole.choices, null=True, blank=True
    )
    start_seconds = models.DecimalField(max_digits=8, decimal_places=2)
    end_seconds = models.DecimalField(max_digits=8, decimal_places=2)
    text = models.TextField()
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["transcript", "sequence"],
                name="comms_transcript_segment_sequence_uniq",
            ),
        ]
        ordering = ["sequence"]

    def __str__(self):
        return f"Segment {self.sequence} of {self.transcript_id}"
