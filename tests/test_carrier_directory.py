"""Carriers directory: every registered carrier, its status, one click to the profile."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from tests.factories import (
    make_candidate,
    make_carrier,
    make_communication_event,
    make_equipment,
    make_inquiry,
    make_load,
    make_snapshot,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def broker(client):
    user = User.objects.create_user("broker@goodlanelogistics.com", password="x")
    client.force_login(user)
    return user


def active_snapshot():
    return make_snapshot(is_active=True, as_of_at=datetime(2026, 5, 25, 16, 0, tzinfo=UTC))


COMPLIANT = {
    "authority_status": "ACTIVE",
    "safety_rating": "Satisfactory",
    "insurance_expiry": date(2027, 1, 10),
    "onboarded": True,
}


class TestCarrierDirectory:
    def test_requires_login(self, client):
        response = client.get(reverse("carriers"))
        assert response.status_code == 302

    def test_sidebar_links_to_carriers(self, client, broker):
        active_snapshot()
        response = client.get(reverse("dashboard"))
        assert reverse("carriers") in response.content.decode()

    def test_lists_active_snapshot_carriers_with_profile_links(self, client, broker):
        snapshot = active_snapshot()
        carrier = make_carrier(
            snapshot=snapshot,
            company_name="Blue Ridge Transport LLC",
            mc_number_raw="MC-884411",
            mc_number_normalized="884411",
            **COMPLIANT,
        )
        stale = make_snapshot(is_active=False)
        make_carrier(snapshot=stale, company_name="Ghost Freight Co")

        response = client.get(reverse("carriers"))

        html = response.content.decode()
        assert "Blue Ridge Transport LLC" in html
        assert "MC-884411" in html
        assert reverse("carrier_profile", args=[carrier.pk]) in html
        assert "Ghost Freight Co" not in html

    def test_compliant_and_onboarded_status_chips(self, client, broker):
        snapshot = active_snapshot()
        make_carrier(
            snapshot=snapshot,
            company_name="Blue Ridge Transport LLC",
            reliability_score=Decimal("4.5"),
            loads_completed_with_goodlane=27,
            **COMPLIANT,
        )

        response = client.get(reverse("carriers"))

        html = response.content.decode()
        assert "Compliant" in html
        assert "Onboarded" in html
        assert "4.5" in html
        assert "27" in html

    def test_expired_insurance_reads_as_compliance_issue(self, client, broker):
        snapshot = active_snapshot()
        make_carrier(
            snapshot=snapshot,
            company_name="Lapsed Logistics",
            authority_status="ACTIVE",
            safety_rating="Satisfactory",
            insurance_expiry=date(2026, 5, 1),  # before the demo clock
            onboarded=True,
        )

        response = client.get(reverse("carriers"))

        assert "Compliance issue" in response.content.decode()

    def test_unknown_fields_read_as_needs_review_not_compliant(self, client, broker):
        snapshot = active_snapshot()
        make_carrier(
            snapshot=snapshot,
            company_name="Mystery Movers",
            authority_status=None,
            safety_rating=None,
            insurance_expiry=None,
            onboarded=None,
        )

        response = client.get(reverse("carriers"))

        html = response.content.decode()
        assert "Needs review" in html
        assert "Compliant" not in html

    def test_not_onboarded_is_stated(self, client, broker):
        snapshot = active_snapshot()
        make_carrier(snapshot=snapshot, company_name="Newcomer Haulage", onboarded=False)

        response = client.get(reverse("carriers"))

        assert "Not onboarded" in response.content.decode()

    def test_unnamed_carrier_still_listed(self, client, broker):
        snapshot = active_snapshot()
        make_carrier(snapshot=snapshot, company_name=None, mc_number_raw="MC-777001")

        response = client.get(reverse("carriers"))

        html = response.content.decode()
        assert "(unnamed carrier)" in html
        assert "MC-777001" in html

    def test_activity_counts(self, client, broker):
        snapshot = active_snapshot()
        carrier = make_carrier(snapshot=snapshot, company_name="Busy Bee Freight", **COMPLIANT)
        equipment = make_equipment()
        for external_id in ("29372480", "29372481"):
            load = make_load(
                snapshot=snapshot, equipment_type=equipment, external_load_id=external_id
            )
            make_candidate(carrier=carrier, load=load)
        event = make_communication_event(snapshot=snapshot)
        make_inquiry(event=event, carrier=carrier)

        response = client.get(reverse("carriers"))

        row = response.content.decode()
        assert 'data-inquiries="1"' in row
        assert 'data-candidacies="2"' in row

    def test_ordering_is_case_insensitive_alphabetical(self, client, broker):
        snapshot = active_snapshot()
        for name in ("BRAVO CARRIERS", "acme haulage", "Alpine Freight"):
            make_carrier(snapshot=snapshot, company_name=name)

        html = client.get(reverse("carriers")).content.decode()

        assert (
            html.index("acme haulage") < html.index("Alpine Freight") < html.index("BRAVO CARRIERS")
        )

    def test_search_filters_by_name_and_mc(self, client, broker):
        snapshot = active_snapshot()
        make_carrier(
            snapshot=snapshot,
            company_name="Blue Ridge Transport LLC",
            mc_number_raw="MC-884411",
            mc_number_normalized="884411",
        )
        make_carrier(snapshot=snapshot, company_name="Keystone Freight")

        by_name = client.get(reverse("carriers"), {"q": "keystone"}).content.decode()
        assert "Keystone Freight" in by_name
        assert "Blue Ridge Transport LLC" not in by_name

        by_mc = client.get(reverse("carriers"), {"q": "884411"}).content.decode()
        assert "Blue Ridge Transport LLC" in by_mc
        assert "Keystone Freight" not in by_mc

    def test_empty_search_states_no_match(self, client, broker):
        snapshot = active_snapshot()
        make_carrier(snapshot=snapshot, company_name="Blue Ridge Transport LLC")

        response = client.get(reverse("carriers"), {"q": "zzz-no-such"})

        assert "No carrier" in response.content.decode()
