from django.contrib import admin

from apps.admin_base import ReadOnlyAdmin, pretty_json
from apps.candidates.models import (
    CandidateAssessmentReason,
    CandidateInquiry,
    CarrierLoadCandidate,
    ComplianceAssessment,
    EligibilityAssessment,
)


@admin.register(CarrierLoadCandidate)
class CarrierLoadCandidateAdmin(ReadOnlyAdmin):
    list_display = ("carrier", "load", "current_status", "first_seen_at", "last_activity_at")
    search_fields = ("carrier__company_name", "load__external_load_id")
    list_select_related = ("carrier", "load", "current_eligibility_assessment")

    @admin.display(description="Current status")
    def current_status(self, obj):
        assessment = obj.current_eligibility_assessment
        return assessment.final_status if assessment else "unassessed"


@admin.register(CandidateInquiry)
class CandidateInquiryAdmin(ReadOnlyAdmin):
    list_display = ("candidate", "inquiry", "relationship")
    list_filter = ("relationship",)
    list_select_related = ("candidate", "inquiry")


@admin.register(ComplianceAssessment)
class ComplianceAssessmentAdmin(ReadOnlyAdmin):
    list_display = (
        "candidate",
        "overall_result",
        "authority_result",
        "safety_result",
        "insurance_result",
        "policy_version",
        "evaluated_at",
    )
    list_filter = ("overall_result", "policy_version")
    list_select_related = ("candidate",)
    readonly_fields = ("facts_pretty",)

    @admin.display(description="Carrier facts snapshot")
    def facts_pretty(self, obj):
        return pretty_json(obj.carrier_facts_snapshot)


@admin.register(EligibilityAssessment)
class EligibilityAssessmentAdmin(ReadOnlyAdmin):
    list_display = (
        "candidate",
        "final_status",
        "identity_result",
        "equipment_result",
        "availability_result",
        "onboarding_result",
        "policy_version",
        "evaluated_at",
    )
    list_filter = ("final_status", "policy_version")
    list_select_related = ("candidate",)
    readonly_fields = ("input_pretty",)

    @admin.display(description="Input snapshot")
    def input_pretty(self, obj):
        return pretty_json(obj.input_snapshot)


@admin.register(CandidateAssessmentReason)
class CandidateAssessmentReasonAdmin(ReadOnlyAdmin):
    list_display = ("eligibility_assessment", "component", "code", "severity", "observed_value")
    list_filter = ("component", "code", "severity")
    list_select_related = ("eligibility_assessment",)
