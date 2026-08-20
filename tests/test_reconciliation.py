"""Inquiry reconciliation and finalization (spec 3F): one atomic, idempotent transaction."""

from datetime import UTC, date, datetime
from unittest import mock

import pytest

from apps.candidates.models import CarrierLoadCandidate
from apps.comms.models import IngestionJob
from apps.inquiries.models import (
    CarrierQuote,
    EvidenceSpan,
    Inquiry,
    InquiryReviewReason,
)
from apps.inquiries.reconciliation import finalize_communication
from tests.factories import (
    make_carrier,
    make_communication_event,
    make_email_content,
    make_equipment,
    make_extraction_run,
    make_ingestion_job,
    make_load,
    make_snapshot,
)

pytestmark = pytest.mark.django_db

BODY = (
    "We can do the Philly to New York box truck Saturday. Not at $240. "
    "Our floor is $280. MC 712843. - Carlos, Blue Ridge Transport"
)


def valid_output(**overrides):
    proposal = {
        "carrier_name": "Blue Ridge Transport",
        "carrier_name_evidence": {"source_part": "body", "excerpt": "Blue Ridge Transport"},
        "mc_number": "712843",
        "mc_number_evidence": {"source_part": "body", "excerpt": "MC 712843"},
        "dot_number": None,
        "contact_email": None,
        "contact_phone": None,
        "load_reference": "29372450",
        "load_reference_evidence": {"source_part": "subject", "excerpt": "29372450"},
        "equipment": "Box Truck",
        "equipment_evidence": {"source_part": "body", "excerpt": "box truck"},
        "availability": "confirmed",
        "availability_evidence": {"source_part": "body", "excerpt": "We can do"},
        "intents": ["rate_negotiation", "availability"],
        "rates": [
            {
                "amount": "240",
                "currency": "USD",
                "role": "broker_rate_reference",
                "basis": "all_in",
                "evidence": {"source_part": "body", "excerpt": "$240"},
            },
            {
                "amount": "280",
                "currency": "USD",
                "role": "carrier_counteroffer",
                "basis": "all_in",
                "evidence": {"source_part": "body", "excerpt": "$280"},
            },
        ],
        "questions": [],
        "conditions": None,
        "conditions_evidence": None,
        "summary": "Counteroffer of $280 for load 29372450.",
    }
    proposal.update(overrides)
    return {"inquiries": [proposal]}


@pytest.fixture
def scenario():
    """Active snapshot with the Blue Ridge carrier, load 29372450, and a processing job."""
    snapshot = make_snapshot(is_active=True)
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
    box_truck = make_equipment("box_truck", "Box Truck")
    load = make_load(
        snapshot=snapshot,
        external_load_id="29372450",
        equipment_type=box_truck,
        pickup_date=date(2026, 5, 23),
    )
    event = make_communication_event(
        snapshot=snapshot, occurred_at=datetime(2026, 5, 18, 14, 0, tzinfo=UTC)
    )
    make_email_content(event=event, subject="Re: Load 29372450", body_text=BODY)
    job = make_ingestion_job(event=event, status="processing", retry_count=0)
    return snapshot, carrier, load, event, job


def run_finalize(event, job, output, generation=0):
    run = make_extraction_run(
        event=event,
        job=job,
        validated_output=output,
        validation_status="valid",
        is_current=True,
    )
    return finalize_communication(run, generation=generation)


class TestHappyPath:
    def test_exact_matches_produce_a_completed_verified_inquiry(self, scenario):
        snapshot, carrier, load, event, job = scenario
        status = run_finalize(event, job, valid_output())

        assert status == "completed"
        job.refresh_from_db()
        assert job.status == "completed"

        inquiry = Inquiry.objects.get(communication_event=event)
        assert inquiry.carrier_id == carrier.id
        assert inquiry.load_id == load.id
        assert inquiry.carrier_resolution_status == "verified"
        assert inquiry.load_resolution_status == "verified"
        assert inquiry.review_status == "unreviewed"
        assert inquiry.primary_intent == "rate_negotiation"
        assert inquiry.intents.count() == 2
        assert EvidenceSpan.objects.filter(communication_event=event).count() > 0

        match = inquiry.carrier_matches.get()
        assert match.match_tier == "exact"
        assert match.is_selected

    def test_quote_roles_and_same_communication_supersession(self, scenario):
        snapshot, carrier, load, event, job = scenario
        run_finalize(event, job, valid_output())

        quotes = CarrierQuote.objects.filter(inquiry__communication_event=event)
        broker_ref = quotes.get(quote_type="broker_rate_reference")
        counter = quotes.get(quote_type="carrier_counteroffer")
        assert broker_ref.is_current is False
        assert counter.is_current is True
        assert str(counter.amount) == "280.00" or str(counter.amount) == "280"

    def test_deterministic_engine_runs_inside_finalization(self, scenario):
        snapshot, carrier, load, event, job = scenario
        run_finalize(event, job, valid_output())

        candidate = CarrierLoadCandidate.objects.get(carrier=carrier, load=load)
        assert candidate.current_eligibility_assessment is not None
        assert candidate.current_eligibility_assessment.final_status == "eligible"
        assert candidate.current_quote is not None

    def test_finalization_is_idempotent(self, scenario):
        snapshot, carrier, load, event, job = scenario
        run = make_extraction_run(
            event=event,
            job=job,
            validated_output=valid_output(),
            validation_status="valid",
            is_current=True,
        )
        finalize_communication(run, generation=0)
        counts = (
            Inquiry.objects.count(),
            CarrierQuote.objects.count(),
            EvidenceSpan.objects.count(),
            CarrierLoadCandidate.objects.count(),
        )
        IngestionJob.objects.filter(id=job.id).update(status="processing")
        # A retry deliberately reuses the same current extraction (spec 3D).
        assert finalize_communication(run, generation=0) == "completed"
        assert counts == (
            Inquiry.objects.count(),
            CarrierQuote.objects.count(),
            EvidenceSpan.objects.count(),
            CarrierLoadCandidate.objects.count(),
        )


