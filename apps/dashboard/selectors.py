"""Read-only queries powering the executive dashboard (PRD 8.2, spec 3I).

Any metric with no underlying data is None and renders as "unavailable" —
never zero, never invented.
"""

from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Sum

from apps.aiops.models import AIOperation, EvaluationRun
from apps.candidates.models import EligibilityAssessment
from apps.candidates.selectors import market_context, offered_per_mile
from apps.comms.models import CommunicationEvent, IngestionJob
from apps.freight.models import DatasetSnapshot, Load
from apps.inquiries.models import ExtractionRun, Inquiry

BAND_LABELS = {
    "below_minimum": ("Below market min", "chip-danger"),
    "within_band": ("In market band", "chip-ok"),
    "above_maximum": ("Above market max", "chip-warn"),
}


def dashboard_data() -> dict:
    snapshot = DatasetSnapshot.objects.filter(is_active=True).first()
    if snapshot is None:
        return {"snapshot": None}

    loads = Load.objects.filter(dataset_snapshot=snapshot)
    load_counts = {
        row["status"]: row["n"] for row in loads.values("status").annotate(n=Count("id"))
    }

    demo_date = snapshot.as_of_at.astimezone(ZoneInfo(snapshot.display_timezone)).date()
    open_loads = []
    for load in (
        loads.filter(status=Load.Status.OPEN)
        .select_related("equipment_type", "lane")
        .annotate(inquiry_count=Count("inquiries"))
        .order_by("pickup_date", "external_load_id")
    ):
        context = market_context(load)
        if load.pickup_date < demo_date:
            band_label, band_class = "Pickup passed", "chip-warn"
        elif context is None or context.band_position is None:
            band_label, band_class = "Insufficient history", "chip-muted"
        else:
            band_label, band_class = BAND_LABELS[context.band_position]
        open_loads.append(
            {
                "load": load,
                "per_mile": offered_per_mile(load),
                "band_label": band_label,
                "band_class": band_class,
            }
        )

    review_count = Inquiry.objects.filter(
        communication_event__dataset_snapshot=snapshot,
        review_status=Inquiry.ReviewStatus.NEEDS_REVIEW,
    ).count()
    compliance_warnings = EligibilityAssessment.objects.filter(
        current_for_candidates__isnull=False,
        candidate__load__dataset_snapshot=snapshot,
        final_status__in=[
            EligibilityAssessment.FinalStatus.BLOCKED,
            EligibilityAssessment.FinalStatus.NEEDS_COMPLIANCE_REVIEW,
        ],
    ).count()

    return {
        "snapshot": snapshot,
        "load_counts": load_counts,
        "open_loads": open_loads,
        "review_count": review_count,
        "compliance_warnings": compliance_warnings,
        "ai": ai_summary(snapshot),
    }


def ai_summary(snapshot) -> dict:
    jobs = IngestionJob.objects.filter(communication_event__dataset_snapshot=snapshot)
    terminal = jobs.filter(
        status__in=[
            IngestionJob.Status.COMPLETED,
            IngestionJob.Status.NEEDS_REVIEW,
            IngestionJob.Status.FAILED,
        ]
    ).count()
    succeeded = jobs.filter(
        status__in=[IngestionJob.Status.COMPLETED, IngestionJob.Status.NEEDS_REVIEW]
    ).count()

    operations = AIOperation.objects.exclude(usage_category=AIOperation.UsageCategory.EVALUATION)
    latencies = sorted(
        operations.filter(latency_ms__isnull=False).values_list("latency_ms", flat=True)
    )
    total_cost = operations.aggregate(total=Sum("estimated_cost"))["total"]
    inquiry_count = Inquiry.objects.filter(communication_event__dataset_snapshot=snapshot).count()

    prompt_versions = sorted(
        set(
            ExtractionRun.objects.filter(is_current=True).values_list(
                "prompt_name", "prompt_version"
            )
        )
    )
    latest_eval = (
        EvaluationRun.objects.filter(status=EvaluationRun.Status.COMPLETED)
        .order_by("-completed_at")
        .first()
    )

    return {
        "total_inputs": CommunicationEvent.objects.filter(dataset_snapshot=snapshot).count(),
        "success_rate": (succeeded / terminal * 100) if terminal else None,
        "review_count": jobs.filter(status=IngestionJob.Status.NEEDS_REVIEW).count(),
        "avg_latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
        "p95_latency_ms": latencies[int(0.95 * (len(latencies) - 1))] if latencies else None,
        "total_cost": total_cost,
        "cost_per_inquiry": (
            (total_cost / inquiry_count).quantize(Decimal("0.0001"))
            if total_cost is not None and inquiry_count
            else None
        ),
        "prompt_versions": [f"{name}@{version}" for name, version in prompt_versions],
        "latest_eval": latest_eval,
        "langfuse_url": settings.LANGFUSE_BASE_URL
        if getattr(settings, "LANGFUSE_BASE_URL", None)
        else None,
    }
