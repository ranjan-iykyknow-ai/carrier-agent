import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="comms.process_ingestion_job")
def process_ingestion_job(job_id: str) -> None:
    """Worker pipeline entry point (spec 3C). Receives only the stable job UUID."""
    from apps.aiops import observability, runtime_evals
    from apps.comms.models import IngestionJob
    from apps.comms.pipeline import process_job

    try:
        process_job(job_id)
    finally:
        # Runtime proxy scores + best-effort flush; neither may fail the job.
        job = IngestionJob.objects.filter(pk=job_id).first()
        if job is not None:
            runtime_evals.score_ingestion_job(job)
        observability.flush_safely()
