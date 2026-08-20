"""Step 2D — candidate aggregation, deterministic compliance, and eligibility.

Compliance and eligibility are deterministic, versioned application decisions
(policy code such as goodlane_demo_eligibility_v1). Assessments are immutable
point-in-time records carrying the fact snapshot they evaluated; reassessment
creates a new row. No is_best_rate / is_best_carrier / weighted-score column
exists anywhere — best rate and strongest candidate are computed at read time.
"""

from django.db import models
from django.db.models import Q

from apps.base import AppendOnlyModel, TimeStampedModel
from apps.freight.models import Carrier, Load
from apps.inquiries.models import CarrierQuote, Inquiry


class ComponentResult(models.TextChoices):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class CarrierLoadCandidate(TimeStampedModel):
    """One known carrier being considered for one load — the aggregate history."""

    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, related_name="load_candidacies")
    load = models.ForeignKey(Load, on_delete=models.PROTECT, related_name="carrier_candidacies")
    current_quote = models.ForeignKey(
        CarrierQuote,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="current_for_candidates",
    )
    current_compliance_assessment = models.ForeignKey(
        "ComplianceAssessment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="current_for_candidates",
    )
    current_eligibility_assessment = models.ForeignKey(
        "EligibilityAssessment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="current_for_candidates",
    )
    first_seen_at = models.DateTimeField()
    last_activity_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["carrier", "load"],
                name="candidates_carrier_load_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.carrier} for {self.load}"


class CandidateInquiry(TimeStampedModel):
    """Links every relevant inquiry to the aggregate candidate history.

    Mutable by design: the supporting/superseding/conflicting relationship is
    re-labeled when chronology or a broker review resolves a conflict (spec 2D).
    """

    class Relationship(models.TextChoices):
        SUPPORTING = "supporting"
        SUPERSEDING = "superseding"
        CONFLICTING = "conflicting"

    candidate = models.ForeignKey(
        CarrierLoadCandidate, on_delete=models.CASCADE, related_name="candidate_inquiries"
    )
    inquiry = models.ForeignKey(Inquiry, on_delete=models.PROTECT, related_name="candidate_links")
    relationship = models.CharField(max_length=15, choices=Relationship.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["candidate", "inquiry"],
                name="candidates_inquiry_link_uniq",
            ),
        ]
        verbose_name_plural = "candidate inquiries"


class ComplianceAssessment(AppendOnlyModel):
    """Immutable point-in-time compliance decision under a versioned code policy."""

    class OverallResult(models.TextChoices):
        PASS = "pass"
        FAIL = "fail"
        NEEDS_REVIEW = "needs_review"

    candidate = models.ForeignKey(
        CarrierLoadCandidate, on_delete=models.CASCADE, related_name="compliance_assessments"
    )
    policy_version = models.CharField(max_length=100)
    pickup_date_used = models.DateField()
    authority_result = models.CharField(max_length=15, choices=ComponentResult.choices)
    safety_result = models.CharField(max_length=15, choices=ComponentResult.choices)
    insurance_result = models.CharField(max_length=15, choices=ComponentResult.choices)
    overall_result = models.CharField(max_length=15, choices=OverallResult.choices)
    carrier_facts_snapshot = models.JSONField(default=dict, blank=True)
    evaluated_at = models.DateTimeField()
    triggering_inquiry = models.ForeignKey(
        Inquiry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="triggered_compliance_assessments",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(authority_result__in=ComponentResult.values)
                & Q(safety_result__in=ComponentResult.values)
                & Q(insurance_result__in=ComponentResult.values)
                & Q(overall_result__in=["pass", "fail", "needs_review"]),
                name="candidates_compliance_vocab",
            ),
        ]

    def __str__(self):
        return f"Compliance {self.overall_result} ({self.policy_version})"


