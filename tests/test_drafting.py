"""Response drafting (spec 3H): grounded, auditable, never sends anything."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from apps.aiops.models import AIOperation
from apps.candidates.engine import assess_candidate
from apps.candidates.models import CandidateInquiry
from apps.inquiries.models import CarrierQuote
from apps.workspace.drafting import DraftError, build_draft_context, generate_draft
from apps.workspace.models import DraftResponse
from tests.factories import (
    make_candidate,
    make_carrier,
    make_communication_event,
    make_email_content,
    make_equipment,
    make_ingestion_job,
    make_inquiry,
    make_load,
    make_snapshot,
)

pytestmark = pytest.mark.django_db

ACTOR = "broker@goodlanelogistics.com"


class FakeOpenAI:
    def __init__(self, payload=None, error=None):
        self.error = error
        self.requests = []
        payload = payload or {
            "subject": "Re: Load 29372450",
            "body": "Thanks for confirming availability. Our offered rate is $420 all-in.",
        }
        message = SimpleNamespace(content=json.dumps(payload))
        self._response = SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=400, completion_tokens=80),
            id="resp-draft-1",
        )

        def create(**kwargs):
            self.requests.append(kwargs)
            if self.error:
                raise self.error
            return self._response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def build_case(*, blocked=False, quote_amount="395"):
    snapshot = make_snapshot(is_active=True)
    equipment = make_equipment()
    load = make_load(
        snapshot=snapshot,
        external_load_id="29372450",
        equipment_type=equipment,
        offered_rate_usd=Decimal("420"),
        pickup_date=date(2026, 5, 23),
        internal_notes="shipper is flexible, do not reveal",
    )
    carrier = make_carrier(
        snapshot=snapshot,
        company_name="Blue Ridge Transport LLC",
        onboarded=True,
        authority_status="REVOKED" if blocked else "ACTIVE",
        safety_rating="Satisfactory",
        insurance_expiry=date(2027, 1, 1),
        notes="privately negotiated last year",
    )
    event = make_communication_event(
        snapshot=snapshot, occurred_at=datetime(2026, 5, 18, 14, 0, tzinfo=UTC)
    )
    make_email_content(event=event)
    make_ingestion_job(event=event, status="completed")
    inquiry = make_inquiry(
        event=event,
        carrier=carrier,
        load=load,
        carrier_resolution_status="verified",
        load_resolution_status="verified",
        availability_status="confirmed",
        equipment_type=equipment,
    )
    quote = None
    if quote_amount:
        quote = CarrierQuote.objects.create(
            inquiry=inquiry,
            amount=Decimal(quote_amount),
            quote_type="carrier_quote",
            rate_basis="all_in",
            evidence_status="explicit",
            is_current=True,
        )
    candidate = make_candidate(carrier=carrier, load=load)
    CandidateInquiry.objects.create(candidate=candidate, inquiry=inquiry, relationship="supporting")
    assess_candidate(candidate, triggering_inquiry=inquiry)
    return inquiry, candidate, quote


class TestDraftContext:
    def test_context_carries_facts_and_blockers_but_never_private_notes(self):
        inquiry, candidate, quote = build_case(blocked=True)

        context = build_draft_context(inquiry, DraftResponse.DraftType.PROVIDE_RATE)

        rendered = json.dumps(context)
        assert "29372450" in rendered
        assert "Blue Ridge Transport LLC" in rendered
        assert "blocked" in rendered
        assert "authority_inactive" in rendered
        # Internal notes are never draft material.
        assert "do not reveal" not in rendered
        assert "privately negotiated" not in rendered

    def test_context_flags_missing_information(self):
        inquiry, _, _ = build_case(quote_amount=None)
        from apps.inquiries.models import InquiryFieldAssessment

        InquiryFieldAssessment.objects.create(
            inquiry=inquiry, field_name="rate", evidence_status="missing", is_current=True
        )

        context = build_draft_context(inquiry, DraftResponse.DraftType.REQUEST_INFORMATION)

        assert "rate" in context["missing_information"]


class TestGenerateDraft:
    def test_generates_persists_and_links_evidence(self):
        inquiry, candidate, quote = build_case()
        fake = FakeOpenAI()

        draft = generate_draft(
            inquiry,
            DraftResponse.DraftType.PROVIDE_RATE,
            actor_label=ACTOR,
            client=fake,
        )

        assert draft.status == DraftResponse.Status.GENERATED
        assert draft.generated_body == draft.current_body
        assert draft.generated_subject == "Re: Load 29372450"
        assert draft.created_by == ACTOR
        assert draft.load == inquiry.load
        assert draft.carrier == inquiry.carrier
        assert draft.candidate == candidate
        assert draft.context_snapshot["load"]["external_load_id"] == "29372450"
        linked = {link.fact_name: link.stable_source_id for link in draft.evidence_links.all()}
        assert linked["load"] == "load:29372450"
        assert linked["current_quote"] == f"quote:{quote.id}"
        assert "eligibility_assessment" in linked
        # Accounting: one draft AIOperation with cost, linked to the draft.
        operation = draft.ai_operation
        assert operation.operation_type == AIOperation.OperationType.DRAFT
        assert operation.usage_category == AIOperation.UsageCategory.DRAFTING
        assert operation.estimated_cost is not None
        assert draft.model
        assert draft.prompt_version

    def test_provider_failure_is_visible_and_leaves_no_draft(self):
        import openai

        inquiry, _, _ = build_case()
        request = SimpleNamespace(method="POST", url="x")
        fake = FakeOpenAI(error=openai.APITimeoutError(request=request))

        with pytest.raises(DraftError):
            generate_draft(
                inquiry,
                DraftResponse.DraftType.PROVIDE_RATE,
                actor_label=ACTOR,
                client=fake,
            )

        assert DraftResponse.objects.count() == 0
        operation = AIOperation.objects.get(operation_type="draft")
        assert operation.status == AIOperation.Status.FAILED

    def test_rejected_inquiry_cannot_be_drafted(self):
        inquiry, _, _ = build_case()
        inquiry.review_status = "rejected"
        inquiry.save(update_fields=["review_status"])

        with pytest.raises(DraftError):
            generate_draft(
                inquiry,
                DraftResponse.DraftType.PROVIDE_RATE,
                actor_label=ACTOR,
                client=FakeOpenAI(),
            )


class TestDraftLifecycle:
    @pytest.fixture
    def broker(self, client):
        user = User.objects.create_user(ACTOR, password="x")
        client.force_login(user)
        return user

    def test_generate_edit_copy_discard_through_views(self, client, broker):
        inquiry, _, _ = build_case()
        fake = FakeOpenAI()
        from apps.workspace import drafting

        original_factory = drafting.default_client
        drafting.default_client = lambda: fake
        try:
            response = client.post(
                reverse("draft_generate", args=[inquiry.pk]),
                {"draft_type": "provide_rate"},
            )
        finally:
            drafting.default_client = original_factory

        assert response.status_code == 302
        draft = DraftResponse.objects.get()

        response = client.post(
            reverse("draft_action", args=[draft.pk, "edit"]),
            {"subject": "Re: Load 29372450 — updated", "body": "Edited body."},
        )
        draft.refresh_from_db()
        assert draft.status == DraftResponse.Status.EDITED
        assert draft.current_body == "Edited body."
        assert draft.generated_body != "Edited body."  # generation preserved

        client.post(reverse("draft_action", args=[draft.pk, "copy"]))
        draft.refresh_from_db()
        assert draft.status == DraftResponse.Status.COPIED
        assert draft.copied_at is not None

        client.post(reverse("draft_action", args=[draft.pk, "discard"]))
        draft.refresh_from_db()
        assert draft.status == DraftResponse.Status.DISCARDED

    def test_review_page_offers_draft_types(self, client, broker):
        inquiry, _, _ = build_case()
        response = client.get(reverse("inquiry_review", args=[inquiry.pk]))
        html = response.content.decode()
        assert "draft_type" in html
        assert "provide_rate" in html
