from django.core.management.base import BaseCommand, CommandError

from apps.comms.recovery import retry_jobs, select_failed_jobs, sweep_stale_jobs
from apps.freight.models import DatasetSnapshot


class Command(BaseCommand):
    help = (
        "Preview (default) or explicitly retry terminally failed ingestion jobs. "
        "Repeat with --execute to authorize redispatch and possible provider cost."
    )

    def add_arguments(self, parser):
        parser.add_argument("--snapshot", default="active", help="'active' or a snapshot UUID.")
        parser.add_argument("--source-type", choices=["email", "call"])
        parser.add_argument(
            "--error-code",
            action="append",
            default=[],
            help="Repeatable filter; required to select permanent error codes.",
        )
        parser.add_argument("--max-jobs", type=int)
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        if options["snapshot"] == "active":
            snapshot = DatasetSnapshot.objects.filter(is_active=True).first()
            if snapshot is None:
                raise CommandError("no active dataset snapshot exists")
        else:
            try:
                snapshot = DatasetSnapshot.objects.get(id=options["snapshot"])
            except (DatasetSnapshot.DoesNotExist, ValueError) as exc:
                raise CommandError("unknown snapshot") from exc

        selection = select_failed_jobs(
            snapshot=snapshot,
            source_type=options["source_type"],
            error_codes=options["error_code"],
            max_jobs=options["max_jobs"],
        )

        self.stdout.write(f"selected failed jobs: {len(selection.job_ids)}")
        for code, count in sorted(selection.by_error_code.items()):
            self.stdout.write(f"  {code}: {count}")
        if selection.skipped_retry_limit:
            self.stdout.write(
                f"skipped (whole-job retry limit reached): {selection.skipped_retry_limit}"
            )
        for job_id in selection.job_ids[:20]:
            self.stdout.write(f"  job {job_id}")

        if not options["execute"]:
            self.stdout.write("preview only — repeat with --execute to retry these jobs")
            return

        retried = retry_jobs(selection)
        swept = sweep_stale_jobs()
        self.stdout.write(f"requeued and dispatched: {retried}; stale swept: {swept}")
