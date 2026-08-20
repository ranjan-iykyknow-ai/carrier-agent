"""Step 2E — broker review, response drafting, assistant, and tools.

Broker corrections are auditable and never rewrite source evidence or machine
output. Drafting and assistant operations are grounded, read-only toward the
world: no email is sent, no booking exists, no compliance outcome can be changed
from here.
"""

from django.db import models
from django.db.models import Q

from apps.aiops.models import AIOperation
from apps.base import AppendOnlyModel, TimeStampedModel
from apps.candidates.models import CarrierLoadCandidate
from apps.freight.models import Carrier, Load
from apps.inquiries.models import EvidenceSpan, Inquiry


class CitationSourceType(models.TextChoices):
    EMAIL = "email"
    CALL = "call"
    TRANSCRIPT_SEGMENT = "transcript_segment"
    INQUIRY = "inquiry"
    LOAD = "load"
    CARRIER = "carrier"
    QUOTE = "quote"
    ASSESSMENT = "assessment"
    MARKET_RATE = "market_rate"


class InquiryReviewAction(AppendOnlyModel):
    """Append-only audit record of a broker decision on an inquiry."""

    class ActionType(models.TextChoices):
        APPROVE_EXTRACTION = "approve_extraction"
        REJECT_EXTRACTION = "reject_extraction"
        CORRECT_CARRIER = "correct_carrier"
        CORRECT_LOAD = "correct_load"
        REOPEN_REVIEW = "reopen_review"

    inquiry = models.ForeignKey(Inquiry, on_delete=models.PROTECT, related_name="review_actions")
    action_type = models.CharField(max_length=25, choices=ActionType.choices)
    actor_label = models.CharField(max_length=200)
    previous_carrier = models.ForeignKey(
        Carrier, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    new_carrier = models.ForeignKey(
        Carrier, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    previous_load = models.ForeignKey(
        Load, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    new_load = models.ForeignKey(
        Load, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    reason = models.TextField(blank=True, default="")
    before_snapshot = models.JSONField(default=dict, blank=True)
    after_snapshot = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.action_type} on {self.inquiry_id}"


class DraftResponse(TimeStampedModel):
    """A generated reply the broker can edit and copy; the app never sends email."""

    class DraftType(models.TextChoices):
        PROVIDE_RATE = "provide_rate"
        NEGOTIATE_RATE = "negotiate_rate"
        REQUEST_INFORMATION = "request_information"
        CONFIRM_NEXT_STEPS = "confirm_next_steps"
        DECLINE = "decline"
        DEFER = "defer"

    class Status(models.TextChoices):
        GENERATED = "generated"
        EDITED = "edited"
        COPIED = "copied"
        DISCARDED = "discarded"

    inquiry = models.ForeignKey(Inquiry, on_delete=models.PROTECT, related_name="drafts")
    candidate = models.ForeignKey(
        CarrierLoadCandidate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="drafts",
    )
    load = models.ForeignKey(Load, on_delete=models.PROTECT, related_name="drafts")
    carrier = models.ForeignKey(
        Carrier, on_delete=models.PROTECT, null=True, blank=True, related_name="drafts"
    )
    draft_type = models.CharField(max_length=25, choices=DraftType.choices)
    generated_subject = models.TextField(blank=True, default="")
    generated_body = models.TextField()
    current_subject = models.TextField(blank=True, default="")
    current_body = models.TextField()
    context_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.GENERATED)
    created_by = models.CharField(max_length=200)
    prompt_name = models.CharField(max_length=100, null=True, blank=True)
    prompt_version = models.CharField(max_length=50, null=True, blank=True)
    model = models.CharField(max_length=100, null=True, blank=True)
    langfuse_trace_id = models.CharField(max_length=100, null=True, blank=True)
    ai_operation = models.ForeignKey(
        AIOperation, on_delete=models.PROTECT, null=True, blank=True, related_name="drafts"
    )
    copied_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.draft_type} draft ({self.status})"


class DraftEvidenceLink(AppendOnlyModel):
    draft = models.ForeignKey(
        DraftResponse, on_delete=models.CASCADE, related_name="evidence_links"
    )
    evidence_span = models.ForeignKey(
        EvidenceSpan, on_delete=models.PROTECT, null=True, blank=True, related_name="draft_links"
    )
    source_type = models.CharField(
        max_length=30, choices=CitationSourceType.choices, null=True, blank=True
    )
    stable_source_id = models.CharField(max_length=300, null=True, blank=True)
    fact_name = models.CharField(max_length=100)
    fact_value = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            # An evidence link must anchor to a span or a stable identifier.
            models.CheckConstraint(
                condition=Q(evidence_span__isnull=False) | Q(stable_source_id__isnull=False),
                name="workspace_draft_evidence_anchor",
            ),
        ]


