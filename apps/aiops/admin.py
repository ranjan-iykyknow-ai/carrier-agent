from django.contrib import admin

from apps.admin_base import ReadOnlyAdmin
from apps.aiops.models import (
    AIOperation,
    AIProviderCall,
    EvaluationCaseResult,
    EvaluationRun,
    EvaluationScore,
)


@admin.register(AIOperation)
class AIOperationAdmin(ReadOnlyAdmin):
    list_display = (
        "operation_type",
        "usage_category",
        "status",
        "provider_call_count",
        "total_tokens",
        "estimated_cost",
        "latency_ms",
        "correlation_id",
        "completed_at",
    )
    list_filter = ("operation_type", "usage_category", "status")
    search_fields = ("correlation_id",)
    ordering = ("-created_at",)


@admin.register(AIProviderCall)
class AIProviderCallAdmin(ReadOnlyAdmin):
    list_display = (
        "provider",
        "operation_name",
        "model",
        "status",
        "attempt_number",
        "prompt_version",
        "prompt_source",
        "estimated_cost",
        "latency_ms",
        "langfuse_trace_id",
    )
    list_filter = ("provider", "status", "prompt_source", "model")
    search_fields = ("langfuse_trace_id", "provider_request_id")
    list_select_related = ("operation",)
    ordering = ("-created_at",)


@admin.register(EvaluationRun)
class EvaluationRunAdmin(ReadOnlyAdmin):
    list_display = (
        "name",
        "status",
        "score",
        "cases_total",
        "cases_passed",
        "cases_failed",
        "cases_errored",
        "evaluation_cost",
        "completed_at",
    )
    list_filter = ("status",)
    ordering = ("-created_at",)

    @admin.display(description="Score")
    def score(self, obj):
        return (obj.aggregate_metrics or {}).get("score")


@admin.register(EvaluationCaseResult)
class EvaluationCaseResultAdmin(ReadOnlyAdmin):
    list_display = ("case_id", "run", "case_type", "status", "estimated_cost")
    list_filter = ("status", "case_type")
    search_fields = ("case_id", "stable_source_id")
    list_select_related = ("run",)


@admin.register(EvaluationScore)
class EvaluationScoreAdmin(ReadOnlyAdmin):
    list_display = ("case_result", "metric_name", "numeric_score", "passed", "evaluator_type")
    list_filter = ("metric_name", "passed", "evaluator_type")
    list_select_related = ("case_result",)
