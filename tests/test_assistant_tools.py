"""Read-only assistant tools (spec 3H): typed, scoped, deterministic."""

from datetime import date
from decimal import Decimal

import pytest

from apps.candidates.engine import assess_candidate
from apps.candidates.models import CandidateInquiry
from apps.inquiries.models import CarrierQuote
from apps.workspace.tools import ToolFailure, ToolScope, execute_tool, tool_schemas
from tests.factories import (
    make_candidate,
    make_carrier,
    make_communication_event,
    make_email_content,
    make_equipment,
    make_inquiry,
    make_lane,
    make_load,
    make_market_rate,
    make_snapshot,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    snapshot = make_snapshot(is_active=True)
    equipment = make_equipment()
    lane = make_lane("PA", "NY")
    load = make_load(
        snapshot=snapshot,
        external_load_id="29372450",
        lane=lane,
        equipment_type=equipment,
        distance_miles=97,
        offered_rate_usd=Decimal("420"),
        pickup_date=date(2026, 5, 23),
    )
    make_market_rate(
        snapshot=snapshot,
        lane=lane,
        equipment_type=equipment,
        week_start=date(2026, 5, 11),
        minimum_rate_per_mile="2.59",
        average_rate_per_mile="2.94",
        maximum_rate_per_mile="3.47",
    )
    carrier = make_carrier(
        snapshot=snapshot,
        company_name="Blue Ridge Transport LLC",
        mc_number_raw="712843",
        mc_number_normalized="712843",
        onboarded=True,
        authority_status="ACTIVE",
        safety_rating="Satisfactory",
        insurance_expiry=date(2027, 1, 1),
    )
    event = make_communication_event(snapshot=snapshot)
    make_email_content(event=event)
    inquiry = make_inquiry(
        event=event,
        carrier=carrier,
        load=load,
        carrier_resolution_status="verified",
        load_resolution_status="verified",
        availability_status="confirmed",
        equipment_type=equipment,
        summary="Confirmed availability, quoted 395 all-in.",
    )
    CarrierQuote.objects.create(
        inquiry=inquiry,
        amount=Decimal("395"),
        quote_type="carrier_quote",
        rate_basis="all_in",
        evidence_status="explicit",
        is_current=True,
    )
    candidate = make_candidate(carrier=carrier, load=load)
    CandidateInquiry.objects.create(candidate=candidate, inquiry=inquiry, relationship="supporting")
    assess_candidate(candidate, triggering_inquiry=inquiry)
    return snapshot, load, carrier, inquiry, candidate


class TestToolExecution:
    def test_get_load_returns_band_and_stable_ids(self, world):
        snapshot, load, *_ = world
        scope = ToolScope(snapshot=snapshot, load=None)

        result = execute_tool("get_load", {"external_load_id": "29372450"}, scope)

        assert result.facts["offered_rate_usd"] == "420.00"
        assert result.facts["market_band_position"] == "above_maximum"
        assert "load:29372450" in result.record_ids

    def test_load_scope_denies_other_loads(self, world):
        snapshot, load, *_ = world
        make_load(snapshot=snapshot, external_load_id="29999999")
        scope = ToolScope(snapshot=snapshot, load=load)

        with pytest.raises(ToolFailure) as excinfo:
            execute_tool("get_load", {"external_load_id": "29999999"}, scope)
        assert excinfo.value.code == "scope_denied"

    def test_get_candidate_assessment_explains_eligibility(self, world):
        snapshot, load, carrier, _, candidate = world
        scope = ToolScope(snapshot=snapshot, load=load)

        result = execute_tool(
            "get_candidate_assessment",
            {"external_load_id": "29372450", "mc_number": "712843"},
            scope,
        )

        assert result.facts["final_status"] == "eligible"
        assert any(rid.startswith("assessment:") for rid in result.record_ids)
        assert result.facts["current_quote"]["amount"] == "395.00"

    def test_unknown_tool_and_bad_arguments_fail_controlled(self, world):
        snapshot, load, *_ = world
        scope = ToolScope(snapshot=snapshot, load=None)

        with pytest.raises(ToolFailure):
            execute_tool("drop_tables", {}, scope)
        with pytest.raises(ToolFailure):
            execute_tool("get_load", {}, scope)  # missing required argument
        with pytest.raises(ToolFailure):
            execute_tool("get_load", {"external_load_id": "no-such-load"}, scope)

    def test_search_inquiries_is_bounded(self, world):
        snapshot, *_ = world
        scope = ToolScope(snapshot=snapshot, load=None)

        result = execute_tool("search_inquiries", {"query": "availability"}, scope)

        assert len(result.record_ids) <= 10
        assert any(rid.startswith("inquiry:") for rid in result.record_ids)

    def test_schemas_expose_only_approved_tools(self):
        names = {schema["function"]["name"] for schema in tool_schemas()}
        assert names == {
            "get_load",
            "get_load_inquiries",
            "search_inquiries",
            "get_carrier_profile",
            "get_carrier_history",
            "get_candidate_assessment",
            "get_market_rate_context",
        }
