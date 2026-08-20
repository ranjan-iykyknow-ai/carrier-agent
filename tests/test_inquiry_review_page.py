"""Inquiry Review page: evidence beside extraction, auditable broker actions."""

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.urls import reverse

from apps.comms.models import IngestionJob
from apps.inquiries.models import (
    CarrierQuote,
    EvidenceSpan,
    Inquiry,
    InquiryFieldAssessment,
    InquiryReviewReason,
)
from apps.workspace.models import InquiryReviewAction
from tests.factories import (
    make_call_recording,
    make_carrier,
    make_communication_event,
    make_email_content,
    make_ingestion_job,
    make_inquiry,
    make_load,
    make_snapshot,
    make_transcript,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def broker(client):
    user = User.objects.create_user("broker@goodlanelogistics.com", password="x")
    client.force_login(user)
    return user


def build_email_case(**inquiry_kwargs):
    snapshot = make_snapshot(is_active=True)
    event = make_communication_event(snapshot=snapshot)
    email = make_email_content(
        event=event,
        subject="Truck available Tuesday",
        body_text="MC 884411 here. We can cover Allentown for $1850 all in.",
    )
    make_ingestion_job(event=event, status=IngestionJob.Status.NEEDS_REVIEW)
    defaults = {"review_status": Inquiry.ReviewStatus.NEEDS_REVIEW}
    defaults.update(inquiry_kwargs)
    inquiry = make_inquiry(event=event, **defaults)
    return snapshot, event, email, inquiry


class TestReviewPageEmail:
    def test_requires_login(self, client):
        _, _, _, inquiry = build_email_case()
        response = client.get(reverse("inquiry_review", args=[inquiry.pk]))
        assert response.status_code == 302
        assert reverse("login") in response.url

    def test_renders_email_evidence_and_extraction(self, client, broker):
        _, event, email, inquiry = build_email_case(summary="Availability with rate")
        span = EvidenceSpan.objects.create(
            communication_event=event,
            source_part="body",
            stable_evidence_id=f"{event.stable_evidence_id}#body:19-45",
            excerpt="cover Allentown for $1850",
            start_offset=19,
            end_offset=44,
        )
        assessment = InquiryFieldAssessment.objects.create(
            inquiry=inquiry,
            field_name="rate",
            evidence_status="explicit",
            value_snapshot="1850",
        )
        assessment.evidence_links.create(evidence_span=span, relationship="supporting")
        quote = CarrierQuote.objects.create(
            inquiry=inquiry,
            amount=Decimal("1850"),
            quote_type="carrier_quote",
            rate_basis="all_in",
            evidence_status="explicit",
            is_current=True,
        )
        quote.evidence_spans.add(span)
        InquiryReviewReason.objects.create(
            inquiry=inquiry, code="weak_carrier_match", severity="review"
        )

        response = client.get(reverse("inquiry_review", args=[inquiry.pk]))

        html = response.content.decode()
        assert response.status_code == 200
        assert "Truck available Tuesday" in html
        assert "MC 884411 here." in html
        # The grounded span is highlighted inside the body, not merely listed.
        assert "<mark" in html
        assert "weak_carrier_match" in html
        assert "Approve" in html
        assert "Reject" in html

    def test_call_page_renders_transcript_and_audio(self, client, broker):
        snapshot = make_snapshot(is_active=True)
        event = make_communication_event(
            snapshot=snapshot, channel="call", stable_evidence_id="call:rev_test_001.wav"
        )
        recording = make_call_recording(event=event)
        make_ingestion_job(event=event, status=IngestionJob.Status.NEEDS_REVIEW)
        transcript = make_transcript(recording=recording, is_current=True)
        transcript.segments.create(
            sequence=1,
            start_seconds=Decimal("0.0"),
            end_seconds=Decimal("4.2"),
            text="Hey, this is Carlos over at Blue Ridge.",
            confidence=Decimal("0.95"),
        )
        transcript.segments.create(
            sequence=2,
            start_seconds=Decimal("4.2"),
            end_seconds=Decimal("9.0"),
            text="Calling about the, uh, Scranton load.",
            confidence=Decimal("0.41"),
        )
        inquiry = make_inquiry(event=event, review_status=Inquiry.ReviewStatus.NEEDS_REVIEW)

        response = client.get(reverse("inquiry_review", args=[inquiry.pk]))

        html = response.content.decode()
        assert response.status_code == 200
        assert "Carlos over at Blue Ridge" in html
        assert reverse("call_audio", args=[recording.pk]) in html
        assert "<audio" in html
        # The low-confidence segment is visibly flagged as uncertain.
        assert "uncertain" in html.lower()


class TestAudioEndpoint:
    def test_streams_stored_audio(self, client, broker, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        recording = make_call_recording(storage_key="calls/test/rev-audio.wav")
        default_storage.save("calls/test/rev-audio.wav", ContentFile(b"RIFFfakewav"))

        response = client.get(reverse("call_audio", args=[recording.pk]))

        assert response.status_code == 200
        assert response.headers["Content-Type"] == "audio/wav"
        assert b"".join(response.streaming_content) == b"RIFFfakewav"

    def test_requires_login(self, client):
        recording = make_call_recording()
        response = client.get(reverse("call_audio", args=[recording.pk]))
        assert response.status_code == 302


class TestReviewActions:
    def test_approve_posts_through(self, client, broker):
        _, _, _, inquiry = build_email_case()
        InquiryReviewReason.objects.create(
            inquiry=inquiry, code="ambiguous_rate", severity="review"
        )

        response = client.post(
            reverse("inquiry_action", args=[inquiry.pk, "approve"]), {"note": "looks right"}
        )

        assert response.status_code == 302
        inquiry.refresh_from_db()
        assert inquiry.review_status == Inquiry.ReviewStatus.APPROVED
        action = InquiryReviewAction.objects.get(inquiry=inquiry)
        assert action.actor_label == "broker@goodlanelogistics.com"
        assert action.reason == "looks right"

    def test_invalid_action_is_rejected_with_error(self, client, broker):
        _, _, _, inquiry = build_email_case(review_status=Inquiry.ReviewStatus.REJECTED)

        response = client.post(reverse("inquiry_action", args=[inquiry.pk, "approve"]), {})

        assert response.status_code == 302
        assert "error=" in response.url
        inquiry.refresh_from_db()
        assert inquiry.review_status == Inquiry.ReviewStatus.REJECTED

    def test_correct_carrier_via_post(self, client, broker):
        snapshot, _, _, inquiry = build_email_case()
        carrier = make_carrier(snapshot=snapshot, company_name="Keystone Freight Lines")

        response = client.post(
            reverse("inquiry_action", args=[inquiry.pk, "correct-carrier"]),
            {"carrier_id": str(carrier.pk)},
        )

        assert response.status_code == 302
        inquiry.refresh_from_db()
        assert inquiry.carrier == carrier
        assert inquiry.carrier_resolution_status == Inquiry.ResolutionStatus.VERIFIED

    def test_correct_load_by_external_id(self, client, broker):
        snapshot, _, _, inquiry = build_email_case()
        load = make_load(snapshot=snapshot, external_load_id="29372499")

        response = client.post(
            reverse("inquiry_action", args=[inquiry.pk, "correct-load"]),
            {"external_load_id": "29372499"},
        )

        assert response.status_code == 302
        inquiry.refresh_from_db()
        assert inquiry.load == load

    def test_correct_load_unknown_reference_reports_error(self, client, broker):
        _, _, _, inquiry = build_email_case()

        response = client.post(
            reverse("inquiry_action", args=[inquiry.pk, "correct-load"]),
            {"external_load_id": "00000000"},
        )

        assert response.status_code == 302
        assert "error=" in response.url
        inquiry.refresh_from_db()
        assert inquiry.load is None


class TestCarrierSearch:
    def test_search_lists_matching_carriers(self, client, broker):
        snapshot, _, _, inquiry = build_email_case()
        make_carrier(snapshot=snapshot, company_name="Keystone Freight Lines")
        make_carrier(snapshot=snapshot, company_name="Atlantic Carriers Inc")

        response = client.get(
            reverse("inquiry_review", args=[inquiry.pk]), {"carrier_q": "keystone"}
        )

        html = response.content.decode()
        assert "Keystone Freight Lines" in html
        assert "Atlantic Carriers Inc" not in html


class TestInboxNavigation:
    def test_inbox_rows_link_to_inquiry_review(self, client, broker):
        snapshot = make_snapshot(is_active=True)
        event = make_communication_event(snapshot=snapshot)
        make_email_content(event=event)
        make_ingestion_job(event=event, status=IngestionJob.Status.NEEDS_REVIEW)
        first = make_inquiry(event=event, review_status=Inquiry.ReviewStatus.UNREVIEWED)
        reviewable = make_inquiry(
            event=event, sequence_number=2, review_status=Inquiry.ReviewStatus.NEEDS_REVIEW
        )

        response = client.get(reverse("inbox"))

        html = response.content.decode()
        # A needs_review job navigates to its lowest reviewable inquiry, not
        # merely the lowest sequence number.
        assert reverse("inquiry_review", args=[reviewable.pk]) in html
        assert reverse("inquiry_review", args=[first.pk]) not in html


class TestReadability:
    def test_evidence_chips_carry_explanatory_tooltips(self, client, broker):
        _, event, email, inquiry = build_email_case()
        InquiryFieldAssessment.objects.create(
            inquiry=inquiry,
            field_name="rate",
            evidence_status="explicit",
            value_snapshot="1850",
        )
        InquiryFieldAssessment.objects.create(
            inquiry=inquiry,
            field_name="equipment",
            evidence_status="inferred",
            value_snapshot="box truck",
        )

        response = client.get(reverse("inquiry_review", args=[inquiry.pk]))

        html = response.content.decode()
        # Categorical confidence chips explain themselves on hover.
        assert 'title="Stated in the source' in html
        assert 'title="Proposed by the model' in html

    def test_matched_load_rows_link_to_the_load_workspace(self, client, broker):
        from apps.inquiries.models import InquiryLoadMatch
        from tests.factories import make_load

        snapshot, event, email, inquiry = build_email_case()
        load = make_load(snapshot=snapshot, external_load_id="29372460")
        InquiryLoadMatch.objects.create(
            inquiry=inquiry,
            load=load,
            match_tier="exact",
            primary_method="external_load_id",
            status="verified",
            is_selected=True,
            selection_source="deterministic",
        )

        response = client.get(reverse("inquiry_review", args=[inquiry.pk]))

        html = response.content.decode()
        assert reverse("load_workspace", args=["29372460"]) in html
