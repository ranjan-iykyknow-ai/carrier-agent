"""Constraint behavior for the Step 2B models (communication, content, transcripts, jobs)."""

import pytest
from django.db import IntegrityError, transaction

from tests.factories import (
    make_call_recording,
    make_communication_event,
    make_email_content,
    make_ingestion_job,
    make_snapshot,
    make_transcript,
)

pytestmark = pytest.mark.django_db


def _rejects(fn):
    with pytest.raises(IntegrityError), transaction.atomic():
        fn()


class TestCommunicationEvent:
    def test_content_fingerprint_globally_unique_across_origins(self):
        event = make_communication_event(origin="dataset")
        _rejects(
            lambda: make_communication_event(
                origin="manual", content_fingerprint=event.content_fingerprint
            )
        )

    def test_dataset_events_unique_by_snapshot_and_external_id(self):
        event = make_communication_event(origin="dataset", external_source_id="CE0058")
        _rejects(
            lambda: make_communication_event(
                snapshot=event.dataset_snapshot,
                origin="dataset",
                external_source_id="CE0058",
            )
        )

    def test_manual_events_may_share_null_external_id(self):
        snapshot = make_snapshot()
        make_communication_event(snapshot=snapshot, origin="manual", external_source_id=None)
        make_communication_event(snapshot=snapshot, origin="manual", external_source_id=None)

    def test_stable_evidence_id_unique(self):
        event = make_communication_event()
        _rejects(lambda: make_communication_event(stable_evidence_id=event.stable_evidence_id))

    def test_occurred_at_may_be_null_for_dataset_calls(self):
        event = make_communication_event(channel="call", occurred_at=None)
        assert event.occurred_at is None
        assert event.received_at is not None


class TestOwnedContent:
    def test_one_email_content_per_event(self):
        content = make_email_content()
        _rejects(lambda: make_email_content(event=content.communication_event))

    def test_one_call_recording_per_event(self):
        recording = make_call_recording()
        _rejects(lambda: make_call_recording(event=recording.communication_event))

    def test_storage_key_unique(self):
        recording = make_call_recording()
        _rejects(lambda: make_call_recording(storage_key=recording.storage_key))


class TestIngestionJob:
    def test_one_job_per_communication_event(self):
        job = make_ingestion_job()
        _rejects(lambda: make_ingestion_job(event=job.communication_event))

    def test_job_defaults(self):
        job = make_ingestion_job()
        assert job.status == "queued"
        assert job.retry_count == 0


class TestTranscript:
    def test_only_one_current_transcript_per_recording(self):
        transcript = make_transcript(is_current=True)
        _rejects(lambda: make_transcript(recording=transcript.call_recording, is_current=True))

    def test_superseded_versions_are_append_only(self):
        transcript = make_transcript(is_current=True)
        make_transcript(recording=transcript.call_recording, is_current=False)
        make_transcript(recording=transcript.call_recording, is_current=False)

    def test_segments_ordered_and_unique_by_sequence(self):
        from apps.comms.models import TranscriptSegment

        transcript = make_transcript()
        TranscriptSegment.objects.create(
            transcript=transcript,
            sequence=1,
            speaker_label="0",
            start_seconds="0.0",
            end_seconds="4.2",
            text="Goodlane dispatch, this is Sam.",
        )
        _rejects(
            lambda: TranscriptSegment.objects.create(
                transcript=transcript,
                sequence=1,
                speaker_label="1",
                start_seconds="4.2",
                end_seconds="9.0",
                text="Hey Sam.",
            )
        )
