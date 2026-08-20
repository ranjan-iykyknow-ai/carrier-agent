"""Behavior of the shared ingestion contract (spec 3A.1).

Every entry point produces the same durable core: CommunicationEvent ->
channel content -> IngestionJob, atomically, with one fingerprint formula,
duplicate safety, demo-clock derivation, and post-commit dispatch.
"""

from datetime import UTC, datetime
from unittest import mock

import pytest
from django.core.files.storage import default_storage

from apps.comms.fingerprints import audio_fingerprint, email_fingerprint
from apps.comms.ingestion import (
    DatasetNotReadyError,
    IngestionSubmissionService,
    SubmissionInvariantError,
    SubmitCallCommand,
    SubmitEmailCommand,
)
from apps.comms.models import CallRecording, CommunicationEvent, IngestionJob
from tests.factories import make_import_batch, make_inquiry, make_snapshot

pytestmark = pytest.mark.django_db

WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt fake-audio-payload"


def email_command(**overrides):
    defaults = {
        "origin": "manual",
        "sender_email": "cmendez@blueridgetransport.com",
        "sender_name": "Carlos Mendez",
        "subject": "RE: Load 29372450",
        "body_text": "We can do the Philly to New York box truck Saturday for $400 all-in.",
        "submitted_by": "broker@goodlanelogistics.com",
    }
    defaults.update(overrides)
    return SubmitEmailCommand(**defaults)


def call_command(**overrides):
    defaults = {
        "origin": "manual",
        "content": WAV_BYTES,
        "original_filename": "call_012_rate_negotiation.wav",
        "mime_type": "audio/wav",
        "byte_size": len(WAV_BYTES),
        "audio_format": "wav",
        "submitted_by": "broker@goodlanelogistics.com",
    }
    defaults.update(overrides)
    return SubmitCallCommand(**defaults)


@pytest.fixture
def service():
    return IngestionSubmissionService()


@pytest.fixture
def active_snapshot():
    return make_snapshot(is_active=True, as_of_at=datetime(2026, 5, 25, 16, 0, tzinfo=UTC))


class TestEmailSubmission:
    def test_creates_event_content_and_job_together(self, service, active_snapshot):
        result = service.submit_email(email_command())

        assert result.outcome == "created"
        event = CommunicationEvent.objects.get(id=result.communication_event_id)
        assert event.channel == "email"
        assert event.origin == "manual"
        assert event.content_fingerprint == email_fingerprint(
            "cmendez@blueridgetransport.com",
            "RE: Load 29372450",
            "We can do the Philly to New York box truck Saturday for $400 all-in.",
        )
        assert event.email_content.body_text.startswith("We can do")
        job = event.ingestion_job
        assert job.status == "queued"
        assert result.job_id == job.id
        assert result.stable_evidence_id == f"email:manual:{event.id}"

    def test_dataset_email_keeps_source_identity_and_time(self, service, active_snapshot):
        batch = make_import_batch(snapshot=active_snapshot, source_type="emails")
        occurred = datetime(2026, 5, 18, 14, 0, tzinfo=UTC)
        result = service.submit_email(
            email_command(
                origin="dataset",
                dataset_snapshot_id=active_snapshot.id,
                import_batch_id=batch.id,
                external_source_id="CE0058",
                occurred_at=occurred,
                source_timestamp_raw="2026-05-18T14:00:00Z",
                source_metadata={"intent": "terse", "mc_number": "567234"},
                dispatch_mode="deferred",
                submitted_by="seed",
            )
        )

        event = CommunicationEvent.objects.get(id=result.communication_event_id)
        assert result.stable_evidence_id == "email:CE0058"
        assert event.occurred_at == occurred
        assert event.email_content.source_metadata["intent"] == "terse"

    def test_rejected_without_active_snapshot(self, service):
        with pytest.raises(DatasetNotReadyError):
            service.submit_email(email_command())
        assert CommunicationEvent.objects.count() == 0
        assert IngestionJob.objects.count() == 0

    def test_manual_occurred_at_uses_demo_date_with_real_time_of_day(
        self, service, active_snapshot
    ):
        real_now = datetime(2026, 8, 20, 13, 30, tzinfo=UTC)  # 09:30 in New York
        with mock.patch("apps.comms.ingestion.timezone.now", return_value=real_now):
            result = service.submit_email(email_command())

        event = CommunicationEvent.objects.get(id=result.communication_event_id)
        assert event.received_at == real_now
        local = event.occurred_at.astimezone(event_tz(active_snapshot))
        assert (local.year, local.month, local.day) == (2026, 5, 25)
        assert (local.hour, local.minute) == (9, 30)

    def test_manual_cannot_carry_trusted_dataset_context(self, service, active_snapshot):
        batch = make_import_batch(snapshot=active_snapshot)
        with pytest.raises(SubmissionInvariantError):
            service.submit_email(email_command(import_batch_id=batch.id))
        with pytest.raises(SubmissionInvariantError):
            service.submit_email(email_command(source_metadata={"intent": "terse"}))

    def test_hints_are_stored_on_the_job(self, service, active_snapshot):
        result = service.submit_email(
            email_command(
                suspected_load_reference="29372450",
                suspected_carrier_identity="Blue Ridge",
            )
        )
        job = IngestionJob.objects.get(id=result.job_id)
        assert job.suspected_load_reference_raw == "29372450"
        assert job.suspected_carrier_identity_raw == "Blue Ridge"


