"""Shell context: navigation and the demo clock chip for authenticated pages."""

from zoneinfo import ZoneInfo

from django.urls import reverse
from django.utils import timezone as django_timezone

from apps.freight.models import DatasetSnapshot


def shell(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    snapshot = (
        DatasetSnapshot.objects.filter(is_active=True).only("as_of_at", "display_timezone").first()
    )
    demo_clock = None
    if snapshot:
        demo_clock = snapshot.as_of_at.astimezone(ZoneInfo(snapshot.display_timezone))
    return {
        "demo_clock": demo_clock,
        "now": django_timezone.now(),
        "nav_items": [
            ("Dashboard", reverse("dashboard")),
            ("Inbox", reverse("inbox")),
            ("Loads", reverse("loads")),
            ("Carriers", reverse("carriers")),
            ("Ingestion Lab", reverse("lab")),
            ("Assistant", reverse("assistant")),
        ],
    }
