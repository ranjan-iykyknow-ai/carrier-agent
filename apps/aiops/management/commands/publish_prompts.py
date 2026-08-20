"""Publish the bundled extraction prompts to Langfuse (spec 3I release flow).

Langfuse Prompt Management is the runtime source of truth; the bundled prompts
are the reviewed emergency fallback. This command aligns the two: it publishes
each bundled prompt as a new production-labelled version only when the current
production text differs, so reruns are idempotent.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.aiops import observability
from apps.aiops.prompts import fallback_prompt


class Command(BaseCommand):
    help = "Publish bundled extraction prompts to Langfuse with the production label."

    def handle(self, **options):
        lf = observability.client()
        if lf is None:
            raise CommandError("Langfuse is not configured; set the LANGFUSE_* variables.")
        for channel in ("email", "call"):
            info = fallback_prompt(channel)
            current_text = None
            try:
                existing = lf.get_prompt(info.name, label="production", cache_ttl_seconds=0)
                current_text = getattr(existing, "prompt", None)
            except Exception:
                pass  # no production version yet
            if current_text == info.text:
                self.stdout.write(f"{info.name}: up to date")
                continue
            lf.create_prompt(
                name=info.name,
                type="text",
                prompt=info.text,
                labels=["production"],
            )
            self.stdout.write(f"{info.name}: published a new production version")
