"""Carrier Profile (PRD 8.6): who they are, why they're eligible or not, history."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from apps.candidates.engine import assess_candidate
from apps.candidates.models import CandidateInquiry
from tests.factories import (
    make_candidate,
    make_carrier,
    make_communication_event,
    make_email_content,
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


def build_profile_case(**carrier_kwargs):
    snapshot = make_snapshot(is_active=True, as_of_at=datetime(2026, 5, 25, 16, 0, tzinfo=UTC))
    defaults = {
        "company_name": "Blue Ridge Transport LLC",
        "mc_number_raw": "MC-884411",
        "dot_number_raw": "2211334",
        "authority_status": "ACTIVE",
        "safety_rating": "Satisfactory",
        "insurance_expiry": date(2027, 1, 10),
        "onboarded": True,
        "reliability_score": Decimal("4.5"),
        "loads_completed_with_goodlane": 27,
        "avg_response_time_hours": Decimal("2.50"),
        "factoring_company": "Apex Capital",
        "payment_terms_preference": "quickpay",
        "notes": "Prefers early pickups.",
    }
    defaults.update(carrier_kwargs)
    carrier = make_carrier(snapshot=snapshot, **defaults)
    carrier.contacts.create(
        name="Carlos Mendez",
        email_raw="carlos@blueridge.com",
        email_normalized="carlos@blueridge.com",
        phone_raw="(555) 210-8890",
        is_primary=True,
    )
    return snapshot, carrier


class TestCarrierProfile:
    def test_requires_login(self, client):
        _, carrier = build_profile_case()
        response = client.get(reverse("carrier_profile", args=[carrier.pk]))
        assert response.status_code == 302

    def test_renders_identity_compliance_and_performance(self, client, broker):
        _, carrier = build_profile_case()

        response = client.get(reverse("carrier_profile", args=[carrier.pk]))

        html = response.content.decode()
        assert response.status_code == 200
        assert "Blue Ridge Transport LLC" in html
        assert "MC-884411" in html
        assert "Carlos Mendez" in html
        assert "Apex Capital" in html
        assert "Prefers early pickups." in html
        assert "4.5" in html
        assert "27" in html

    def test_raw_vocabulary_shown_verbatim_with_interpretation(self, client, broker):
        _, carrier = build_profile_case(
            authority_status="unknown", safety_rating=None, insurance_expiry=date(2026, 5, 1)
        )

        response = client.get(reverse("carrier_profile", args=[carrier.pk]))

        html = response.content.decode()
        # The literal dataset value is preserved, and the policy reads it as unknown.
        assert "unknown" in html
        assert "not on file" in html  # missing safety rating stated, not hidden
        assert "expired" in html.lower()  # insurance vs the demo clock

    def test_lists_candidacies_with_eligibility_and_load_links(self, client, broker):
        snapshot, carrier = build_profile_case()
        equipment = make_equipment()
        load = make_load(
            snapshot=snapshot,
            external_load_id="29372460",
            equipment_type=equipment,
            pickup_date=date(2026, 5, 28),
        )
        candidate = make_candidate(carrier=carrier, load=load)
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
        )
        CandidateInquiry.objects.create(
            candidate=candidate, inquiry=inquiry, relationship="supporting"
        )
        assess_candidate(candidate, triggering_inquiry=inquiry)

        response = client.get(reverse("carrier_profile", args=[carrier.pk]))

        html = response.content.decode()
        assert reverse("load_workspace", args=["29372460"]) in html
        assert "Eligible" in html

    def test_communication_history_links_to_inquiry_review(self, client, broker):
        snapshot, carrier = build_profile_case()
        event = make_communication_event(snapshot=snapshot)
        make_email_content(event=event, subject="Rate for the Scranton run")
        inquiry = make_inquiry(event=event, carrier=carrier, summary="Asked about Scranton rate")

        response = client.get(reverse("carrier_profile", args=[carrier.pk]))

        html = response.content.decode()
        assert reverse("inquiry_review", args=[inquiry.pk]) in html
        assert "Asked about Scranton rate" in html


class TestWorkspaceLinksToProfile:
    def test_candidate_rows_link_to_carrier_profile(self, client, broker):
        snapshot = make_snapshot(is_active=True)
        equipment = make_equipment()
        load = make_load(snapshot=snapshot, equipment_type=equipment, external_load_id="29372470")
        carrier = make_carrier(snapshot=snapshot, company_name="Keystone Freight")
        make_candidate(carrier=carrier, load=load)

        response = client.get(reverse("load_workspace", args=["29372470"]))

        assert reverse("carrier_profile", args=[carrier.pk]) in response.content.decode()
