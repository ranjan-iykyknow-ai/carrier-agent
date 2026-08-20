import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="comms.process_ingestion_job")
def process_ingestion_job(job_id: str) -> None:
    """Worker pipeline entry point (spec 3C). Receives only the stable job UUID."""
    from apps.aiops import observability
    from apps.comms.pipeline import process_job

    try:
        process_job(job_id)
    finally:
        # Best-effort flush so trace links exist by the time the job is terminal.
        observability.flush_safely()