class TestReviewPaths:
    def test_weak_name_only_needs_review_without_selection(self, scenario):
        snapshot, carrier, load, event, job = scenario
        output = valid_output(
            carrier_name="Blue Ridge",
            mc_number=None,
            mc_number_evidence=None,
        )
        status = run_finalize(event, job, output)

        assert status == "needs_review"
        inquiry = Inquiry.objects.get(communication_event=event)
        assert inquiry.carrier_id is None
        assert inquiry.carrier_resolution_status == "needs_review"
        assert inquiry.carrier_matches.filter(is_selected=True).count() == 0
        assert inquiry.carrier_matches.filter(match_tier="weak").count() >= 1
        codes = set(inquiry.review_reasons.values_list("code", flat=True))
        assert "weak_carrier_match" in codes

    def test_conflicting_exact_signals_require_review(self, scenario):
        snapshot, carrier, load, event, job = scenario
        other = make_carrier(snapshot=snapshot, company_name="Ridgeline Transport LLC")
        from apps.freight.models import CarrierContact

        CarrierContact.objects.create(
            carrier=other,
            email_raw="carlos@ridgeline.example",
            email_normalized="carlos@ridgeline.example",
        )
        output = valid_output(contact_email="carlos@ridgeline.example")
        status = run_finalize(event, job, output)

        assert status == "needs_review"
        inquiry = Inquiry.objects.get(communication_event=event)
        assert inquiry.carrier_id is None
        codes = set(inquiry.review_reasons.values_list("code", flat=True))
        assert "conflicting_carrier_identity" in codes

    def test_unknown_load_reference_needs_review(self, scenario):
        snapshot, carrier, load, event, job = scenario
        output = valid_output(
            load_reference="99999999",
            load_reference_evidence={"source_part": "body", "excerpt": "box truck"},
        )
        status = run_finalize(event, job, output)
        assert status == "needs_review"
        inquiry = Inquiry.objects.get(communication_event=event)
        assert inquiry.load_id is None
        codes = set(inquiry.review_reasons.values_list("code", flat=True))
        assert "ambiguous_load_reference" in codes

    def test_metadata_conflict_is_informational_only(self, scenario):
        snapshot, carrier, load, event, job = scenario
        event.email_content.source_metadata = {"equipment_mentioned": "Refrigerated"}
        event.email_content.save()

        status = run_finalize(event, job, valid_output())
        assert status == "completed"  # warning, never a review queue entry
        reason = InquiryReviewReason.objects.get(code="metadata_content_conflict")
        assert reason.severity == "informational"


class TestFenceAndRollback:
    def test_stale_generation_writes_nothing_canonical(self, scenario):
        snapshot, carrier, load, event, job = scenario
        IngestionJob.objects.filter(id=job.id).update(retry_count=2)
        status = run_finalize(event, job, valid_output(), generation=0)

        assert status == "stale"
        assert Inquiry.objects.count() == 0
        job.refresh_from_db()
        assert job.status == "processing"

    def test_failure_rolls_back_the_whole_card(self, scenario):
        snapshot, carrier, load, event, job = scenario
        with mock.patch(
            "apps.inquiries.reconciliation.assess_candidate",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError):
                run_finalize(event, job, valid_output())

        assert Inquiry.objects.count() == 0
        assert EvidenceSpan.objects.count() == 0
        job.refresh_from_db()
        assert job.status == "processing"  # caller's failure handling takes over