class AssistantConversation(TimeStampedModel):
    class Scope(models.TextChoices):
        GLOBAL = "global"
        LOAD = "load"

    class Status(models.TextChoices):
        ACTIVE = "active"
        CLOSED = "closed"

    actor_label = models.CharField(max_length=200)
    scope = models.CharField(max_length=10, choices=Scope.choices)
    load = models.ForeignKey(
        Load,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assistant_conversations",
    )
    title = models.CharField(max_length=300, blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(scope="load") & Q(load__isnull=False))
                | (Q(scope="global") & Q(load__isnull=True)),
                name="workspace_conversation_scope_load",
            ),
        ]

    def __str__(self):
        return self.title or f"Conversation {self.id}"


class AssistantMessage(TimeStampedModel):
    class Role(models.TextChoices):
        USER = "user"
        ASSISTANT = "assistant"

    class Status(models.TextChoices):
        PENDING = "pending"
        COMPLETED = "completed"
        FAILED = "failed"

    conversation = models.ForeignKey(
        AssistantConversation, on_delete=models.CASCADE, related_name="messages"
    )
    sequence = models.PositiveIntegerField()
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField(blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETED)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "sequence"],
                name="workspace_message_sequence_uniq",
            ),
        ]
        ordering = ["sequence"]


class AssistantRun(TimeStampedModel):
    """One attempted assistant response; a timeout is durable, never a hidden continuation."""

    class Status(models.TextChoices):
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        TIMED_OUT = "timed_out"

    conversation = models.ForeignKey(
        AssistantConversation, on_delete=models.CASCADE, related_name="runs"
    )
    user_message = models.ForeignKey(
        AssistantMessage, on_delete=models.PROTECT, related_name="runs_as_user_message"
    )
    assistant_message = models.ForeignKey(
        AssistantMessage,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="runs_as_assistant_message",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    scope_snapshot = models.JSONField(default=dict, blank=True)
    prompt_name = models.CharField(max_length=100, null=True, blank=True)
    prompt_version = models.CharField(max_length=50, null=True, blank=True)
    model = models.CharField(max_length=100, null=True, blank=True)
    langfuse_trace_id = models.CharField(max_length=100, null=True, blank=True)
    ai_operation = models.ForeignKey(
        AIOperation, on_delete=models.PROTECT, null=True, blank=True, related_name="assistant_runs"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=100, null=True, blank=True)
    error_summary = models.TextField(blank=True, default="")

    def __str__(self):
        return f"Run {self.id} ({self.status})"


class ToolExecution(AppendOnlyModel):
    """One read-only assistant tool call; results are the citation-validation source."""

    class Status(models.TextChoices):
        COMPLETED = "completed"
        FAILED = "failed"

    assistant_run = models.ForeignKey(
        AssistantRun, on_delete=models.CASCADE, related_name="tool_executions"
    )
    sequence = models.PositiveIntegerField()
    tool_name = models.CharField(max_length=100)
    arguments = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETED)
    result_summary = models.TextField(blank=True, default="")
    result_record_ids = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=100, null=True, blank=True)
    error_summary = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assistant_run", "sequence"],
                name="workspace_tool_sequence_uniq",
            ),
        ]


class AssistantCitation(AppendOnlyModel):
    assistant_message = models.ForeignKey(
        AssistantMessage, on_delete=models.CASCADE, related_name="citations"
    )
    sequence = models.PositiveIntegerField()
    source_type = models.CharField(max_length=20, choices=CitationSourceType.choices)
    stable_source_id = models.CharField(max_length=300)
    evidence_span = models.ForeignKey(
        EvidenceSpan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assistant_citations",
    )
    display_label = models.CharField(max_length=300)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assistant_message", "sequence"],
                name="workspace_citation_sequence_uniq",
            ),
        ]
        ordering = ["sequence"]
