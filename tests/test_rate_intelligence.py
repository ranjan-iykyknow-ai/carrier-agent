"""Market context, band position, comparable conversion, best rates, ranking (spec 3G)."""

from datetime import date
from decimal import Decimal

import pytest

from apps.candidates.selectors import (
    best_rates,
    market_context,
    quote_as_all_in,
    quote_as_per_mile,
    strongest_candidates,
)
from apps.inquiries.models import CarrierQuote
from tests.factories import (
    make_candidate,
    make_carrier,
    make_communication_event,
    make_equipment,
    make_inquiry,
    make_lane,
    make_load,
    make_market_rate,
    make_snapshot,
)

pytestmark = pytest.mark.django_db


def reefer_load(snapshot):
    """Load 29372501: $520 over 115 mi = $4.52/mi vs reefer band 5.54-7.67."""
    return make_load(
        snapshot=snapshot,
        external_load_id="29372501",
        origin_state="NJ",
        destination_state="MD",
        lane=make_lane("NJ", "MD"),
        equipment_type=make_equipment("refrigerated", "Refrigerated"),
        distance_miles=115,
        offered_rate_usd=Decimal("520"),
        pickup_date=date(2026, 5, 25),
    )


def reefer_band(snapshot):
    return make_market_rate(
        snapshot=snapshot,
        week_start=date(2026, 5, 11),
        lane=make_lane("NJ", "MD"),
        equipment_type=make_equipment("refrigerated", "Refrigerated"),
        minimum_rate_per_mile="5.54",
        average_rate_per_mile="6.29",
        maximum_rate_per_mile="7.42",
        load_volume=10,
    )


class TestMarketContext:
    def test_selects_latest_week_on_or_before_pickup(self, django_assert_num_queries):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)
        reefer_band(snapshot)
        later = make_market_rate(
            snapshot=snapshot,
            week_start=date(2026, 5, 18),
            lane=make_lane("NJ", "MD"),
            equipment_type=make_equipment("refrigerated", "Refrigerated"),
            minimum_rate_per_mile="5.72",
            average_rate_per_mile="6.50",
            maximum_rate_per_mile="7.67",
            load_volume=2,
        )
        make_market_rate(  # after pickup: never selected
            snapshot=snapshot,
            week_start=date(2026, 6, 1),
            lane=make_lane("NJ", "MD"),
            equipment_type=make_equipment("refrigerated", "Refrigerated"),
        )

        context = market_context(load)
        assert context.history.id == later.id
        assert context.offered_per_mile == Decimal("4.52")
        assert context.band_position == "below_minimum"

    def test_no_substitute_lane_or_equipment(self):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)
        make_market_rate(  # same lane, wrong equipment
            snapshot=snapshot,
            lane=make_lane("NJ", "MD"),
            equipment_type=make_equipment("box_truck", "Box Truck"),
        )
        assert market_context(load) is None

    def test_band_positions(self):
        snapshot = make_snapshot()
        band = reefer_band(snapshot)
        load = reefer_load(snapshot)
        context = market_context(load)
        assert context.band_position == "below_minimum"

        load.offered_rate_usd = Decimal("750")  # 6.52/mi -> within
        load.save()
        assert market_context(load).band_position == "within_band"

        load.offered_rate_usd = Decimal("900")  # 7.83/mi -> above max
        load.save()
        assert market_context(load).band_position == "above_maximum"
        band.refresh_from_db()
        assert band.maximum_rate_per_mile == Decimal("7.42")

    def test_unknown_distance_means_no_per_mile_or_band_position(self):
        snapshot = make_snapshot()
        reefer_band(snapshot)
        load = reefer_load(snapshot)
        load.distance_miles = None
        load.save()
        context = market_context(load)
        assert context.offered_per_mile is None
        assert context.band_position is None


