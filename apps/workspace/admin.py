from django.contrib import admin

from apps.admin_base import ReadOnlyAdmin, pretty_json
from apps.workspace.models import (
    AssistantCitation,
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    DraftEvidenceLink,
    DraftResponse,
    InquiryReviewAction,
    ToolExecution,
)


@admin.register(InquiryReviewAction)
class InquiryReviewActionAdmin(ReadOnlyAdmin):
    list_display = ("inquiry", "action_type", "actor_label", "created_at")
    list_filter = ("action_type",)
    search_fields = ("actor_label", "inquiry__summary")
    list_select_related = ("inquiry",)
    readonly_fields = ("before_pretty", "after_pretty")
    exclude = ("before_snapshot", "after_snapshot")

    @admin.display(description="Before")
    def before_pretty(self, obj):
        return pretty_json(obj.before_snapshot)

    @admin.display(description="After")
    def after_pretty(self, obj):
        return pretty_json(obj.after_snapshot)


@admin.register(DraftResponse)
class DraftResponseAdmin(ReadOnlyAdmin):
    list_display = (
        "inquiry",
        "draft_type",
        "status",
        "carrier",
        "load",
        "model",
        "prompt_version",
        "created_by",
        "created_at",
    )
    list_filter = ("draft_type", "status")
    list_select_related = ("inquiry", "carrier", "load")
    readonly_fields = ("context_pretty",)
    exclude = ("context_snapshot",)

    @admin.display(description="Context snapshot")
    def context_pretty(self, obj):
        return pretty_json(obj.context_snapshot)


@admin.register(DraftEvidenceLink)
class DraftEvidenceLinkAdmin(ReadOnlyAdmin):
    list_display = ("draft", "fact_name", "source_type", "stable_source_id")
    list_filter = ("source_type",)
    list_select_related = ("draft",)


@admin.register(AssistantConversation)
class AssistantConversationAdmin(ReadOnlyAdmin):
    list_display = ("title", "actor_label", "scope", "load", "status", "created_at")
    list_filter = ("scope", "status")
    list_select_related = ("load",)


@admin.register(AssistantMessage)
class AssistantMessageAdmin(ReadOnlyAdmin):
    list_display = ("conversation", "sequence", "role", "status", "created_at")
    list_filter = ("role", "status")
    list_select_related = ("conversation",)


@admin.register(AssistantRun)
class AssistantRunAdmin(ReadOnlyAdmin):
    list_display = (
        "conversation",
        "status",
        "model",
        "prompt_version",
        "error_code",
        "langfuse_trace_id",
        "completed_at",
    )
    list_filter = ("status", "error_code")
    search_fields = ("langfuse_trace_id",)
    list_select_related = ("conversation",)


@admin.register(ToolExecution)
class ToolExecutionAdmin(ReadOnlyAdmin):
    list_display = ("assistant_run", "sequence", "tool_name", "status", "error_code")
    list_filter = ("tool_name", "status")
    list_select_related = ("assistant_run",)
    readonly_fields = ("arguments_pretty",)
    exclude = ("arguments",)

    @admin.display(description="Arguments")
    def arguments_pretty(self, obj):
        return pretty_json(obj.arguments)


@admin.register(AssistantCitation)
class AssistantCitationAdmin(ReadOnlyAdmin):
    list_display = ("assistant_message", "sequence", "source_type", "stable_source_id")
    list_filter = ("source_type",)
    list_select_related = ("assistant_message",)
