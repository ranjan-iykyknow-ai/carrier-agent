"""Read-time rate intelligence and deterministic ranking (spec 3G).

Nothing here is stored: best rate, band position, and strongest candidate are
computed from current pointers every time, so no stale flag can contradict the
underlying facts. Historical rates are context, never a market-price claim.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from apps.candidates.models import CarrierLoadCandidate, EligibilityAssessment
from apps.freight.models import Load, MarketRateHistory
from apps.inquiries.models import CarrierQuote

_CENT = Decimal("0.01")

COMPARABLE_QUOTE_TYPES = {
    CarrierQuote.QuoteType.CARRIER_QUOTE,
    CarrierQuote.QuoteType.CARRIER_COUNTEROFFER,
}


@dataclass
class MarketContext:
    history: MarketRateHistory
    offered_per_mile: Decimal | None
    band_position: str | None  # below_minimum | within_band | above_maximum


@dataclass
class RateAnswer:
    candidate_id: str
    quote_id: str
    all_in: Decimal
    per_mile: Decimal | None
    eligibility_status: str | None


@dataclass
class BestRates:
    lowest_overall: RateAnswer | None
    lowest_eligible: RateAnswer | None


def offered_per_mile(load: Load) -> Decimal | None:
    if load.offered_rate_usd is None or not load.distance_miles or load.distance_miles <= 0:
        return None
    return (load.offered_rate_usd / Decimal(load.distance_miles)).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )


def market_context(load: Load) -> MarketContext | None:
    """Latest week on or before pickup, exact directional lane and equipment only."""
    if load.equipment_type_id is None:
        return None
    history = (
        MarketRateHistory.objects.filter(
            dataset_snapshot=load.dataset_snapshot,
            lane=load.lane,
            equipment_type=load.equipment_type,
            week_start__lte=load.pickup_date,
        )
        .order_by("-week_start")
        .first()
    )
    if history is None:
        return None
    per_mile = offered_per_mile(load)
    position = None
    if per_mile is not None:
        if per_mile < history.minimum_rate_per_mile:
            position = "below_minimum"
        elif per_mile > history.maximum_rate_per_mile:
            position = "above_maximum"
        else:
            position = "within_band"
    return MarketContext(history=history, offered_per_mile=per_mile, band_position=position)


def _distance(load: Load) -> Decimal | None:
    if load.distance_miles and load.distance_miles > 0:
        return Decimal(load.distance_miles)
    return None


def quote_as_all_in(quote: CarrierQuote, load: Load) -> Decimal | None:
    if quote.rate_basis == CarrierQuote.RateBasis.ALL_IN:
        return quote.amount
    if quote.rate_basis == CarrierQuote.RateBasis.PER_MILE:
        distance = _distance(load)
        if distance is None:
            return None
        return (quote.amount * distance).quantize(_CENT, rounding=ROUND_HALF_UP)
    return None  # hourly / unknown bases are never comparable


def quote_as_per_mile(quote: CarrierQuote, load: Load) -> Decimal | None:
    if quote.rate_basis == CarrierQuote.RateBasis.PER_MILE:
        return quote.amount
    if quote.rate_basis == CarrierQuote.RateBasis.ALL_IN:
        distance = _distance(load)
        if distance is None:
            return None
        return (quote.amount / distance).quantize(_CENT, rounding=ROUND_HALF_UP)
    return None


def _comparable_quote(candidate: CarrierLoadCandidate, load: Load) -> RateAnswer | None:
    quote = candidate.current_quote
    if quote is None:
        return None
    if quote.quote_type not in COMPARABLE_QUOTE_TYPES:
        return None
    if quote.evidence_status != "explicit":
        return None
    if quote.currency != "USD":
        return None
    all_in = quote_as_all_in(quote, load)
    if all_in is None:
        return None
    assessment = candidate.current_eligibility_assessment
    return RateAnswer(
        candidate_id=candidate.id,
        quote_id=quote.id,
        all_in=all_in,
        per_mile=quote_as_per_mile(quote, load),
        eligibility_status=assessment.final_status if assessment else None,
    )


def best_rates(load: Load) -> BestRates:
    candidates = CarrierLoadCandidate.objects.filter(load=load).select_related(
        "current_quote", "current_eligibility_assessment"
    )
    answers = [a for a in (_comparable_quote(c, load) for c in candidates) if a]
    answers.sort(key=lambda a: (a.all_in, str(a.candidate_id)))
    eligible = [
        a for a in answers if a.eligibility_status == EligibilityAssessment.FinalStatus.ELIGIBLE
    ]
    return BestRates(
        lowest_overall=answers[0] if answers else None,
        lowest_eligible=eligible[0] if eligible else None,
    )


def strongest_candidates(load: Load) -> list[CarrierLoadCandidate]:
    """Deterministic ordering over Eligible candidates — no opaque score.

    Prefers a comparable explicit current quote, lower amount, higher known
    reliability, more completed loads, faster known response, then a stable
    carrier id. Missing performance values sort after known values.
    """
    candidates = list(
        CarrierLoadCandidate.objects.filter(
            load=load,
            current_eligibility_assessment__final_status=(
                EligibilityAssessment.FinalStatus.ELIGIBLE
            ),
        ).select_related("carrier", "current_quote", "current_eligibility_assessment")
    )

    def sort_key(candidate: CarrierLoadCandidate):
        answer = _comparable_quote(candidate, load)
        carrier = candidate.carrier
        return (
            answer is None,  # quoted candidates first
            answer.all_in if answer else Decimal(0),
            carrier.reliability_score is None,
            -(carrier.reliability_score or 0),
            carrier.loads_completed_with_goodlane is None,
            -(carrier.loads_completed_with_goodlane or 0),
            carrier.avg_response_time_hours is None,
            carrier.avg_response_time_hours or 0,
            str(carrier.id),
        )

    return sorted(candidates, key=sort_key)