class TestConversion:
    def test_all_in_converts_to_per_mile_and_back(self):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)
        inquiry = make_inquiry(event=make_communication_event(snapshot=snapshot))
        quote = CarrierQuote.objects.create(
            inquiry=inquiry,
            amount="460",
            quote_type="carrier_quote",
            rate_basis="all_in",
            is_current=True,
            evidence_status="explicit",
        )
        quote.refresh_from_db()
        assert quote_as_per_mile(quote, load) == Decimal("4.00")
        assert quote_as_all_in(quote, load) == Decimal("460")

    def test_per_mile_converts_to_all_in(self):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)
        inquiry = make_inquiry(event=make_communication_event(snapshot=snapshot))
        quote = CarrierQuote.objects.create(
            inquiry=inquiry,
            amount="4.00",
            quote_type="carrier_counteroffer",
            rate_basis="per_mile",
            is_current=True,
            evidence_status="explicit",
        )
        quote.refresh_from_db()
        assert quote_as_all_in(quote, load) == Decimal("460.00")

    def test_conversion_requires_known_positive_distance(self):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)
        load.distance_miles = None
        load.save()
        inquiry = make_inquiry(event=make_communication_event(snapshot=snapshot))
        quote = CarrierQuote.objects.create(
            inquiry=inquiry,
            amount="4.00",
            quote_type="carrier_quote",
            rate_basis="per_mile",
            is_current=True,
            evidence_status="explicit",
        )
        quote.refresh_from_db()
        assert quote_as_all_in(quote, load) is None
        assert quote_as_per_mile(quote, load) == Decimal("4.00")


def candidate_with_quote(
    load,
    *,
    amount,
    status="eligible",
    basis="all_in",
    quote_type="carrier_quote",
    evidence="explicit",
    reliability=None,
    loads_done=None,
    response_hours=None,
):
    snapshot = load.dataset_snapshot
    carrier = make_carrier(
        snapshot=snapshot,
        reliability_score=reliability,
        loads_completed_with_goodlane=loads_done,
        avg_response_time_hours=response_hours,
    )
    candidate = make_candidate(carrier=carrier, load=load)
    quote = None
    if amount is not None:
        inquiry = make_inquiry(
            event=make_communication_event(snapshot=snapshot),
            carrier=carrier,
            load=load,
        )
        quote = CarrierQuote.objects.create(
            inquiry=inquiry,
            amount=amount,
            quote_type=quote_type,
            rate_basis=basis,
            is_current=True,
            evidence_status=evidence,
        )
    from tests.factories import make_eligibility_assessment

    assessment = make_eligibility_assessment(candidate=candidate, final_status=status)
    candidate.current_quote = quote
    candidate.current_eligibility_assessment = assessment
    candidate.save()
    return candidate


class TestBestRates:
    def test_lowest_overall_and_lowest_eligible_are_separate(self):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)
        blocked = candidate_with_quote(load, amount="400", status="blocked")
        eligible = candidate_with_quote(load, amount="455", status="eligible")

        rates = best_rates(load)
        assert rates.lowest_overall.candidate_id == blocked.id
        assert rates.lowest_overall.all_in == Decimal("400")
        assert rates.lowest_eligible.candidate_id == eligible.id

    def test_ambiguous_and_broker_reference_amounts_never_win(self):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)
        candidate_with_quote(load, amount="100", quote_type="broker_rate_reference")
        candidate_with_quote(load, amount="150", quote_type="ambiguous")
        candidate_with_quote(load, amount="200", evidence="inferred")
        real = candidate_with_quote(load, amount="455")

        rates = best_rates(load)
        assert rates.lowest_overall.candidate_id == real.id

    def test_eligible_candidate_without_quote_cannot_win(self):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)
        candidate_with_quote(load, amount=None, status="eligible")
        quoted = candidate_with_quote(load, amount="500", status="eligible")
        assert best_rates(load).lowest_eligible.candidate_id == quoted.id

    def test_per_mile_quote_compares_via_conversion(self):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)  # 115 miles
        per_mile = candidate_with_quote(load, amount="3.50", basis="per_mile")  # 402.50
        candidate_with_quote(load, amount="455", basis="all_in")
        assert best_rates(load).lowest_overall.candidate_id == per_mile.id


class TestStrongestCandidates:
    def test_ordering_prefers_quote_then_amount_then_reliability(self):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)
        no_quote = candidate_with_quote(load, amount=None, reliability="4.9")
        cheap = candidate_with_quote(load, amount="450", reliability="3.0")
        pricey_reliable = candidate_with_quote(load, amount="480", reliability="4.5")
        candidate_with_quote(load, amount="999", status="blocked")

        ordered = strongest_candidates(load)
        assert [c.id for c in ordered] == [cheap.id, pricey_reliable.id, no_quote.id]

    def test_missing_reliability_sorts_after_known(self):
        snapshot = make_snapshot()
        load = reefer_load(snapshot)
        unknown = candidate_with_quote(load, amount="450", reliability=None)
        known = candidate_with_quote(load, amount="450", reliability="2.1")

        ordered = strongest_candidates(load)
        assert [c.id for c in ordered] == [known.id, unknown.id]
