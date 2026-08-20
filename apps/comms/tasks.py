import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="comms.process_ingestion_job")
def process_ingestion_job(job_id: str) -> None:
    """Worker pipeline entry point (Step 3C).

    Placeholder until the pipeline phase lands: a dispatched job is failed
    visibly (retryable) rather than left silently stuck or faked as processed.
    """
    from apps.comms.models import IngestionJob

    updated = IngestionJob.objects.filter(id=job_id, status=IngestionJob.Status.QUEUED).update(
        status=IngestionJob.Status.FAILED,
        last_error_code="pipeline_not_implemented",
        last_error_summary="The processing pipeline is not implemented yet.",
    )
    if updated:
        logger.info("job %s parked as failed: pipeline_not_implemented", job_id)
