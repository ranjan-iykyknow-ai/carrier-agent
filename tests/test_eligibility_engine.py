"""Deterministic candidate assessment (spec 3G): immutable, versioned, explainable."""

from datetime import UTC, date, datetime

import pytest

from apps.candidates.engine import assess_candidate, derive_current_facts
from apps.candidates.models import ComplianceAssessment, EligibilityAssessment
from apps.inquiries.models import CarrierQuote
from tests.factories import (
    make_candidate,
    make_candidate_inquiry,
    make_carrier,
    make_communication_event,
    make_equipment,
    make_inquiry,
    make_load,
    make_snapshot,
)

pytestmark = pytest.mark.django_db


def build_candidate(
    *,
    authority="ACTIVE",
    safety="Satisfactory",
    insurance=date(2027, 1, 1),
    onboarded=True,
    load_equipment="box_truck",
):
    snapshot = make_snapshot()
    carrier = make_carrier(
        snapshot=snapshot,
        authority_status=authority,
        safety_rating=safety,
        insurance_expiry=insurance,
        onboarded=onboarded,
        reliability_score=None,
    )
    load = make_load(
        snapshot=snapshot,
        equipment_type=make_equipment(load_equipment, load_equipment.title()),
        pickup_date=date(2026, 5, 23),
    )
    return make_candidate(carrier=carrier, load=load)


def link_inquiry(
    candidate,
    *,
    availability="confirmed",
    equipment_code="box_truck",
    occurred_at=None,
    verified=True,
    relationship="supporting",
):
    event = make_communication_event(
        snapshot=candidate.load.dataset_snapshot,
        occurred_at=occurred_at or datetime(2026, 5, 18, 14, 0, tzinfo=UTC),
    )
    inquiry = make_inquiry(
        event=event,
        availability_status=availability,
        equipment_type=make_equipment(equipment_code, equipment_code.title())
        if equipment_code
        else None,
        carrier=candidate.carrier,
        load=candidate.load,
        carrier_resolution_status="verified" if verified else "needs_review",
        load_resolution_status="verified" if verified else "needs_review",
    )
    make_candidate_inquiry(candidate=candidate, inquiry=inquiry, relationship=relationship)
    return inquiry


class TestFinalStatusPrecedence:
    def test_expired_insurance_blocks(self):
        candidate = build_candidate(insurance=date(2026, 5, 15))
        link_inquiry(candidate)
        assessment = assess_candidate(candidate)

        assert assessment.final_status == "blocked"
        assert assessment.compliance_assessment.insurance_result == "fail"
        codes = {r.code for r in assessment.reasons.all()}
        assert "insurance_expired" in codes

    def test_conditional_authority_needs_compliance_review(self):
        candidate = build_candidate(authority="CONDITIONAL")
        link_inquiry(candidate)
        assessment = assess_candidate(candidate)
        assert assessment.final_status == "needs_compliance_review"

    def test_equipment_mismatch_blocks(self):
        candidate = build_candidate(load_equipment="refrigerated")
        link_inquiry(candidate, equipment_code="box_truck")
        assessment = assess_candidate(candidate)
        assert assessment.final_status == "blocked"
        assert {r.code for r in assessment.reasons.all()} >= {"equipment_mismatch"}

    def test_explicit_unavailability_blocks(self):
        candidate = build_candidate()
        link_inquiry(candidate, availability="unavailable")
        assert assess_candidate(candidate).final_status == "blocked"

    def test_conditional_availability_needs_clarification(self):
        candidate = build_candidate()
        link_inquiry(candidate, availability="conditional")
        assert assess_candidate(candidate).final_status == "needs_clarification"

    def test_not_onboarded_carrier_needs_onboarding(self):
        candidate = build_candidate(onboarded=False)
        link_inquiry(candidate)
        assessment = assess_candidate(candidate)
        assert assessment.final_status == "needs_onboarding"
        assert "carrier_not_onboarded" in {r.code for r in assessment.reasons.all()}

    def test_everything_passing_is_eligible(self):
        candidate = build_candidate()
        link_inquiry(candidate)
        assert assess_candidate(candidate).final_status == "eligible"

    def test_unverified_identity_needs_clarification(self):
        candidate = build_candidate()
        link_inquiry(candidate, verified=False)
        assert assess_candidate(candidate).final_status == "needs_clarification"

    def test_blocked_beats_compliance_review(self):
        candidate = build_candidate(authority="CONDITIONAL", insurance=date(2026, 5, 15))
        link_inquiry(candidate)
        assert assess_candidate(candidate).final_status == "blocked"


