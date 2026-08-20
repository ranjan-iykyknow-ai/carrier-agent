"""Page behavior: authentication, dashboard metrics, and the unified inbox."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from apps.comms.models import IngestionJob
from tests.factories import (
    make_ai_operation,
    make_communication_event,
    make_email_content,
    make_equipment,
    make_ingestion_job,
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


class TestAuthentication:
    def test_anonymous_users_are_redirected_to_login(self, client):
        response = client.get(reverse("dashboard"))
        assert response.status_code == 302
        assert reverse("login") in response.url

    def test_login_page_renders(self, client):
        response = client.get(reverse("login"))
        assert response.status_code == 200
        assert b"Sign in" in response.content


class TestDashboard:
    def test_shows_load_counts_and_open_loads_with_band_position(self, client, broker):
        snapshot = make_snapshot(is_active=True, as_of_at=datetime(2026, 5, 25, 16, 0, tzinfo=UTC))
        reefer = make_equipment("refrigerated", "Refrigerated")
        load = make_load(
            snapshot=snapshot,
            external_load_id="29372501",
            status="open",
            lane=make_lane("NJ", "MD"),
            equipment_type=reefer,
            distance_miles=115,
            offered_rate_usd=Decimal("520"),
            pickup_date=datetime(2026, 5, 25, tzinfo=UTC).date(),
        )
        make_load(snapshot=snapshot, status="delivered")
        make_market_rate(
            snapshot=snapshot,
            lane=load.lane,
            equipment_type=reefer,
            week_start=datetime(2026, 5, 11, tzinfo=UTC).date(),
            minimum_rate_per_mile="5.54",
            average_rate_per_mile="6.29",
            maximum_rate_per_mile="7.42",
        )

        response = client.get(reverse("dashboard"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "29372501" in content
        assert "Below market min" in content
        assert "Open loads" in content

    def test_missing_metrics_render_as_unavailable(self, client, broker):
        make_snapshot(is_active=True)
        response = client.get(reverse("dashboard"))
        assert b"unavailable" in response.content

    def test_ai_summary_uses_real_operation_data(self, client, broker):
        snapshot = make_snapshot(is_active=True)
        event = make_communication_event(snapshot=snapshot)
        make_ingestion_job(event=event, status="completed")
        make_ai_operation(
            status="completed",
            latency_ms=4200,
            estimated_cost=Decimal("0.001200"),
        )

        response = client.get(reverse("dashboard"))
        content = response.content.decode()
        assert "4200" in content or "4.2" in content
        assert "$0.0012" in content


class TestInbox:
    def test_lists_communications_with_attention_group_first(self, client, broker):
        snapshot = make_snapshot(is_active=True)
        healthy = make_communication_event(
            snapshot=snapshot, occurred_at=datetime(2026, 5, 18, 14, 0, tzinfo=UTC)
        )
        make_email_content(event=healthy, sender_name="Desmond Okafor")
        make_ingestion_job(event=healthy, status="completed")
        make_inquiry(event=healthy, review_status="approved")

        attention = make_communication_event(
            snapshot=snapshot, occurred_at=datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
        )
        make_email_content(event=attention, sender_name="Tariq")
        make_ingestion_job(event=attention, status="needs_review")
        make_inquiry(event=attention, review_status="needs_review")

        response = client.get(reverse("inbox"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "Needs attention" in content
        assert content.index("Tariq") < content.index("Desmond")

    def test_filter_by_processing_state(self, client, broker):
        snapshot = make_snapshot(is_active=True)
        completed = make_communication_event(snapshot=snapshot)
        make_email_content(event=completed, sender_name="CompletedSender")
        make_ingestion_job(event=completed, status="completed")

        failed = make_communication_event(snapshot=snapshot)
        make_email_content(event=failed, sender_name="FailedSender")
        job = make_ingestion_job(event=failed)
        IngestionJob.objects.filter(id=job.id).update(
            status="failed", last_error_code="provider_unavailable"
        )

        response = client.get(reverse("inbox"), {"state": "failed"})
        content = response.content.decode()
        assert "FailedSender" in content
        assert "CompletedSender" not in content