class EligibilityAssessment(AppendOnlyModel):
    """Immutable combination of compliance with identity/equipment/availability/onboarding."""

    class FinalStatus(models.TextChoices):
        BLOCKED = "blocked"
        NEEDS_COMPLIANCE_REVIEW = "needs_compliance_review"
        NEEDS_CLARIFICATION = "needs_clarification"
        NEEDS_ONBOARDING = "needs_onboarding"
        ELIGIBLE = "eligible"

    candidate = models.ForeignKey(
        CarrierLoadCandidate, on_delete=models.CASCADE, related_name="eligibility_assessments"
    )
    compliance_assessment = models.ForeignKey(
        ComplianceAssessment, on_delete=models.PROTECT, related_name="eligibility_assessments"
    )
    policy_version = models.CharField(max_length=100)
    identity_result = models.CharField(max_length=15, choices=ComponentResult.choices)
    equipment_result = models.CharField(max_length=15, choices=ComponentResult.choices)
    availability_result = models.CharField(max_length=15, choices=ComponentResult.choices)
    onboarding_result = models.CharField(max_length=15, choices=ComponentResult.choices)
    final_status = models.CharField(max_length=25, choices=FinalStatus.choices)
    input_snapshot = models.JSONField(default=dict, blank=True)
    evaluated_at = models.DateTimeField()
    triggering_inquiry = models.ForeignKey(
        Inquiry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="triggered_eligibility_assessments",
    )
    triggering_review_action = models.ForeignKey(
        "workspace.InquiryReviewAction",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="triggered_eligibility_assessments",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(identity_result__in=ComponentResult.values)
                & Q(equipment_result__in=ComponentResult.values)
                & Q(availability_result__in=ComponentResult.values)
                & Q(onboarding_result__in=ComponentResult.values)
                & Q(
                    final_status__in=[
                        "blocked",
                        "needs_compliance_review",
                        "needs_clarification",
                        "needs_onboarding",
                        "eligible",
                    ]
                ),
                name="candidates_eligibility_vocab",
            ),
        ]

    def __str__(self):
        return f"Eligibility {self.final_status} ({self.policy_version})"


class CandidateAssessmentReason(AppendOnlyModel):
    class Component(models.TextChoices):
        AUTHORITY = "authority"
        SAFETY = "safety"
        INSURANCE = "insurance"
        IDENTITY = "identity"
        EQUIPMENT = "equipment"
        AVAILABILITY = "availability"
        ONBOARDING = "onboarding"
        QUOTE = "quote"

    class Code(models.TextChoices):
        AUTHORITY_INACTIVE = "authority_inactive"
        AUTHORITY_UNKNOWN = "authority_unknown"
        SAFETY_UNACCEPTABLE = "safety_unacceptable"
        SAFETY_UNKNOWN = "safety_unknown"
        INSURANCE_EXPIRED = "insurance_expired"
        INSURANCE_EXPIRY_UNKNOWN = "insurance_expiry_unknown"
        EQUIPMENT_MISMATCH = "equipment_mismatch"
        EQUIPMENT_UNKNOWN = "equipment_unknown"
        EQUIPMENT_CONFLICTING = "equipment_conflicting"
        CARRIER_UNAVAILABLE = "carrier_unavailable"
        AVAILABILITY_UNKNOWN = "availability_unknown"
        AVAILABILITY_CONDITIONAL = "availability_conditional"
        AVAILABILITY_CONFLICTING = "availability_conflicting"
        CARRIER_NOT_ONBOARDED = "carrier_not_onboarded"
        QUOTE_MISSING = "quote_missing"
        QUOTE_AMBIGUOUS = "quote_ambiguous"

    class Severity(models.TextChoices):
        BLOCKER = "blocker"
        REVIEW = "review"
        WARNING = "warning"
        INFORMATION = "information"

    eligibility_assessment = models.ForeignKey(
        EligibilityAssessment, on_delete=models.CASCADE, related_name="reasons"
    )
    component = models.CharField(max_length=15, choices=Component.choices)
    code = models.CharField(max_length=30, choices=Code.choices)
    severity = models.CharField(max_length=15, choices=Severity.choices)
    observed_value = models.CharField(max_length=200, null=True, blank=True)
    required_value = models.CharField(max_length=200, null=True, blank=True)
    stable_evidence_id = models.CharField(max_length=300, null=True, blank=True)
    details = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.code} ({self.severity})"
