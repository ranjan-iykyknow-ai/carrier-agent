"""Seed command behavior (spec 3A.2) using miniature packages — no network, no providers."""

import json
from unittest import mock

import pytest
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.comms.models import CallRecording, CommunicationEvent, IngestionJob
from apps.freight.models import (
    Carrier,
    DatasetSnapshot,
    ImportBatch,
    Lane,
    Load,
    MarketRateHistory,
)
from tests.dataset_builder import DEFAULT_CARRIERS, DEFAULT_EMAILS, build_package

pytestmark = pytest.mark.django_db


@pytest.fixture
def package(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    return build_package(tmp_path / "mini-dataset")


def seed(*args):
    call_command("seed", *args)


class TestValidateOnly:
    def test_makes_no_database_or_storage_writes(self, package, tmp_path):
        seed("--dataset-path", str(package), "--validate-only")

        assert DatasetSnapshot.objects.count() == 0
        assert ImportBatch.objects.count() == 0
        assert CommunicationEvent.objects.count() == 0
        assert not (tmp_path / "media").exists()


class TestFullSeed:
    def test_imports_everything_and_activates_the_snapshot(self, package):
        seed("--dataset-path", str(package), "--no-enqueue")

        snapshot = DatasetSnapshot.objects.get()
        assert snapshot.is_active
        assert snapshot.imported_at is not None
        assert ImportBatch.objects.filter(status="completed").count() == 5

        assert Load.objects.count() == 3
        assert Carrier.objects.count() == 3
        assert MarketRateHistory.objects.count() == 2
        assert CommunicationEvent.objects.filter(channel="email").count() == 3
        assert CommunicationEvent.objects.filter(channel="call").count() == 2
        assert IngestionJob.objects.filter(status="queued").count() == 5

        for recording in CallRecording.objects.all():
            assert default_storage.exists(recording.storage_key)
            assert recording.original_filename not in recording.storage_key

    def test_intra_state_lane_is_imported_normally(self, package):
        seed("--dataset-path", str(package), "--no-enqueue")
        assert Lane.objects.filter(origin_state="PA", destination_state="PA").exists()

    def test_dataset_annotations_stay_untrusted_metadata(self, package):
        seed("--dataset-path", str(package), "--no-enqueue")
        event = CommunicationEvent.objects.get(external_source_id="CE0074")
        assert event.email_content.source_metadata["equipment_mentioned"] == "Refrigerated"
        assert event.inquiries.count() == 0

    def test_carrier_source_identity_uses_namespaced_fallbacks(self, package):
        seed("--dataset-path", str(package), "--no-enqueue")
        assert Carrier.objects.filter(source_identifier="mc:712843").exists()
        assert Carrier.objects.filter(source_identifier="email:pat@keystoneoddjobs.com").exists()

    def test_unknown_company_name_is_accepted_business_data(self, package):
        seed("--dataset-path", str(package), "--no-enqueue")
        nameless = Carrier.objects.get(source_identifier="email:pat@keystoneoddjobs.com")
        assert nameless.company_name is None

    def test_rerun_creates_no_duplicates(self, package):
        seed("--dataset-path", str(package), "--no-enqueue")
        counts = (
            DatasetSnapshot.objects.count(),
            Load.objects.count(),
            Carrier.objects.count(),
            CommunicationEvent.objects.count(),
            IngestionJob.objects.count(),
            CallRecording.objects.count(),
        )

        seed("--dataset-path", str(package), "--no-enqueue")
        assert counts == (
            DatasetSnapshot.objects.count(),
            Load.objects.count(),
            Carrier.objects.count(),
            CommunicationEvent.objects.count(),
            IngestionJob.objects.count(),
            CallRecording.objects.count(),
        )


class TestDispatchBoundary:
    def test_no_enqueue_leaves_jobs_for_a_later_ordinary_run(self, package):
        seed("--dataset-path", str(package), "--no-enqueue")
        with mock.patch("apps.comms.recovery.process_ingestion_job") as task:
            seed("--dataset-path", str(package))
        assert task.delay.call_count == 5
        # Dispatch hands over UUIDs; the jobs themselves remain queued rows.
        assert IngestionJob.objects.filter(status="queued").count() == 5


class TestPreflightRejections:
    def test_timezone_naive_email_timestamp(self, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path / "media")
        emails = json.loads(json.dumps(DEFAULT_EMAILS))
        emails[0]["timestamp"] = "2026-05-18T14:00:00"
        package = build_package(tmp_path / "pkg", emails=emails)

        with pytest.raises(CommandError, match="timestamp"):
            seed("--dataset-path", str(package), "--no-enqueue")
        assert DatasetSnapshot.objects.count() == 0

    def test_duplicate_source_ids(self, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path / "media")
        emails = json.loads(json.dumps(DEFAULT_EMAILS))
        emails[1]["email_id"] = emails[0]["email_id"]
        package = build_package(tmp_path / "pkg", emails=emails)

        with pytest.raises(CommandError, match="duplicate"):
            seed("--dataset-path", str(package), "--no-enqueue")

    def test_duplicate_fingerprints_fail_before_writes(self, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path / "media")
        emails = json.loads(json.dumps(DEFAULT_EMAILS))
        emails[1] = dict(
            emails[0], email_id="CE0999", timestamp="2026-05-19T10:00:00Z"
        )  # same sender/subject/body => same fingerprint
        package = build_package(tmp_path / "pkg", emails=emails)

        with pytest.raises(CommandError, match="fingerprint"):
            seed("--dataset-path", str(package), "--no-enqueue")
        assert CommunicationEvent.objects.count() == 0

    def test_missing_required_source(self, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path / "media")
        package = build_package(tmp_path / "pkg")
        (package / "rate_history.csv").unlink()

        with pytest.raises(CommandError, match="rate_history"):
            seed("--dataset-path", str(package), "--no-enqueue")

    def test_invalid_wav_container(self, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path / "media")
        package = build_package(tmp_path / "pkg")
        (package / "call_recordings" / "call_012_rate_negotiation.wav").write_text("not audio")

        with pytest.raises(CommandError, match="WAV|wav"):
            seed("--dataset-path", str(package), "--no-enqueue")

    def test_changed_content_under_completed_version_is_rejected(self, package):
        seed("--dataset-path", str(package), "--no-enqueue")
        emails = json.loads((package / "carrier_emails.json").read_text())
        emails[0]["body"] = "Changed content, same version."
        (package / "carrier_emails.json").write_text(json.dumps(emails))

        with pytest.raises(CommandError, match="version|checksum"):
            seed("--dataset-path", str(package), "--no-enqueue")


class TestStructuralRecordFailures:
    def test_invalid_record_blocks_activation_as_partially_failed(self, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path / "media")
        carriers = json.loads(json.dumps(DEFAULT_CARRIERS))
        carriers[1]["reliability_score"] = "not-a-number"
        package = build_package(tmp_path / "pkg", carriers=carriers)

        with pytest.raises(CommandError):
            seed("--dataset-path", str(package), "--no-enqueue")

        snapshot = DatasetSnapshot.objects.get()
        assert not snapshot.is_active
        batch = ImportBatch.objects.get(source_type="carriers")
        assert batch.status == "partially_failed"
        assert batch.records_failed == 1


class TestStaleSweep:
    def test_seed_summary_sweeps_abandoned_processing_jobs(self, package, settings):
        settings.STALE_PROCESSING_SWEEP_SECONDS = 0
        seed("--dataset-path", str(package), "--no-enqueue")
        job = IngestionJob.objects.first()
        IngestionJob.objects.filter(id=job.id).update(
            status="processing", started_at=job.submitted_at
        )

        seed("--dataset-path", str(package), "--no-enqueue")
        job.refresh_from_db()
        assert job.status == "failed"
        assert job.last_error_code == "stale_processing"
