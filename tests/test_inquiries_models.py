"""Constraint behavior for the Step 2C models (extraction, inquiry, evidence, quotes, matches)."""

import pytest
from django.db import IntegrityError, transaction

from tests.factories import (
    make_carrier,
    make_evidence_span,
    make_extraction_run,
    make_inquiry,
    make_load,
)

pytestmark = pytest.mark.django_db


def _rejects(fn):
    with pytest.raises(IntegrityError), transaction.atomic():
        fn()


class TestExtractionRun:
    def test_only_one_current_extraction_per_communication(self):
        run = make_extraction_run(is_current=True)
        _rejects(lambda: make_extraction_run(event=run.communication_event, is_current=True))

    def test_failed_and_superseded_runs_are_preserved(self):
        run = make_extraction_run(is_current=True)
        make_extraction_run(event=run.communication_event, is_current=False)
        make_extraction_run(
            event=run.communication_event, is_current=False, validation_status="invalid"
        )


class TestInquiry:
    def test_sequence_unique_per_communication(self):
        inquiry = make_inquiry(sequence_number=1)
        _rejects(lambda: make_inquiry(event=inquiry.communication_event, sequence_number=1))

    def test_multiple_inquiries_per_communication_by_sequence(self):
        inquiry = make_inquiry(sequence_number=1)
        make_inquiry(event=inquiry.communication_event, sequence_number=2)

    def test_unresolved_entities_stay_null(self):
        inquiry = make_inquiry()
        assert inquiry.carrier is None
        assert inquiry.load is None
        assert inquiry.review_status == "unreviewed"


class TestInquiryIntent:
    def test_one_primary_intent_per_inquiry(self):
        from apps.inquiries.models import InquiryIntent

        inquiry = make_inquiry()
        InquiryIntent.objects.create(inquiry=inquiry, intent="availability", is_primary=True)
        _rejects(
            lambda: InquiryIntent.objects.create(
                inquiry=inquiry, intent="rate_quote", is_primary=True
            )
        )

    def test_intent_not_duplicated_on_inquiry(self):
        from apps.inquiries.models import InquiryIntent

        inquiry = make_inquiry()
        InquiryIntent.objects.create(inquiry=inquiry, intent="availability")
        _rejects(lambda: InquiryIntent.objects.create(inquiry=inquiry, intent="availability"))


class TestEvidenceSpan:
    def test_email_span_uses_offsets(self):
        span = make_evidence_span(start_offset=0, end_offset=43)
        assert span.start_seconds is None

    def test_span_cannot_mix_offsets_and_seconds(self):
        _rejects(
            lambda: make_evidence_span(
                start_offset=0, end_offset=10, start_seconds="14.2", end_seconds="22.8"
            )
        )


class TestInquiryFieldAssessment:
    def test_one_current_assessment_per_field(self):
        from apps.inquiries.models import InquiryFieldAssessment

        inquiry = make_inquiry()
        InquiryFieldAssessment.objects.create(
            inquiry=inquiry,
            field_name="rate",
            evidence_status="explicit",
            is_current=True,
        )
        _rejects(
            lambda: InquiryFieldAssessment.objects.create(
                inquiry=inquiry,
                field_name="rate",
                evidence_status="conflicting",
                is_current=True,
            )
        )


class TestCarrierQuote:
    def test_amount_must_be_non_negative(self):
        from apps.inquiries.models import CarrierQuote

        inquiry = make_inquiry()
        _rejects(
            lambda: CarrierQuote.objects.create(
                inquiry=inquiry,
                amount="-1.00",
                quote_type="carrier_quote",
                rate_basis="all_in",
                evidence_status="explicit",
            )
        )


class TestMatches:
    def test_only_one_selected_carrier_candidate_per_inquiry(self):
        from apps.inquiries.models import InquiryCarrierMatch

        inquiry = make_inquiry()
        snapshot = inquiry.communication_event.dataset_snapshot
        first = make_carrier(snapshot=snapshot)
        second = make_carrier(snapshot=snapshot)
        InquiryCarrierMatch.objects.create(
            inquiry=inquiry,
            carrier=first,
            match_tier="exact",
            primary_method="mc_number",
            is_selected=True,
        )
        _rejects(
            lambda: InquiryCarrierMatch.objects.create(
                inquiry=inquiry,
                carrier=second,
                match_tier="strong",
                primary_method="email",
                is_selected=True,
            )
        )

    def test_candidate_carrier_listed_once_per_inquiry(self):
        from apps.inquiries.models import InquiryCarrierMatch

        inquiry = make_inquiry()
        carrier = make_carrier(snapshot=inquiry.communication_event.dataset_snapshot)
        InquiryCarrierMatch.objects.create(
            inquiry=inquiry, carrier=carrier, match_tier="weak", primary_method="name_similarity"
        )
        _rejects(
            lambda: InquiryCarrierMatch.objects.create(
                inquiry=inquiry,
                carrier=carrier,
                match_tier="weak",
                primary_method="name_similarity",
            )
        )

    def test_only_one_selected_load_candidate_per_inquiry(self):
        from apps.inquiries.models import InquiryLoadMatch

        inquiry = make_inquiry()
        snapshot = inquiry.communication_event.dataset_snapshot
        first = make_load(snapshot=snapshot)
        second = make_load(snapshot=snapshot)
        InquiryLoadMatch.objects.create(
            inquiry=inquiry,
            load=first,
            match_tier="exact",
            primary_method="external_load_id",
            is_selected=True,
        )
        _rejects(
            lambda: InquiryLoadMatch.objects.create(
                inquiry=inquiry,
                load=second,
                match_tier="strong",
                primary_method="corrected_reference",
                is_selected=True,
            )
        )


class TestInquiryReviewReason:
    def test_reason_severity_defaults_to_review(self):
        from apps.inquiries.models import InquiryReviewReason

        inquiry = make_inquiry()
        reason = InquiryReviewReason.objects.create(inquiry=inquiry, code="weak_carrier_match")
        assert reason.severity == "review"
        assert reason.resolved_at is None
