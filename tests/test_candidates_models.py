"""Constraint behavior for the Step 2D models (candidates, compliance, eligibility)."""

import pytest
from django.db import IntegrityError, transaction

from tests.factories import (
    make_candidate,
    make_compliance_assessment,
    make_eligibility_assessment,
    make_inquiry,
)

pytestmark = pytest.mark.django_db


def _rejects(fn):
    with pytest.raises(IntegrityError), transaction.atomic():
        fn()


class TestCarrierLoadCandidate:
    def test_carrier_load_pair_unique(self):
        candidate = make_candidate()
        _rejects(lambda: make_candidate(carrier=candidate.carrier, load=candidate.load))

    def test_current_assessment_pointers_start_null(self):
        candidate = make_candidate()
        assert candidate.current_quote is None
        assert candidate.current_compliance_assessment is None
        assert candidate.current_eligibility_assessment is None


class TestCandidateInquiry:
    def test_inquiry_linked_once_per_candidate(self):
        from apps.candidates.models import CandidateInquiry

        candidate = make_candidate()
        inquiry = make_inquiry(
            event=None,
        )
        CandidateInquiry.objects.create(
            candidate=candidate, inquiry=inquiry, relationship="supporting"
        )
        _rejects(
            lambda: CandidateInquiry.objects.create(
                candidate=candidate, inquiry=inquiry, relationship="conflicting"
            )
        )


class TestAssessments:
    def test_compliance_assessment_is_immutable_point_in_time(self):
        assessment = make_compliance_assessment()
        assert assessment.policy_version == "goodlane_demo_eligibility_v1"
        assert assessment.carrier_facts_snapshot != {}

    def test_eligibility_requires_its_compliance_assessment(self):
        assessment = make_eligibility_assessment()
        assert assessment.compliance_assessment is not None
        assert assessment.final_status in {
            "blocked",
            "needs_compliance_review",
            "needs_clarification",
            "needs_onboarding",
            "eligible",
        }

    def test_component_result_vocabulary_enforced_at_database(self):
        _rejects(lambda: make_compliance_assessment(authority_result="maybe"))

    def test_current_assessment_reference_is_protected_from_deletion(self):
        from django.db.models import ProtectedError

        candidate = make_candidate()
        assessment = make_compliance_assessment(candidate=candidate)
        candidate.current_compliance_assessment = assessment
        candidate.save()
        with pytest.raises(ProtectedError):
            assessment.delete()

    def test_assessment_reason_requires_component_and_code(self):
        from apps.candidates.models import CandidateAssessmentReason

        assessment = make_eligibility_assessment()
        reason = CandidateAssessmentReason.objects.create(
            eligibility_assessment=assessment,
            component="insurance",
            code="insurance_expired",
            severity="blocker",
            observed_value="2026-05-15",
            required_value=">= 2026-05-23",
        )
        assert reason.severity == "blocker"
