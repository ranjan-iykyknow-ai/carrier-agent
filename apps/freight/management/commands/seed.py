from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.freight.seeding import SeedError, SeedRunner


class Command(BaseCommand):
    help = "Import the Goodlane dataset package into the active durable snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dataset-path",
            default=str(Path(settings.BASE_DIR) / "goodlane-dataset"),
            help="Dataset package directory (defaults to the committed package).",
        )
        parser.add_argument(
            "--validate-only",
            action="store_true",
            help="Discovery, schema checks, WAV inspection, and checksums only; no writes.",
        )
        parser.add_argument(
            "--no-enqueue",
            action="store_true",
            help="Import durable records and queued jobs but suppress Celery dispatch.",
        )

    def handle(self, *args, **options):
        runner = SeedRunner(
            Path(options["dataset_path"]),
            validate_only=options["validate_only"],
            no_enqueue=options["no_enqueue"],
        )
        try:
            summary = runner.run()
        except SeedError as exc:
            for line in runner.summary.lines():
                self.stdout.write(line)
            raise CommandError(str(exc)) from exc
        for line in summary.lines():
            self.stdout.write(line)
