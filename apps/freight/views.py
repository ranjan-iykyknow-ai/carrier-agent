from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from apps.candidates.models import CarrierLoadCandidate
from apps.candidates.policy import assess_authority, assess_safety
from apps.candidates.selectors import (
    best_rates,
    market_context,
    offered_per_mile,
    quote_as_per_mile,
    strongest_candidates,
)
from apps.freight.models import Carrier, DatasetSnapshot, Load
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


ONBOARDING_CHIPS = {
    True: ("Onboarded", "chip-ok"),
    False: ("Not onboarded", "chip-warn"),
    None: ("Onboarding unknown", "chip-muted"),
}


def _carrier_compliance(carrier, as_of):
    """Snapshot-level read of the same signals the profile page shows row-wise.

    Insurance here is situated against the demo clock; per-load policy always
    compares against the pickup date instead.
    """
    readings = [
        (
            "Authority",
            carrier.authority_status or "not on file",
            assess_authority(carrier.authority_status),
        ),
        ("Safety", carrier.safety_rating or "not on file", assess_safety(carrier.safety_rating)),
    ]
    if carrier.insurance_expiry is None:
        readings.append(("Insurance", "expiry not on file", "unknown"))
    elif carrier.insurance_expiry < as_of:
        readings.append(("Insurance", f"expired {carrier.insurance_expiry:%b %-d, %Y}", "fail"))
    else:
        readings.append(("Insurance", f"valid to {carrier.insurance_expiry:%b %-d, %Y}", "pass"))
    detail = " · ".join(f"{label}: {raw}" for label, raw, _ in readings)
    results = [result for _, _, result in readings]
    if "fail" in results:
        return "Compliance issue", "chip-danger", detail
    if "unknown" in results:
        return "Needs review", "chip-warn", detail
    return "Compliant", "chip-ok", detail


@login_required
def carriers_list(request):
    snapshot = _active_snapshot()
    q = request.GET.get("q", "").strip()
    rows = []
    if snapshot:
        as_of = snapshot.as_of_at.date()
        carriers = (
            Carrier.objects.filter(dataset_snapshot=snapshot)
            .annotate(
                inquiry_count=Count("inquiries", distinct=True),
                candidacy_count=Count("load_candidacies", distinct=True),
            )
            .order_by(Lower("company_name").asc(nulls_last=True), "source_identifier")
        )
        if q:
            carriers = carriers.filter(
                Q(company_name__icontains=q)
                | Q(mc_number_raw__icontains=q)
                | Q(mc_number_normalized__icontains=q)
                | Q(dot_number_raw__icontains=q)
                | Q(dot_number_normalized__icontains=q)
            )
        for carrier in carriers:
            status_label, status_chip, status_detail = _carrier_compliance(carrier, as_of)
            onboarding_label, onboarding_chip = ONBOARDING_CHIPS[carrier.onboarded]
            rows.append(
                {
                    "carrier": carrier,
                    "status_label": status_label,
                    "status_chip": status_chip,
                    "status_detail": status_detail,
                    "onboarding_label": onboarding_label,
                    "onboarding_chip": onboarding_chip,
                }
            )
    return render(request, "freight/carriers.html", {"rows": rows, "q": q})


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

    # Eligible candidates lead in their deterministic strongest-first order;
    # everyone else follows grouped by how actionable their status is.
    STATUS_RANK = {
        "eligible": 0,
        "needs_clarification": 1,
        "needs_compliance_review": 2,
        "needs_onboarding": 3,
        "blocked": 4,
    }
    strongest_order = {c.id: index for index, c in enumerate(strongest_candidates(load))}
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
        status = None
        if assessment:
            status = assessment.final_status
            label, chip = STATUS_CHIPS.get(status, (status, "chip-muted"))
            reasons = [
                reason
                for reason in assessment.reasons.all()
                if reason.severity in ("blocker", "review")
            ]
        quote = candidate.current_quote
        candidates.append(
            {
                "candidate": candidate,
                "carrier": candidate.carrier,
                "quote": quote,
                "quote_per_mile": quote_as_per_mile(quote, load) if quote else None,
                "status_label": label,
                "status_chip": chip,
                "reasons": reasons,
                "_rank": (
                    STATUS_RANK.get(status, 5),
                    strongest_order.get(candidate.id, len(strongest_order)),
                    candidate.current_quote is None,
                    str(candidate.carrier_id),
                ),
            }
        )
    candidates.sort(key=lambda row: row["_rank"])

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

    from apps.workspace.views import assistant_thread_context

    assistant_context = assistant_thread_context(
        request.user.email or request.user.username, load=load
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
            "assistant_scope_load": load.external_load_id,
            **assistant_context,
        },
    )


