"""Stale-job sweep, dispatch coordination, and explicit bulk retry (spec 3A.2)."""

from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.comms.models import IngestionJob
from apps.comms.recovery import sweep_stale_jobs
from tests.factories import make_communication_event, make_ingestion_job, make_snapshot

pytestmark = pytest.mark.django_db


def old(minutes):
    return timezone.now() - timedelta(minutes=minutes)


class TestSweep:
    def test_abandoned_processing_job_fails(self, settings):
        settings.STALE_PROCESSING_SWEEP_SECONDS = 60
        job = make_ingestion_job()
        IngestionJob.objects.filter(id=job.id).update(
            status="processing", started_at=old(minutes=10)
        )

        assert sweep_stale_jobs() == 1
        job.refresh_from_db()
        assert job.status == "failed"
        assert job.last_error_code == "stale_processing"

    def test_recent_processing_job_untouched(self, settings):
        settings.STALE_PROCESSING_SWEEP_SECONDS = 3600
        job = make_ingestion_job()
        IngestionJob.objects.filter(id=job.id).update(
            status="processing", started_at=timezone.now()
        )
        assert sweep_stale_jobs() == 0

    def test_stale_manual_queued_job_fails_but_dataset_queued_is_excluded(self, settings):
        settings.STALE_QUEUED_SWEEP_SECONDS = 60
        snapshot = make_snapshot()
        manual = make_ingestion_job(
            event=make_communication_event(snapshot=snapshot, origin="manual")
        )
        dataset = make_ingestion_job(
            event=make_communication_event(snapshot=snapshot, origin="dataset")
        )
        IngestionJob.objects.filter(id__in=[manual.id, dataset.id]).update(
            submitted_at=old(minutes=10)
        )

        swept = sweep_stale_jobs()
        manual.refresh_from_db()
        dataset.refresh_from_db()
        assert swept == 1
        assert manual.status == "failed"
        assert manual.last_error_code == "queue_unavailable"
        assert dataset.status == "queued"


class TestRetryFailedJobs:
    def _failed_job(self, snapshot, error_code="provider_unavailable", retry_count=0):
        job = make_ingestion_job(event=make_communication_event(snapshot=snapshot))
        IngestionJob.objects.filter(id=job.id).update(
            status="failed", last_error_code=error_code, retry_count=retry_count
        )
        job.refresh_from_db()
        return job

    def test_preview_is_read_only(self):
        snapshot = make_snapshot(is_active=True)
        job = self._failed_job(snapshot)

        call_command("retry_failed_jobs", "--snapshot", "active")
        job.refresh_from_db()
        assert job.status == "failed"

    def test_execute_requeues_increments_and_dispatches(self, django_capture_on_commit_callbacks):
        snapshot = make_snapshot(is_active=True)
        job = self._failed_job(snapshot)

        with mock.patch("apps.comms.recovery.process_ingestion_job") as task:
            with django_capture_on_commit_callbacks(execute=True):
                call_command("retry_failed_jobs", "--snapshot", "active", "--execute")
            task.delay.assert_called_once_with(str(job.id))

        job.refresh_from_db()
        assert job.status == "queued"
        assert job.retry_count == 1

    def test_respects_whole_job_retry_limit(self, settings):
        settings.MAX_JOB_RETRIES = 2
        snapshot = make_snapshot(is_active=True)
        job = self._failed_job(snapshot, retry_count=2)

        with mock.patch("apps.comms.recovery.process_ingestion_job") as task:
            call_command("retry_failed_jobs", "--snapshot", "active", "--execute")
            task.delay.assert_not_called()
        job.refresh_from_db()
        assert job.status == "failed"

    def test_error_code_filter_selects_only_matching_jobs(self):
        snapshot = make_snapshot(is_active=True)
        transient = self._failed_job(snapshot, error_code="provider_unavailable")
        permanent = self._failed_job(snapshot, error_code="unsupported_media")

        with mock.patch("apps.comms.recovery.process_ingestion_job"):
            call_command(
                "retry_failed_jobs",
                "--snapshot",
                "active",
                "--error-code",
                "provider_unavailable",
                "--execute",
            )
        transient.refresh_from_db()
        permanent.refresh_from_db()
        assert transient.status == "queued"
        assert permanent.status == "failed"