class TestDuplicates:
    def test_duplicate_returns_existing_without_new_records(self, service, active_snapshot):
        first = service.submit_email(email_command())
        second = service.submit_email(
            email_command(
                sender_email="  CMENDEZ@blueridgetransport.com ",
                body_text=(
                    "We can do the Philly to New York box truck Saturday for $400 all-in.\r\n"
                ),
                suspected_load_reference="99999999",
            )
        )

        assert second.outcome == "existing"
        assert second.job_id == first.job_id
        assert CommunicationEvent.objects.count() == 1
        job = IngestionJob.objects.get(id=first.job_id)
        assert job.suspected_load_reference_raw is None  # hints never silently applied

    def test_failed_duplicate_offers_retry(self, service, active_snapshot):
        first = service.submit_email(email_command())
        IngestionJob.objects.filter(id=first.job_id).update(status="failed")

        second = service.submit_email(email_command())
        assert second.outcome == "existing"
        assert second.retry_available is True

    def test_needs_review_duplicate_returns_the_review_inquiry(self, service, active_snapshot):
        first = service.submit_email(email_command())
        job = IngestionJob.objects.get(id=first.job_id)
        event = job.communication_event
        make_inquiry(event=event, sequence_number=1, review_status="approved")
        reviewable = make_inquiry(event=event, sequence_number=2, review_status="needs_review")
        IngestionJob.objects.filter(id=job.id).update(status="needs_review")

        second = service.submit_email(email_command())
        assert second.inquiry_id == reviewable.id

    def test_completed_duplicate_returns_lowest_sequence_inquiry(self, service, active_snapshot):
        first = service.submit_email(email_command())
        job = IngestionJob.objects.get(id=first.job_id)
        lowest = make_inquiry(
            event=job.communication_event, sequence_number=1, review_status="approved"
        )
        make_inquiry(event=job.communication_event, sequence_number=2, review_status="needs_review")
        IngestionJob.objects.filter(id=job.id).update(status="completed")

        second = service.submit_email(email_command())
        assert second.inquiry_id == lowest.id


class TestDispatch:
    def test_immediate_dispatch_enqueues_after_commit(
        self, service, active_snapshot, django_capture_on_commit_callbacks
    ):
        with mock.patch("apps.comms.ingestion.process_ingestion_job") as task:
            with django_capture_on_commit_callbacks(execute=True):
                result = service.submit_email(email_command())
            task.delay.assert_called_once_with(str(result.job_id))

    def test_deferred_dispatch_leaves_job_for_bulk_dispatcher(
        self, service, active_snapshot, django_capture_on_commit_callbacks
    ):
        batch = make_import_batch(snapshot=active_snapshot, source_type="emails")
        with mock.patch("apps.comms.ingestion.process_ingestion_job") as task:
            with django_capture_on_commit_callbacks(execute=True):
                service.submit_email(
                    email_command(
                        origin="dataset",
                        dataset_snapshot_id=active_snapshot.id,
                        import_batch_id=batch.id,
                        external_source_id="CE0001",
                        occurred_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
                        dispatch_mode="deferred",
                        submitted_by="seed",
                    )
                )
            task.delay.assert_not_called()

    def test_broker_failure_marks_job_queue_unavailable_without_raising(
        self, service, active_snapshot, django_capture_on_commit_callbacks
    ):
        with mock.patch("apps.comms.ingestion.process_ingestion_job") as task:
            task.delay.side_effect = ConnectionError("broker down")
            with django_capture_on_commit_callbacks(execute=True):
                result = service.submit_email(email_command())

        job = IngestionJob.objects.get(id=result.job_id)
        assert job.status == "failed"
        assert job.last_error_code == "queue_unavailable"


class TestCallSubmission:
    def test_creates_event_recording_and_job_with_stored_audio(self, service, active_snapshot):
        result = service.submit_call(call_command())

        event = CommunicationEvent.objects.get(id=result.communication_event_id)
        assert event.channel == "call"
        assert event.content_fingerprint == audio_fingerprint(WAV_BYTES)
        assert event.occurred_at is not None  # manual calls get the demo clock
        recording = event.call_recording
        assert recording.original_filename == "call_012_rate_negotiation.wav"
        assert recording.original_filename not in recording.storage_key
        assert default_storage.exists(recording.storage_key)
        assert result.stable_evidence_id == f"call:manual:{event.id}"

    def test_dataset_call_has_no_business_time(self, service, active_snapshot):
        batch = make_import_batch(snapshot=active_snapshot, source_type="calls")
        result = service.submit_call(
            call_command(
                origin="dataset",
                dataset_snapshot_id=active_snapshot.id,
                import_batch_id=batch.id,
                external_source_id="call_012_rate_negotiation.wav",
                dispatch_mode="deferred",
                submitted_by="seed",
            )
        )
        event = CommunicationEvent.objects.get(id=result.communication_event_id)
        assert event.occurred_at is None
        assert event.source_timestamp_raw is None
        assert result.stable_evidence_id == "call:call_012_rate_negotiation.wav"

    def test_duplicate_audio_creates_no_new_storage_object(self, service, active_snapshot):
        first = service.submit_call(call_command())
        before = CallRecording.objects.count()

        second = service.submit_call(call_command(original_filename="renamed-copy.wav"))
        assert second.outcome == "existing"
        assert second.job_id == first.job_id
        assert CallRecording.objects.count() == before


def event_tz(snapshot):
    from zoneinfo import ZoneInfo

    return ZoneInfo(snapshot.display_timezone)
