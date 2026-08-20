from django.contrib import admin

from apps.admin_base import ReadOnlyAdmin, pretty_json
from apps.inquiries.models import (
    CarrierQuote,
    EvidenceSpan,
    ExtractionRun,
    Inquiry,
    InquiryCarrierMatch,
    InquiryFieldAssessment,
    InquiryFieldEvidenceLink,
    InquiryIntent,
    InquiryLoadMatch,
    InquiryQuestion,
    InquiryReviewReason,
)


@admin.register(ExtractionRun)
class ExtractionRunAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "communication_event",
        "schema_version",
        "prompt_version",
        "prompt_source",
        "model",
        "validation_status",
        "is_current",
        "retry_generation",
    )
    list_filter = ("validation_status", "is_current", "prompt_source", "model")
    list_select_related = ("communication_event",)
    exclude = ("raw_output", "validated_output")
    readonly_fields = ("validated_output_pretty", "raw_output_pretty")

    @admin.display(description="Validated output")
    def validated_output_pretty(self, obj):
        return pretty_json(obj.validated_output)

    @admin.display(description="Raw output")
    def raw_output_pretty(self, obj):
        return pretty_json(obj.raw_output)


@admin.register(Inquiry)
class InquiryAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "communication_event",
        "sequence_number",
        "primary_intent",
        "availability_status",
        "carrier",
        "load",
        "carrier_resolution_status",
        "load_resolution_status",
        "review_status",
    )
    list_filter = (
        "review_status",
        "primary_intent",
        "carrier_resolution_status",
        "load_resolution_status",
    )
    search_fields = ("summary", "carrier__company_name", "load__external_load_id")
    list_select_related = ("communication_event", "carrier", "load")


@admin.register(InquiryIntent)
class InquiryIntentAdmin(ReadOnlyAdmin):
    list_display = ("inquiry", "intent", "is_primary")
    list_filter = ("intent",)
    list_select_related = ("inquiry",)


@admin.register(InquiryQuestion)
class InquiryQuestionAdmin(ReadOnlyAdmin):
    list_display = ("inquiry", "category", "question_text", "answer_status")
    list_filter = ("category", "answer_status")
    list_select_related = ("inquiry",)


@admin.register(EvidenceSpan)
class EvidenceSpanAdmin(ReadOnlyAdmin):
    list_display = (
        "stable_evidence_id",
        "source_part",
        "start_offset",
        "end_offset",
        "start_seconds",
        "end_seconds",
    )
    list_filter = ("source_part",)
    search_fields = ("stable_evidence_id", "excerpt")
    list_select_related = ("communication_event",)


@admin.register(InquiryFieldAssessment)
class InquiryFieldAssessmentAdmin(ReadOnlyAdmin):
    list_display = ("inquiry", "field_name", "evidence_status", "is_current")
    list_filter = ("field_name", "evidence_status", "is_current")
    list_select_related = ("inquiry",)


@admin.register(InquiryFieldEvidenceLink)
class InquiryFieldEvidenceLinkAdmin(ReadOnlyAdmin):
    list_display = ("assessment", "evidence_span", "relationship")
    list_select_related = ("assessment", "evidence_span")


@admin.register(CarrierQuote)
class CarrierQuoteAdmin(ReadOnlyAdmin):
    list_display = (
        "inquiry",
        "amount",
        "quote_type",
        "rate_basis",
        "evidence_status",
        "is_current",
    )
    list_filter = ("quote_type", "rate_basis", "is_current")
    list_select_related = ("inquiry",)


@admin.register(InquiryCarrierMatch)
class InquiryCarrierMatchAdmin(ReadOnlyAdmin):
    list_display = (
        "inquiry",
        "carrier",
        "match_tier",
        "primary_method",
        "status",
        "is_selected",
        "selection_source",
    )
    list_filter = ("match_tier", "primary_method", "status", "is_selected")
    list_select_related = ("inquiry", "carrier")


@admin.register(InquiryLoadMatch)
class InquiryLoadMatchAdmin(ReadOnlyAdmin):
    list_display = (
        "inquiry",
        "load",
        "match_tier",
        "primary_method",
        "status",
        "is_selected",
        "selection_source",
    )
    list_filter = ("match_tier", "primary_method", "status", "is_selected")
    list_select_related = ("inquiry", "load")


@admin.register(InquiryReviewReason)
class InquiryReviewReasonAdmin(ReadOnlyAdmin):
    list_display = ("inquiry", "code", "severity", "resolved_at", "created_at")
    list_filter = ("code", "severity")
    list_select_related = ("inquiry",)
