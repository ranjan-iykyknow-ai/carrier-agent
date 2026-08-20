from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404
from django.shortcuts import render

from apps.candidates.models import CarrierLoadCandidate
from apps.candidates.selectors import best_rates, market_context, offered_per_mile
from apps.freight.models import DatasetSnapshot, Load
from apps.inquiries.models import Inquiry

STATUS_CHIPS = {
    "eligible": ("Eligible", "chip-ok"),
    "blocked": ("Blocked", "chip-danger"),
    "needs_compliance_review": ("Needs compliance review", "chip-warn"),
    "needs_clarification": ("Needs clarification", "chip-warn"),
    "needs_onboarding": ("Needs onboarding", "chip-info"),
}


def _active_snapshot():
    return DatasetSnapshot.objects.filter(is_active=True).first()


@login_required
def loads_list(request):
    snapshot = _active_snapshot()
    loads = (
        Load.objects.filter(dataset_snapshot=snapshot)
        .select_related("equipment_type")
        .annotate(inquiry_count=Count("inquiries"))
        .order_by("status", "pickup_date", "external_load_id")
        if snapshot
        else Load.objects.none()
    )
    return render(request, "freight/loads.html", {"loads": loads})


def _band_geometry(context, per_mile):
    """Percent positions for the market-band bar."""
    values = [
        context.history.minimum_rate_per_mile,
        context.history.maximum_rate_per_mile,
    ]
    if per_mile is not None:
        values.append(per_mile)
    low = min(values) * Decimal("0.85")
    high = max(values) * Decimal("1.1")
    span = high - low or Decimal(1)

    def pct(value):
        return float((value - low) / span * 100)

    return {
        "min_pct": pct(context.history.minimum_rate_per_mile),
        "avg_pct": pct(context.history.average_rate_per_mile),
        "max_pct": pct(context.history.maximum_rate_per_mile),
        "offered_pct": pct(per_mile) if per_mile is not None else None,
        "range_width": pct(context.history.maximum_rate_per_mile)
        - pct(context.history.minimum_rate_per_mile),
    }


BAND_SENTENCES = {
    "below_minimum": "below the market minimum",
    "within_band": "within the market band",
    "above_maximum": "above the market max",
}


@login_required
def load_workspace(request, external_load_id):
    snapshot = _active_snapshot()
    load = (
        Load.objects.filter(dataset_snapshot=snapshot, external_load_id=external_load_id)
        .select_related("equipment_type", "lane")
        .first()
    )
    if load is None:
        raise Http404

    context = market_context(load)
    per_mile = offered_per_mile(load)
    rates = best_rates(load)

    candidates = []
    for candidate in (
        CarrierLoadCandidate.objects.filter(load=load)
        .select_related(
            "carrier",
            "current_quote",
            "current_eligibility_assessment__compliance_assessment",
        )
        .prefetch_related("current_eligibility_assessment__reasons")
    ):
        assessment = candidate.current_eligibility_assessment
        label, chip = ("Unknown", "chip-muted")
        reasons = []
        if assessment:
            label, chip = STATUS_CHIPS.get(
                assessment.final_status, (assessment.final_status, "chip-muted")
            )
            reasons = [
                reason
                for reason in assessment.reasons.all()
                if reason.severity in ("blocker", "review")
            ]
        candidates.append(
            {
                "candidate": candidate,
                "carrier": candidate.carrier,
                "quote": candidate.current_quote,
                "status_label": label,
                "status_chip": chip,
                "reasons": reasons,
            }
        )
    candidates.sort(key=lambda row: (row["quote"] is None, str(row["carrier"].id)))

    unmatched = (
        Inquiry.objects.filter(load=load, carrier__isnull=True)
        .select_related("communication_event__email_content")
        .order_by("-created_at")
    )
    timeline = (
        Inquiry.objects.filter(load=load)
        .select_related("communication_event__email_content", "carrier")
        .prefetch_related("quotes")
        .order_by("-communication_event__occurred_at", "-communication_event__received_at")
    )

    return render(
        request,
        "freight/load_workspace.html",
        {
            "load": load,
            "per_mile": per_mile,
            "market": context,
            "band": _band_geometry(context, per_mile) if context else None,
            "band_sentence": BAND_SENTENCES.get(context.band_position) if context else None,
            "rates": rates,
            "candidates": candidates,
            "unmatched": unmatched,
            "timeline": timeline,
        },
    )
