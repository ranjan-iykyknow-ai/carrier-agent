"""Step 2F (P0) — AI operation accounting and offline evaluation.

Neither table stores prompt bodies, emails, transcripts, credentials, or full
provider responses. Cost is an estimate carrying its own pricing snapshot;
historical cost is never recalculated with newer prices. Unknown usage or cost
stays NULL — never zero.
"""

from django.db import models
from django.db.models import Q

from apps.base import AppendOnlyModel, TimeStampedModel


class AIOperation(TimeStampedModel):
    """One logical transcription, extraction, draft, assistant turn, or evaluation case."""

    class OperationType(models.TextChoices):
        TRANSCRIPTION = "transcription"
        EXTRACTION = "extraction"
        DRAFT = "draft"
        ASSISTANT_TURN = "assistant_turn"
        EVALUATION_CASE = "evaluation_case"

    class UsageCategory(models.TextChoices):
        INGESTION = "ingestion"
        ASSISTANT = "assistant"
        DRAFTING = "drafting"
        EVALUATION = "evaluation"

    class Status(models.TextChoices):
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"

    operation_type = models.CharField(max_length=20, choices=OperationType.choices)
    usage_category = models.CharField(max_length=15, choices=UsageCategory.choices)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.RUNNING)
    correlation_id = models.UUIDField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    provider_call_count = models.PositiveIntegerField(default=0)
    prompt_tokens = models.IntegerField(null=True, blank=True)
    completion_tokens = models.IntegerField(null=True, blank=True)
    total_tokens = models.IntegerField(null=True, blank=True)
    audio_seconds = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    cost_currency = models.CharField(max_length=3, default="USD")
    last_error_code = models.CharField(max_length=100, null=True, blank=True)
    last_error_summary = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["operation_type", "status"]),
            models.Index(fields=["usage_category"]),
            models.Index(fields=["correlation_id"]),
        ]

    def __str__(self):
        return f"{self.operation_type} ({self.status})"


class AIProviderCall(AppendOnlyModel):
    """One external provider request or retry; every retry is its own visible row."""

    class Provider(models.TextChoices):
        OPENAI = "openai"
        DEEPGRAM = "deepgram"

    class PromptSource(models.TextChoices):
        LANGFUSE = "langfuse"
        CACHE = "cache"
        LOCAL_FALLBACK = "local_fallback"

    class Status(models.TextChoices):
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"

    operation = models.ForeignKey(
        AIOperation, on_delete=models.CASCADE, related_name="provider_calls"
    )
    sequence = models.PositiveIntegerField()
    provider = models.CharField(max_length=20, choices=Provider.choices)
    operation_name = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    provider_request_id = models.CharField(max_length=200, null=True, blank=True)
    prompt_name = models.CharField(max_length=100, null=True, blank=True)
    prompt_version = models.CharField(max_length=50, null=True, blank=True)
    prompt_source = models.CharField(
        max_length=20, choices=PromptSource.choices, null=True, blank=True
    )
    langfuse_trace_id = models.CharField(max_length=100, null=True, blank=True)
    langfuse_observation_id = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.RUNNING)
    attempt_number = models.PositiveIntegerField(default=1)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    prompt_tokens = models.IntegerField(null=True, blank=True)
    completion_tokens = models.IntegerField(null=True, blank=True)
    audio_seconds = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    pricing_version = models.CharField(max_length=50, null=True, blank=True)
    pricing_snapshot = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=100, null=True, blank=True)
    error_summary = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["operation", "sequence"],
                name="aiops_provider_call_sequence_uniq",
            ),
        ]
        indexes = [models.Index(fields=["provider", "status"])]

    def __str__(self):
        return f"{self.provider}:{self.operation_name} #{self.sequence}"


class EvaluationRun(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"

    name = models.CharField(max_length=200)
    dataset_version = models.CharField(max_length=100)
    dataset_checksum = models.CharField(max_length=64)
    git_commit_sha = models.CharField(max_length=40)
    prompt_versions = models.JSONField(default=dict, blank=True)
    model_versions = models.JSONField(default=dict, blank=True)
    policy_version = models.CharField(max_length=100, null=True, blank=True)
    configuration = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    langfuse_dataset_run_id = models.CharField(max_length=200, null=True, blank=True)
    cases_total = models.IntegerField(null=True, blank=True)
    cases_passed = models.IntegerField(null=True, blank=True)
    cases_failed = models.IntegerField(null=True, blank=True)
    cases_errored = models.IntegerField(null=True, blank=True)
    aggregate_metrics = models.JSONField(default=dict, blank=True)
    evaluation_cost = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    report_path = models.CharField(max_length=500, null=True, blank=True)
    error_summary = models.TextField(blank=True, default="")

    def __str__(self):
        return self.name


class EvaluationCaseResult(AppendOnlyModel):
    class CaseType(models.TextChoices):
        EMAIL = "email"
        CALL = "call"

    class Mode(models.TextChoices):
        REVIEWED_TRANSCRIPT = "reviewed_transcript"
        END_TO_END = "end_to_end"

    class Status(models.TextChoices):
        PASSED = "passed"
        FAILED = "failed"
        ERRORED = "errored"

    run = models.ForeignKey(EvaluationRun, on_delete=models.CASCADE, related_name="cases")
    case_id = models.CharField(max_length=200)
    stable_source_id = models.CharField(max_length=200)
    case_type = models.CharField(max_length=10, choices=CaseType.choices)
    evaluation_mode = models.CharField(max_length=25, choices=Mode.choices, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    expected_label_ref = models.CharField(max_length=500)
    actual_output = models.JSONField(default=dict, blank=True)
    ai_operation = models.ForeignKey(
        AIOperation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="evaluation_cases",
    )
    langfuse_trace_id = models.CharField(max_length=100, null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    failure_categories = models.JSONField(default=list, blank=True)
    failure_analysis = models.TextField(blank=True, default="")
    suggested_improvement = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "case_id"],
                name="aiops_evaluation_case_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.case_id} ({self.status})"


class EvaluationScore(AppendOnlyModel):
    class EvaluatorType(models.TextChoices):
        DETERMINISTIC = "deterministic"
        LLM_JUDGE = "llm_judge"
        HUMAN = "human"

    case_result = models.ForeignKey(
        EvaluationCaseResult, on_delete=models.CASCADE, related_name="scores"
    )
    metric_name = models.CharField(max_length=100)
    evaluator_type = models.CharField(max_length=15, choices=EvaluatorType.choices)
    numeric_score = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    categorical_score = models.CharField(max_length=100, null=True, blank=True)
    passed = models.BooleanField(null=True, blank=True)
    explanation = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(numeric_score__isnull=False) | Q(categorical_score__isnull=False),
                name="aiops_score_has_a_value",
            ),
        ]

    def __str__(self):
        return f"{self.metric_name}={self.numeric_score or self.categorical_score}"
