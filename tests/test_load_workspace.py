"""Load Workspace behavior (PRD 8.4): summary, market context, candidates, timeline."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from apps.candidates.engine import assess_candidate
from apps.inquiries.models import CarrierQuote
from tests.factories import (
    make_candidate,
    make_candidate_inquiry,
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
def broker(client):
    user = User.objects.create_user("broker@goodlanelogistics.com", password="x")
    client.force_login(user)
    return user


@pytest.fixture
def workspace():
    snapshot = make_snapshot(is_active=True, as_of_at=datetime(2026, 5, 25, 16, 0, tzinfo=UTC))
    box = make_equipment("box_truck", "Box Truck")
    load = make_load(
        snapshot=snapshot,
        external_load_id="29372450",
        lane=make_lane("PA", "NY"),
        equipment_type=box,
        distance_miles=97,
        offered_rate_usd=Decimal("420"),
        pickup_date=date(2026, 5, 23),
        internal_notes="needs liftgate",
    )
    make_market_rate(
        snapshot=snapshot,
        lane=load.lane,
        equipment_type=box,
        week_start=date(2026, 5, 11),
        minimum_rate_per_mile="2.59",
        average_rate_per_mile="2.94",
        maximum_rate_per_mile="3.47",
    )
    return snapshot, load


def add_candidate(snapshot, load, *, company, insurance=date(2027, 1, 1), amount=None):
    carrier = make_carrier(
        snapshot=snapshot,
        company_name=company,
        onboarded=True,
        authority_status="ACTIVE",
        safety_rating="Satisfactory",
        insurance_expiry=insurance,
    )
    event = make_communication_event(
        snapshot=snapshot, occurred_at=datetime(2026, 5, 18, 14, 0, tzinfo=UTC)
    )
    make_email_content(event=event, sender_name=company, body_text=f"From {company}")
    inquiry = make_inquiry(
        event=event,
        carrier=carrier,
        load=load,
        availability_status="confirmed",
        equipment_type=load.equipment_type,
        carrier_resolution_status="verified",
        load_resolution_status="verified",
        summary=f"{company} confirms availability.",
    )
    if amount is not None:
        CarrierQuote.objects.create(
            inquiry=inquiry,
            amount=amount,
            quote_type="carrier_quote",
            rate_basis="all_in",
            is_current=True,
            evidence_status="explicit",
        )
    candidate = make_candidate(carrier=carrier, load=load)
    make_candidate_inquiry(candidate=candidate, inquiry=inquiry)
    assess_candidate(candidate, triggering_inquiry=inquiry)
    return candidate


class TestLoadWorkspace:
    def test_shows_summary_market_band_and_verbatim_notes(self, client, broker, workspace):
        snapshot, load = workspace
        response = client.get(reverse("load_workspace", args=["29372450"]))
        content = response.content.decode()

        assert response.status_code == 200
        assert "Philadelphia, PA" in content
        assert "needs liftgate" in content  # internal note shown verbatim
        assert "above the market max" in content  # 4.33 vs 3.47 band position
        assert "2.94" in content

    def test_candidates_show_deterministic_status_and_blocked_reason(
        self, client, broker, workspace
    ):
        snapshot, load = workspace
        add_candidate(snapshot, load, company="Atlantic Carriers Inc", amount="455")
        add_candidate(
            snapshot,
            load,
            company="Blue Ridge Transport LLC",
            insurance=date(2026, 5, 15),
            amount="400",
        )

        response = client.get(reverse("load_workspace", args=["29372450"]))
        content = response.content.decode()
        assert "Eligible" in content
        assert "Blocked" in content
        assert "insurance_expired" in content or "Insurance" in content

    def test_best_rates_separate_overall_from_eligible(self, client, broker, workspace):
        snapshot, load = workspace
        add_candidate(snapshot, load, company="Atlantic Carriers Inc", amount="455")
        add_candidate(
            snapshot,
            load,
            company="Blue Ridge Transport LLC",
            insurance=date(2026, 5, 15),
            amount="400",
        )

        response = client.get(reverse("load_workspace", args=["29372450"]))
        content = response.content.decode()
        assert "$400" in content  # lowest overall (blocked carrier)
        assert "$455" in content  # lowest eligible

    def test_unknown_load_is_404(self, client, broker, workspace):
        response = client.get(reverse("load_workspace", args=["99999999"]))
        assert response.status_code == 404

    def test_candidate_row_opens_inquiry_review_while_name_opens_profile(
        self, client, broker, workspace
    ):
        """The broker's row-click intent is "what's going on / what needs clarifying"
        (the inquiry review), not the carrier dossier — that stays on the name."""
        snapshot, load = workspace
        candidate = add_candidate(snapshot, load, company="Atlantic Carriers Inc", amount="455")
        inquiry = candidate.candidate_inquiries.get().inquiry

        content = client.get(reverse("load_workspace", args=["29372450"])).content.decode()

        review_url = reverse("inquiry_review", args=[inquiry.pk])
        profile_url = reverse("carrier_profile", args=[candidate.carrier.pk])
        assert f"window.location='{review_url}'" in content
        assert f'href="{profile_url}"' in content

    def test_candidate_row_without_inquiry_falls_back_to_profile(self, client, broker, workspace):
        snapshot, load = workspace
        carrier = make_carrier(snapshot=snapshot, company_name="Silent Partner Trucking")
        make_candidate(carrier=carrier, load=load)

        content = client.get(reverse("load_workspace", args=["29372450"])).content.decode()

        profile_url = reverse("carrier_profile", args=[carrier.pk])
        assert f"window.location='{profile_url}'" in content


class TestLoadsList:
    def test_lists_loads_with_links(self, client, broker, workspace):
        snapshot, load = workspace
        response = client.get(reverse("loads"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "29372450" in content

    def test_open_loads_first_cancelled_last(self, client, broker):
        snapshot = make_snapshot(is_active=True)
        equipment = make_equipment()
        for external_id, status in (
            ("29372491", "cancelled"),
            ("29372492", "delivered"),
            ("29372493", "open"),
            ("29372494", "covered"),
        ):
            make_load(
                snapshot=snapshot,
                equipment_type=equipment,
                external_load_id=external_id,
                status=status,
                pickup_date=date(2026, 5, 20),
            )

        content = client.get(reverse("loads")).content.decode()

        assert (
            content.index("29372493")  # open
            < content.index("29372494")  # covered
            < content.index("29372492")  # delivered
            < content.index("29372491")  # cancelled
        )
