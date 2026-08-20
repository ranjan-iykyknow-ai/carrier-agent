"""Broker review actions (spec 2E): auditable, transactional, never rewriting evidence.

Every action writes an append-only InquiryReviewAction with before/after snapshots,
updates canonical resolution only, resolves the review reasons it addresses,
reconciles candidates, and recomputes the job's terminal review state.
"""

from datetime import date

import pytest

from apps.candidates.engine import derive_current_facts
from apps.candidates.models import CandidateInquiry, CarrierLoadCandidate, EligibilityAssessment
from apps.comms.models import IngestionJob
from apps.inquiries.models import (
    Inquiry,
    InquiryCarrierMatch,
    InquiryLoadMatch,
    InquiryReviewReason,
)
from apps.workspace.models import InquiryReviewAction
from apps.workspace.review import (
    ReviewError,
    approve_extraction,
    correct_carrier,
    correct_load,
    reject_extraction,
    reopen_review,
)
from tests.factories import (
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


def build_review_case(*, with_load=True, with_carrier=False, reason_codes=("weak_carrier_match",)):
    """One needs_review email inquiry with its job in needs_review."""
    snapshot = make_snapshot()
    equipment = make_equipment()
    event = make_communication_event(snapshot=snapshot)
    make_email_content(event=event)
    job = make_ingestion_job(event=event, status=IngestionJob.Status.NEEDS_REVIEW)
    load = (
        make_load(snapshot=snapshot, equipment_type=equipment, pickup_date=date(2026, 5, 28))
        if with_load
        else None
    )
    carrier = (
        make_carrier(snapshot=snapshot, authority_status="ACTIVE", onboarded=True)
        if with_carrier
        else None
    )
    inquiry = make_inquiry(
        event=event,
        review_status=Inquiry.ReviewStatus.NEEDS_REVIEW,
        load=load,
        load_resolution_status=(
            Inquiry.ResolutionStatus.VERIFIED if with_load else Inquiry.ResolutionStatus.UNMATCHED
        ),
        carrier=carrier,
        carrier_resolution_status=(
            Inquiry.ResolutionStatus.VERIFIED
            if with_carrier
            else Inquiry.ResolutionStatus.NEEDS_REVIEW
        ),
        availability_status=Inquiry.AvailabilityStatus.CONFIRMED,
    )
    for code in reason_codes:
        InquiryReviewReason.objects.create(
            inquiry=inquiry, code=code, severity=InquiryReviewReason.Severity.REVIEW
        )
    return snapshot, event, job, load, carrier, inquiry


class TestApprove:
    def test_approve_resolves_reasons_and_completes_job(self):
        _, _, job, _, _, inquiry = build_review_case()

        action = approve_extraction(inquiry, actor_label=ACTOR)

        inquiry.refresh_from_db()
        job.refresh_from_db()
        assert inquiry.review_status == Inquiry.ReviewStatus.APPROVED
        assert job.status == IngestionJob.Status.COMPLETED
        reason = inquiry.review_reasons.get()
        assert reason.resolved_at is not None
        assert action.action_type == InquiryReviewAction.ActionType.APPROVE_EXTRACTION
        assert action.actor_label == ACTOR
        assert action.before_snapshot["review_status"] == "needs_review"
        assert action.after_snapshot["review_status"] == "approved"

    def test_approve_rejected_inquiry_raises(self):
        _, _, _, _, _, inquiry = build_review_case()
        inquiry.review_status = Inquiry.ReviewStatus.REJECTED
        inquiry.save(update_fields=["review_status"])

        with pytest.raises(ReviewError):
            approve_extraction(inquiry, actor_label=ACTOR)
        assert not InquiryReviewAction.objects.exists()

    def test_approve_leaves_sibling_needs_review_job_flag(self):
        """The job stays needs_review while another inquiry on the event still needs review."""
        _, event, job, _, _, inquiry = build_review_case()
        make_inquiry(
            event=event, sequence_number=2, review_status=Inquiry.ReviewStatus.NEEDS_REVIEW
        )

        approve_extraction(inquiry, actor_label=ACTOR)

        job.refresh_from_db()
        assert job.status == IngestionJob.Status.NEEDS_REVIEW

    def test_review_action_never_touches_failed_job(self):
        _, _, job, _, _, inquiry = build_review_case()
        job.status = IngestionJob.Status.FAILED
        job.save(update_fields=["status"])

        approve_extraction(inquiry, actor_label=ACTOR)

        job.refresh_from_db()
        assert job.status == IngestionJob.Status.FAILED


class TestReject:
    def test_reject_marks_status_and_records_action(self):
        _, _, job, _, _, inquiry = build_review_case()

        action = reject_extraction(inquiry, actor_label=ACTOR, note="hallucinated load")

        inquiry.refresh_from_db()
        job.refresh_from_db()
        assert inquiry.review_status == Inquiry.ReviewStatus.REJECTED
        assert job.status == IngestionJob.Status.COMPLETED
        assert action.reason == "hallucinated load"

    def test_rejected_inquiry_is_excluded_from_candidate_facts(self):
        _, event, _, load, carrier, inquiry = build_review_case(with_load=True, with_carrier=True)
        candidate = CarrierLoadCandidate.objects.create(
            carrier=carrier,
            load=load,
            first_seen_at=event.occurred_at,
            last_activity_at=event.occurred_at,
        )
        CandidateInquiry.objects.create(
            candidate=candidate,
            inquiry=inquiry,
            relationship=CandidateInquiry.Relationship.SUPPORTING,
        )

        facts_before = derive_current_facts(candidate)
        assert facts_before.availability == Inquiry.AvailabilityStatus.CONFIRMED

        action = reject_extraction(inquiry, actor_label=ACTOR)

        facts_after = derive_current_facts(candidate)
        assert facts_after.availability is None
        # Rejection triggered a fresh deterministic reassessment of the candidate.
        candidate.refresh_from_db()
        latest = candidate.current_eligibility_assessment
        assert latest is not None
        assert latest.triggering_review_action_id == action.id


class TestCorrectCarrier:
    def test_correct_carrier_from_weak_proposal(self):
        snapshot, _, job, load, _, inquiry = build_review_case()
        carrier = make_carrier(snapshot=snapshot, authority_status="ACTIVE", onboarded=True)
        proposal = InquiryCarrierMatch.objects.create(
            inquiry=inquiry,
            carrier=carrier,
            match_tier="weak",
            primary_method="name_similarity",
        )

        action = correct_carrier(inquiry, carrier, actor_label=ACTOR)

        inquiry.refresh_from_db()
        job.refresh_from_db()
        proposal.refresh_from_db()
        assert inquiry.carrier == carrier
        assert inquiry.carrier_resolution_status == Inquiry.ResolutionStatus.VERIFIED
        assert proposal.status == InquiryCarrierMatch.Status.VERIFIED
        assert proposal.is_selected is True
        assert proposal.selection_source == InquiryCarrierMatch.SelectionSource.BROKER
        assert proposal.reviewed_at is not None
        # The addressed reason resolves; with nothing left, review completes.
        assert inquiry.review_reasons.get().resolved_at is not None
        assert inquiry.review_status == Inquiry.ReviewStatus.APPROVED
        assert job.status == IngestionJob.Status.COMPLETED
        assert action.new_carrier == carrier
        assert action.previous_carrier is None
        # Candidate created and deterministically assessed for (carrier, load).
        candidate = CarrierLoadCandidate.objects.get(carrier=carrier, load=load)
        assert candidate.candidate_inquiries.filter(inquiry=inquiry).exists()
        assert candidate.current_eligibility_assessment is not None
        assert candidate.current_eligibility_assessment.triggering_review_action_id == action.id

    def test_correct_carrier_replaces_previous_selection(self):
        snapshot, event, _, load, old_carrier, inquiry = build_review_case(with_carrier=True)
        old_match = InquiryCarrierMatch.objects.create(
            inquiry=inquiry,
            carrier=old_carrier,
            match_tier="exact",
            primary_method="email",
            status="verified",
            is_selected=True,
            selection_source="deterministic",
        )
        old_candidate = CarrierLoadCandidate.objects.create(
            carrier=old_carrier,
            load=load,
            first_seen_at=event.occurred_at,
            last_activity_at=event.occurred_at,
        )
        CandidateInquiry.objects.create(
            candidate=old_candidate,
            inquiry=inquiry,
            relationship=CandidateInquiry.Relationship.SUPPORTING,
        )
        new_carrier = make_carrier(
            snapshot=snapshot, company_name="Blue Ridge Transport LLC", authority_status="ACTIVE"
        )

        action = correct_carrier(inquiry, new_carrier, actor_label=ACTOR)

        inquiry.refresh_from_db()
        old_match.refresh_from_db()
        assert inquiry.carrier == new_carrier
        assert old_match.is_selected is False
        assert old_match.status == InquiryCarrierMatch.Status.REJECTED
        new_match = InquiryCarrierMatch.objects.get(inquiry=inquiry, carrier=new_carrier)
        assert new_match.is_selected is True
        assert new_match.primary_method == InquiryCarrierMatch.PrimaryMethod.BROKER_CONFIRMED
        assert action.previous_carrier == old_carrier
        assert action.new_carrier == new_carrier
        # The old aggregate no longer claims this inquiry and was reassessed.
        assert not old_candidate.candidate_inquiries.filter(inquiry=inquiry).exists()
        assert EligibilityAssessment.objects.filter(
            candidate=old_candidate, triggering_review_action=action
        ).exists()
        assert CarrierLoadCandidate.objects.filter(carrier=new_carrier, load=load).exists()

    def test_correct_carrier_across_snapshots_raises(self):
        _, _, _, _, _, inquiry = build_review_case()
        foreign = make_carrier(snapshot=make_snapshot())

        with pytest.raises(ReviewError):
            correct_carrier(inquiry, foreign, actor_label=ACTOR)
        inquiry.refresh_from_db()
        assert inquiry.carrier is None

    def test_correction_leaves_unrelated_reasons_pending(self):
        snapshot, _, job, _, _, inquiry = build_review_case(
            reason_codes=("weak_carrier_match", "ambiguous_rate")
        )
        carrier = make_carrier(snapshot=snapshot)

        correct_carrier(inquiry, carrier, actor_label=ACTOR)

        inquiry.refresh_from_db()
        job.refresh_from_db()
        assert inquiry.review_status == Inquiry.ReviewStatus.NEEDS_REVIEW
        assert job.status == IngestionJob.Status.NEEDS_REVIEW
        pending = inquiry.review_reasons.get(resolved_at__isnull=True)
        assert pending.code == "ambiguous_rate"


class TestCorrectLoad:
    def test_correct_load_resolves_stated_reference(self):
        snapshot, _, _, _, carrier, inquiry = build_review_case(
            with_load=False, with_carrier=True, reason_codes=("ambiguous_load_reference",)
        )
        load = make_load(snapshot=snapshot, external_load_id="29372460")

        action = correct_load(inquiry, load, actor_label=ACTOR)

        inquiry.refresh_from_db()
        assert inquiry.load == load
        assert inquiry.load_resolution_status == Inquiry.ResolutionStatus.VERIFIED
        match = InquiryLoadMatch.objects.get(inquiry=inquiry, load=load)
        assert match.primary_method == InquiryLoadMatch.PrimaryMethod.CORRECTED_REFERENCE
        assert match.is_selected is True
        assert match.selection_source == InquiryLoadMatch.SelectionSource.BROKER
        assert inquiry.review_status == Inquiry.ReviewStatus.APPROVED
        assert action.new_load == load
        # With carrier already verified, the candidate aggregate now exists.
        candidate = CarrierLoadCandidate.objects.get(carrier=carrier, load=load)
        assert candidate.current_eligibility_assessment is not None

    def test_correct_load_on_rejected_inquiry_raises(self):
        snapshot, _, _, _, _, inquiry = build_review_case(with_load=False)
        inquiry.review_status = Inquiry.ReviewStatus.REJECTED
        inquiry.save(update_fields=["review_status"])
        load = make_load(snapshot=snapshot)

        with pytest.raises(ReviewError):
            correct_load(inquiry, load, actor_label=ACTOR)


class TestReopen:
    def test_reopen_returns_to_needs_review(self):
        _, _, job, _, _, inquiry = build_review_case()
        approve_extraction(inquiry, actor_label=ACTOR)
        inquiry.refresh_from_db()

        action = reopen_review(inquiry, actor_label=ACTOR, note="second look")

        inquiry.refresh_from_db()
        job.refresh_from_db()
        assert inquiry.review_status == Inquiry.ReviewStatus.NEEDS_REVIEW
        assert job.status == IngestionJob.Status.NEEDS_REVIEW
        assert action.action_type == InquiryReviewAction.ActionType.REOPEN_REVIEW

    def test_reopen_unreviewed_inquiry_raises(self):
        _, _, _, _, _, inquiry = build_review_case()

        with pytest.raises(ReviewError):
            reopen_review(inquiry, actor_label=ACTOR)


class TestAudit:
    def test_every_action_appends_exactly_one_audit_row(self):
        snapshot, _, _, _, _, inquiry = build_review_case()
        carrier = make_carrier(snapshot=snapshot)

        correct_carrier(inquiry, carrier, actor_label=ACTOR)
        inquiry.refresh_from_db()
        reopen_review(inquiry, actor_label=ACTOR)
        inquiry.refresh_from_db()
        approve_extraction(inquiry, actor_label=ACTOR)

        actions = list(InquiryReviewAction.objects.filter(inquiry=inquiry).order_by("created_at"))
        assert [a.action_type for a in actions] == [
            InquiryReviewAction.ActionType.CORRECT_CARRIER,
            InquiryReviewAction.ActionType.REOPEN_REVIEW,
            InquiryReviewAction.ActionType.APPROVE_EXTRACTION,
        ]