POLICY_CHIPS = {
    "pass": ("chip-ok", "meets policy"),
    "fail": ("chip-danger", "fails policy"),
    "unknown": ("chip-warn", "needs review"),
}


@login_required
def carrier_profile(request, pk):
    carrier = get_object_or_404(
        Carrier.objects.select_related("dataset_snapshot").prefetch_related(
            "contacts", "equipment__equipment_type", "preferred_lanes__lane"
        ),
        pk=pk,
    )
    as_of = carrier.dataset_snapshot.as_of_at.date()

    def policy_row(label, raw, result, detail=""):
        chip, verdict = POLICY_CHIPS[result]
        return {"label": label, "raw": raw, "chip": chip, "verdict": verdict, "detail": detail}

    insurance_expired = carrier.insurance_expiry is not None and carrier.insurance_expiry < as_of
    compliance = [
        policy_row(
            "Authority", carrier.authority_status, assess_authority(carrier.authority_status)
        ),
        policy_row("Safety rating", carrier.safety_rating, assess_safety(carrier.safety_rating)),
        {
            # Insurance policy compares against each load's pickup date; the
            # profile only situates the expiry against the demo clock.
            "label": "Insurance",
            "raw": carrier.insurance_expiry,
            "chip": "chip-danger"
            if insurance_expired
            else ("chip-warn" if carrier.insurance_expiry is None else "chip-ok"),
            "verdict": "expired"
            if insurance_expired
            else ("unknown" if carrier.insurance_expiry is None else "valid"),
            "detail": f"as of {as_of}" if carrier.insurance_expiry else "",
        },
        {
            "label": "Onboarding",
            "raw": {True: "onboarded", False: "not onboarded", None: None}[carrier.onboarded],
            "chip": {True: "chip-ok", False: "chip-warn", None: "chip-warn"}[carrier.onboarded],
            "verdict": {True: "complete", False: "required", None: "unknown"}[carrier.onboarded],
            "detail": "",
        },
    ]

    gaps = []
    if not carrier.mc_number_raw:
        gaps.append("No MC number on file — identity cannot be verified deterministically.")
    if not carrier.dot_number_raw:
        gaps.append("No DOT number on file.")
    if carrier.insurance_expiry is None:
        gaps.append("Insurance expiry unknown.")
    if assess_authority(carrier.authority_status) == "unknown":
        gaps.append("Authority status is unrecognized or missing; policy treats it as unknown.")
    if assess_safety(carrier.safety_rating) == "unknown":
        gaps.append("Safety rating is unrecognized or missing.")
    if carrier.onboarded is None:
        gaps.append("Onboarding state unknown.")
    if not carrier.company_name:
        gaps.append("Company name missing from the dataset.")

    candidacies = []
    for candidate in (
        CarrierLoadCandidate.objects.filter(carrier=carrier)
        .select_related("load__equipment_type", "current_quote", "current_eligibility_assessment")
        .prefetch_related("current_eligibility_assessment__reasons")
        .order_by("load__pickup_date")
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
        candidacies.append(
            {
                "candidate": candidate,
                "load": candidate.load,
                "quote": candidate.current_quote,
                "status_label": label,
                "status_chip": chip,
                "reasons": reasons,
            }
        )

    history = (
        Inquiry.objects.filter(carrier=carrier)
        .select_related("communication_event__email_content", "load")
        .prefetch_related("quotes")
        .order_by("-communication_event__occurred_at", "-communication_event__received_at")
    )

    return render(
        request,
        "freight/carrier_profile.html",
        {
            "carrier": carrier,
            "primary_contact": next(
                (c for c in carrier.contacts.all() if c.is_primary),
                carrier.contacts.all()[0] if carrier.contacts.all() else None,
            ),
            "contacts": carrier.contacts.all(),
            "compliance": compliance,
            "gaps": gaps,
            "candidacies": candidacies,
            "history": history,
            "as_of": as_of,
        },
    )
