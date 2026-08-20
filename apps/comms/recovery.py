"""Stale-job sweep, bulk dispatch coordination, and explicit failed-job retry.

The sweep is the P0 substitute for a scheduled reaper: it runs whenever any job
is polled or a seed health summary is built (production delta: move unchanged
into a Beat/cron task). Dispatch and retry hand workers only stable job UUIDs.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.comms.models import IngestionJob
from apps.comms.tasks import process_ingestion_job

logger = logging.getLogger(__name__)

# Failure codes that describe permanent outcomes; the default bulk-retry
# selection excludes them so reruns cannot silently spend money on inputs that
# can never succeed. An explicit --error-code filter overrides.
PERMANENT_ERROR_CODES = frozenset({"unsupported_media", "invalid_source"})


def sweep_stale_jobs() -> int:
    """One bounded global sweep across all stale jobs. Returns jobs swept."""
    now = timezone.now()
    swept = 0

    processing_cutoff = now - timedelta(seconds=settings.STALE_PROCESSING_SWEEP_SECONDS)
    swept += IngestionJob.objects.filter(
        status=IngestionJob.Status.PROCESSING, started_at__lt=processing_cutoff
    ).update(
        status=IngestionJob.Status.FAILED,
        last_error_code="stale_processing",
        last_error_summary="Processing exceeded the stale threshold; the worker is presumed dead.",
        completed_at=now,
    )

    # Manual-origin queued jobs whose dispatch evidently never arrived; dataset
    # jobs are deliberately excluded because deferred seeding leaves them queued.
    queued_cutoff = now - timedelta(seconds=settings.STALE_QUEUED_SWEEP_SECONDS)
    swept += IngestionJob.objects.filter(
        status=IngestionJob.Status.QUEUED,
        origin="manual",
        submitted_at__lt=queued_cutoff,
    ).update(
        status=IngestionJob.Status.FAILED,
        last_error_code="queue_unavailable",
        last_error_summary="The job never reached a worker; the queue was likely unavailable.",
        completed_at=now,
    )

    if swept:
        logger.warning("stale-job sweep moved %d job(s) to failed", swept)
    return swept


def dispatch_queued_jobs(snapshot) -> int:
    """Hand every queued job UUID of the snapshot to the worker queue.

    Raises on broker failure — durable jobs stay queued and the caller reports
    a safe dispatch error; a later ordinary run dispatches them.
    """
    job_ids = list(
        IngestionJob.objects.filter(
            status=IngestionJob.Status.QUEUED,
            communication_event__dataset_snapshot=snapshot,
        ).values_list("id", flat=True)
    )
    for job_id in job_ids:
        process_ingestion_job.delay(str(job_id))
    return len(job_ids)


@dataclass
class RetrySelection:
    job_ids: list = field(default_factory=list)
    by_error_code: dict = field(default_factory=dict)
    skipped_retry_limit: int = 0


def select_failed_jobs(
    *, snapshot=None, source_type=None, error_codes=(), max_jobs=None
) -> RetrySelection:
    jobs = IngestionJob.objects.filter(status=IngestionJob.Status.FAILED)
    if snapshot is not None:
        jobs = jobs.filter(communication_event__dataset_snapshot=snapshot)
    if source_type:
        jobs = jobs.filter(source_type=source_type)
    if error_codes:
        jobs = jobs.filter(last_error_code__in=list(error_codes))
    else:
        jobs = jobs.exclude(last_error_code__in=PERMANENT_ERROR_CODES)

    selection = RetrySelection()
    limit = settings.MAX_JOB_RETRIES
    for job in jobs.order_by("submitted_at"):
        if job.retry_count >= limit:
            selection.skipped_retry_limit += 1
            continue
        if max_jobs is not None and len(selection.job_ids) >= max_jobs:
            break
        selection.job_ids.append(job.id)
        code = job.last_error_code or "unknown"
        selection.by_error_code[code] = selection.by_error_code.get(code, 0) + 1
    return selection


def retry_jobs(selection: RetrySelection) -> int:
    """Atomically requeue each selected job and dispatch after commit.

    The conditional transition means a racing manual retry safely loses; the
    same logical attempt is never double-dispatched.
    """
    retried = 0
    for job_id in selection.job_ids:
        with transaction.atomic():
            updated = IngestionJob.objects.filter(
                id=job_id,
                status=IngestionJob.Status.FAILED,
                retry_count__lt=settings.MAX_JOB_RETRIES,
            ).update(
                status=IngestionJob.Status.QUEUED,
                retry_count=F("retry_count") + 1,
                completed_at=None,
            )
            if updated:
                retried += 1
                transaction.on_commit(
                    lambda captured=job_id: process_ingestion_job.delay(str(captured))
                )
    return retried