class TestImmutability:
    def test_reassessment_creates_new_rows_and_moves_pointers(self):
        candidate = build_candidate()
        link_inquiry(candidate)
        first = assess_candidate(candidate)

        candidate.carrier.insurance_expiry = date(2026, 5, 1)
        candidate.carrier.save()
        second = assess_candidate(candidate)

        assert first.id != second.id
        assert EligibilityAssessment.objects.count() == 2
        assert ComplianceAssessment.objects.count() == 2
        candidate.refresh_from_db()
        assert candidate.current_eligibility_assessment_id == second.id
        first.refresh_from_db()
        assert first.final_status == "eligible"  # earlier decision preserved

    def test_snapshot_records_the_facts_used(self):
        candidate = build_candidate(authority="CONDITIONAL")
        link_inquiry(candidate)
        assessment = assess_candidate(candidate)
        snapshot = assessment.compliance_assessment.carrier_facts_snapshot
        assert snapshot["authority_status"] == "CONDITIONAL"
        assert assessment.compliance_assessment.pickup_date_used == date(2026, 5, 23)


class TestCurrentFacts:
    def test_later_explicit_statement_supersedes(self):
        candidate = build_candidate()
        link_inquiry(
            candidate,
            availability="confirmed",
            occurred_at=datetime(2026, 5, 18, 9, 0, tzinfo=UTC),
        )
        link_inquiry(
            candidate,
            availability="unavailable",
            occurred_at=datetime(2026, 5, 20, 9, 0, tzinfo=UTC),
        )
        facts = derive_current_facts(candidate)
        assert facts.availability == "unavailable"
        assert facts.availability_conflict is False

    def test_omission_never_erases_an_explicit_statement(self):
        candidate = build_candidate()
        link_inquiry(
            candidate,
            availability="confirmed",
            occurred_at=datetime(2026, 5, 18, 9, 0, tzinfo=UTC),
        )
        link_inquiry(
            candidate,
            availability="not_stated",
            occurred_at=datetime(2026, 5, 20, 9, 0, tzinfo=UTC),
        )
        assert derive_current_facts(candidate).availability == "confirmed"

    def test_contradiction_without_chronology_is_a_conflict(self):
        candidate = build_candidate()
        link_inquiry(
            candidate,
            availability="confirmed",
            occurred_at=datetime(2026, 5, 18, 9, 0, tzinfo=UTC),
        )
        # A dataset call has no occurred_at: chronology is not comparable.
        event = make_communication_event(
            snapshot=candidate.load.dataset_snapshot, channel="call", occurred_at=None
        )
        inquiry = make_inquiry(
            event=event,
            availability_status="unavailable",
            carrier=candidate.carrier,
            load=candidate.load,
        )
        make_candidate_inquiry(candidate=candidate, inquiry=inquiry, relationship="conflicting")

        facts = derive_current_facts(candidate)
        assert facts.availability is None
        assert facts.availability_conflict is True
        link_inquiry(candidate)  # identity-verified supporting link
        assert assess_candidate(candidate).final_status == "needs_clarification"

    def test_conflicting_quotes_without_chronology_have_no_current_quote(self):
        candidate = build_candidate()
        first = link_inquiry(candidate, occurred_at=datetime(2026, 5, 18, 9, 0, tzinfo=UTC))
        event = make_communication_event(
            snapshot=candidate.load.dataset_snapshot, channel="call", occurred_at=None
        )
        second = make_inquiry(event=event, carrier=candidate.carrier, load=candidate.load)
        make_candidate_inquiry(candidate=candidate, inquiry=second, relationship="conflicting")
        for inquiry, amount in ((first, "400"), (second, "455")):
            CarrierQuote.objects.create(
                inquiry=inquiry,
                amount=amount,
                quote_type="carrier_quote",
                rate_basis="all_in",
                is_current=True,
                evidence_status="explicit",
            )

        facts = derive_current_facts(candidate)
        assert facts.current_quote is None
        assert facts.quote_conflict is True
