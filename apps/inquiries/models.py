"""Step 2C — inquiry, evidence, quotes, and entity resolution.

Trust boundary: OpenAI produces a schema-valid extraction proposal; deterministic
application code normalizes values, verifies evidence, resolves database entities,
assigns review reasons, and writes canonical records transactionally. No numeric
LLM confidence is stored or shown — evidence status, match tier, and review status
are the product's categorical confidence.
"""

from django.db import models
from django.db.models import F, Q

from apps.aiops.models import AIOperation
from apps.base import AppendOnlyModel, TimeStampedModel
from apps.comms.models import CommunicationEvent, IngestionJob, Transcript, TranscriptSegment
from apps.freight.models import Carrier, EquipmentType, Load


class EvidenceStatus(models.TextChoices):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    MISSING = "missing"
    CONFLICTING = "conflicting"


class MatchTier(models.TextChoices):
    EXACT = "exact"
    STRONG = "strong"
    WEAK = "weak"
    CONFLICTING = "conflicting"


class ExtractionRun(AppendOnlyModel):
    """Every model extraction attempt, preserved append-only."""

    class ValidationStatus(models.TextChoices):
        VALID = "valid"
        INVALID = "invalid"

    communication_event = models.ForeignKey(
        CommunicationEvent, on_delete=models.PROTECT, related_name="extraction_runs"
    )
    ingestion_job = models.ForeignKey(
        IngestionJob, on_delete=models.PROTECT, related_name="extraction_runs"
    )
    # Whole-job retry generation captured at claim by the execution that produced this run.
    retry_generation = models.PositiveIntegerField()
    # Call extraction consumed a specific transcript; reuse keys on its identity.
    transcript = models.ForeignKey(
        Transcript,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="extraction_runs",
    )
    schema_version = models.CharField(max_length=50)
    prompt_name = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=50)
    prompt_source = models.CharField(max_length=20, null=True, blank=True)
    model = models.CharField(max_length=100)
    raw_output = models.JSONField(default=dict, blank=True)
    validated_output = models.JSONField(null=True, blank=True)
    validation_status = models.CharField(
        max_length=10, choices=ValidationStatus.choices, null=True, blank=True
    )
    validation_errors = models.JSONField(null=True, blank=True)
    is_current = models.BooleanField(default=False)
    ai_operation = models.ForeignKey(
        AIOperation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="extraction_runs",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["communication_event"],
                condition=Q(is_current=True),
                name="inquiries_one_current_extraction_per_event",
            ),
        ]
        indexes = [
            # Deterministic reuse lookup: same schema + prompt version + model, current only.
            models.Index(
                fields=["communication_event", "schema_version", "prompt_version", "model"],
                name="inquiries_extraction_reuse_idx",
            ),
        ]

    def __str__(self):
        return f"Extraction {self.id} ({'current' if self.is_current else 'superseded'})"


class Inquiry(TimeStampedModel):
    """Typed business representation of what a carrier communicated."""

    class AvailabilityStatus(models.TextChoices):
        CONFIRMED = "confirmed"
        CONDITIONAL = "conditional"
        UNAVAILABLE = "unavailable"
        NOT_STATED = "not_stated"
        CONFLICTING = "conflicting"

    class ResolutionStatus(models.TextChoices):
        VERIFIED = "verified"
        NEEDS_REVIEW = "needs_review"
        UNMATCHED = "unmatched"

    class ReviewStatus(models.TextChoices):
        UNREVIEWED = "unreviewed"
        NEEDS_REVIEW = "needs_review"
        APPROVED = "approved"
        REJECTED = "rejected"

    class Intent(models.TextChoices):
        AVAILABILITY = "availability"
        RATE_QUOTE = "rate_quote"
        RATE_NEGOTIATION = "rate_negotiation"
        LOAD_DETAIL_QUESTION = "load_detail_question"
        COMPLIANCE = "compliance"
        CONFIRMATION = "confirmation"
        DECLINE = "decline"
        FACTORING_OR_PAYMENT = "factoring_or_payment"
        PROBLEM_OR_EXCEPTION = "problem_or_exception"
        GENERAL_INQUIRY = "general_inquiry"
        OTHER = "other"

    communication_event = models.ForeignKey(
        CommunicationEvent, on_delete=models.PROTECT, related_name="inquiries"
    )
    sequence_number = models.PositiveIntegerField(default=1)
    current_extraction = models.ForeignKey(
        ExtractionRun,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="canonical_inquiries",
    )
    primary_intent = models.CharField(max_length=25, choices=Intent.choices)
    availability_status = models.CharField(
        max_length=15, choices=AvailabilityStatus.choices, default=AvailabilityStatus.NOT_STATED
    )
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, null=True, blank=True, related_name="inquiries"
    )
    equipment_raw = models.CharField(max_length=100, null=True, blank=True)
    carrier = models.ForeignKey(
        Carrier, on_delete=models.PROTECT, null=True, blank=True, related_name="inquiries"
    )
    load = models.ForeignKey(
        Load, on_delete=models.PROTECT, null=True, blank=True, related_name="inquiries"
    )
    carrier_resolution_status = models.CharField(
        max_length=15, choices=ResolutionStatus.choices, default=ResolutionStatus.UNMATCHED
    )
    load_resolution_status = models.CharField(
        max_length=15, choices=ResolutionStatus.choices, default=ResolutionStatus.UNMATCHED
    )
    review_status = models.CharField(
        max_length=15, choices=ReviewStatus.choices, default=ReviewStatus.UNREVIEWED
    )
    summary = models.TextField(blank=True, default="")
    conditions_text = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["communication_event", "sequence_number"],
                name="inquiries_sequence_uniq",
            ),
            models.CheckConstraint(
                condition=Q(
                    review_status__in=["unreviewed", "needs_review", "approved", "rejected"]
                ),
                name="inquiries_review_status_vocab",
            ),
        ]
        indexes = [
            models.Index(fields=["review_status"]),
            models.Index(fields=["primary_intent"]),
        ]
        verbose_name_plural = "inquiries"

    def __str__(self):
        return f"Inquiry {self.sequence_number} of {self.communication_event_id}"


class InquiryIntent(TimeStampedModel):
    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="intents")
    intent = models.CharField(max_length=25, choices=Inquiry.Intent.choices)
    is_primary = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["inquiry", "intent"],
                name="inquiries_intent_uniq",
            ),
            models.UniqueConstraint(
                fields=["inquiry"],
                condition=Q(is_primary=True),
                name="inquiries_one_primary_intent",
            ),
        ]


class InquiryQuestion(TimeStampedModel):
    class Category(models.TextChoices):
        RATE = "rate"
        WEIGHT = "weight"
        PICKUP_WINDOW = "pickup_window"
        DELIVERY = "delivery"
        EQUIPMENT = "equipment"
        LOAD_DETAILS = "load_details"
        COMPLIANCE = "compliance"
        NEXT_STEPS = "next_steps"
        OTHER = "other"

    class AnswerStatus(models.TextChoices):
        UNANSWERED = "unanswered"
        ANSWERED = "answered"

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="questions")
    category = models.CharField(max_length=20, choices=Category.choices)
    question_text = models.TextField()
    answer_status = models.CharField(
        max_length=15, choices=AnswerStatus.choices, default=AnswerStatus.UNANSWERED
    )


class EvidenceSpan(AppendOnlyModel):
    """A stable, citable portion of a communication."""

    class SourcePart(models.TextChoices):
        SUBJECT = "subject"
        BODY = "body"
        TRANSCRIPT = "transcript"

    communication_event = models.ForeignKey(
        CommunicationEvent, on_delete=models.PROTECT, related_name="evidence_spans"
    )
    source_part = models.CharField(max_length=15, choices=SourcePart.choices)
    stable_evidence_id = models.CharField(max_length=300)
    excerpt = models.TextField()
    start_offset = models.IntegerField(null=True, blank=True)
    end_offset = models.IntegerField(null=True, blank=True)
    start_seconds = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    end_seconds = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    transcript_segment = models.ForeignKey(
        TranscriptSegment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="evidence_spans",
    )

    class Meta:
        constraints = [
            # Channel-appropriate shape: email spans carry both character offsets and
            # nothing time-based; transcript spans carry both timestamps and no offsets.
            models.CheckConstraint(
                condition=(
                    Q(source_part__in=["subject", "body"])
                    & Q(start_offset__isnull=False)
                    & Q(end_offset__isnull=False)
                    & Q(start_seconds__isnull=True)
                    & Q(end_seconds__isnull=True)
                    & Q(transcript_segment__isnull=True)
                )
                | (
                    Q(source_part="transcript")
                    & Q(start_seconds__isnull=False)
                    & Q(end_seconds__isnull=False)
                    & Q(start_offset__isnull=True)
                    & Q(end_offset__isnull=True)
                ),
                name="inquiries_span_channel_shape",
            ),
            models.CheckConstraint(
                condition=(Q(start_offset__isnull=True) | Q(end_offset__gte=F("start_offset")))
                & (Q(start_seconds__isnull=True) | Q(end_seconds__gte=F("start_seconds"))),
                name="inquiries_span_ordering",
            ),
            # Reconciliation idempotency: one span row per stable citation id per event.
            models.UniqueConstraint(
                fields=["communication_event", "stable_evidence_id"],
                name="inquiries_span_stable_id_uniq",
            ),
        ]
        indexes = [models.Index(fields=["stable_evidence_id"])]

    def __str__(self):
        return self.stable_evidence_id


class InquiryFieldAssessment(AppendOnlyModel):
    class FieldName(models.TextChoices):
        CARRIER_IDENTITY = "carrier_identity"
        LOAD_REFERENCE = "load_reference"
        EQUIPMENT = "equipment"
        AVAILABILITY = "availability"
        INTENT = "intent"
        RATE = "rate"
        QUESTION = "question"
        CONDITIONS = "conditions"

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="field_assessments")
    field_name = models.CharField(max_length=20, choices=FieldName.choices)
    evidence_status = models.CharField(max_length=15, choices=EvidenceStatus.choices)
    value_snapshot = models.JSONField(null=True, blank=True)
    reason_code = models.CharField(max_length=50, null=True, blank=True)
    explanation = models.TextField(blank=True, default="")
    is_current = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["inquiry", "field_name"],
                condition=Q(is_current=True),
                name="inquiries_one_current_assessment_per_field",
            ),
        ]


class InquiryFieldEvidenceLink(AppendOnlyModel):
    class Relationship(models.TextChoices):
        SUPPORTING = "supporting"
        CONTRADICTING = "contradicting"

    assessment = models.ForeignKey(
        InquiryFieldAssessment, on_delete=models.CASCADE, related_name="evidence_links"
    )
    evidence_span = models.ForeignKey(
        EvidenceSpan, on_delete=models.PROTECT, related_name="field_links"
    )
    relationship = models.CharField(max_length=15, choices=Relationship.choices)


class CarrierQuote(TimeStampedModel):
    """A monetary mention with its semantic role — roles are never interchangeable."""

    class QuoteType(models.TextChoices):
        CARRIER_QUOTE = "carrier_quote"
        CARRIER_COUNTEROFFER = "carrier_counteroffer"
        BROKER_RATE_REFERENCE = "broker_rate_reference"
        ACCEPTED_BROKER_RATE = "accepted_broker_rate"
        AMBIGUOUS = "ambiguous"

    class RateBasis(models.TextChoices):
        ALL_IN = "all_in"
        PER_MILE = "per_mile"
        HOURLY = "hourly"
        UNKNOWN = "unknown"

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="quotes")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    quote_type = models.CharField(max_length=25, choices=QuoteType.choices)
    rate_basis = models.CharField(max_length=10, choices=RateBasis.choices)
    is_current = models.BooleanField(default=False)
    supersedes = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="superseded_by"
    )
    evidence_status = models.CharField(max_length=15, choices=EvidenceStatus.choices)
    evidence_spans = models.ManyToManyField(EvidenceSpan, blank=True, related_name="quotes")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="inquiries_quote_amount_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.quote_type} {self.currency} {self.amount}"


class InquiryCarrierMatch(TimeStampedModel):
    class PrimaryMethod(models.TextChoices):
        MC_NUMBER = "mc_number"
        DOT_NUMBER = "dot_number"
        EMAIL = "email"
        PHONE = "phone"
        MULTI_SIGNAL = "multi_signal"
        NAME_SIMILARITY = "name_similarity"
        # A broker picked this carrier with no prior deterministic signal.
        BROKER_CONFIRMED = "broker_confirmed"

    class Status(models.TextChoices):
        PROPOSED = "proposed"
        VERIFIED = "verified"
        REJECTED = "rejected"

    class SelectionSource(models.TextChoices):
        DETERMINISTIC = "deterministic"
        BROKER = "broker"

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="carrier_matches")
    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, related_name="inquiry_matches")
    match_tier = models.CharField(max_length=15, choices=MatchTier.choices)
    primary_method = models.CharField(max_length=20, choices=PrimaryMethod.choices)
    signal_codes = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PROPOSED)
    is_selected = models.BooleanField(default=False)
    selection_source = models.CharField(
        max_length=15, choices=SelectionSource.choices, null=True, blank=True
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["inquiry", "carrier"],
                name="inquiries_carrier_candidate_uniq",
            ),
            models.UniqueConstraint(
                fields=["inquiry"],
                condition=Q(is_selected=True),
                name="inquiries_one_selected_carrier",
            ),
        ]
        verbose_name_plural = "inquiry carrier matches"


class InquiryLoadMatch(TimeStampedModel):
    class PrimaryMethod(models.TextChoices):
        EXTERNAL_LOAD_ID = "external_load_id"
        CORRECTED_REFERENCE = "corrected_reference"
        LANE_EQUIPMENT_PICKUP = "lane_equipment_pickup"
        LANE_OR_EQUIPMENT = "lane_or_equipment"

    class Status(models.TextChoices):
        PROPOSED = "proposed"
        VERIFIED = "verified"
        REJECTED = "rejected"

    class SelectionSource(models.TextChoices):
        DETERMINISTIC = "deterministic"
        BROKER = "broker"

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="load_matches")
    load = models.ForeignKey(Load, on_delete=models.PROTECT, related_name="inquiry_matches")
    match_tier = models.CharField(max_length=15, choices=MatchTier.choices)
    primary_method = models.CharField(max_length=25, choices=PrimaryMethod.choices)
    signal_codes = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PROPOSED)
    is_selected = models.BooleanField(default=False)
    selection_source = models.CharField(
        max_length=15, choices=SelectionSource.choices, null=True, blank=True
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["inquiry", "load"],
                name="inquiries_load_candidate_uniq",
            ),
            models.UniqueConstraint(
                fields=["inquiry"],
                condition=Q(is_selected=True),
                name="inquiries_one_selected_load",
            ),
        ]
        verbose_name_plural = "inquiry load matches"


class InquiryReviewReason(AppendOnlyModel):
    class Code(models.TextChoices):
        MISSING_MC = "missing_mc"
        GARBLED_MC = "garbled_mc"
        WEAK_CARRIER_MATCH = "weak_carrier_match"
        CONFLICTING_CARRIER_IDENTITY = "conflicting_carrier_identity"
        AMBIGUOUS_LOAD_REFERENCE = "ambiguous_load_reference"
        CONFLICTING_LOAD_REFERENCE = "conflicting_load_reference"
        EQUIPMENT_CONFLICT = "equipment_conflict"
        AMBIGUOUS_RATE = "ambiguous_rate"
        MISSING_RATE = "missing_rate"
        CONFLICTING_AVAILABILITY = "conflicting_availability"
        LOW_CONFIDENCE_TRANSCRIPT_EVIDENCE = "low_confidence_transcript_evidence"
        QUOTE_CHRONOLOGY_UNRESOLVED = "quote_chronology_unresolved"
        METADATA_CONTENT_CONFLICT = "metadata_content_conflict"
        EXTRACTION_VALIDATION_FAILURE = "extraction_validation_failure"

    class Severity(models.TextChoices):
        REVIEW = "review"
        INFORMATIONAL = "informational"

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="review_reasons")
    field_assessment = models.ForeignKey(
        InquiryFieldAssessment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="review_reasons",
    )
    code = models.CharField(max_length=40, choices=Code.choices)
    # review_status derivation counts only review-severity reasons; informational
    # reasons render as warnings without forcing needs_review.
    severity = models.CharField(max_length=15, choices=Severity.choices, default=Severity.REVIEW)
    details = models.TextField(blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)

    # Codes whose reasons are warnings by default; everything else gates review.
    DEFAULT_INFORMATIONAL_CODES = frozenset({Code.METADATA_CONTENT_CONFLICT})

    @classmethod
    def default_severity(cls, code) -> str:
        if code in cls.DEFAULT_INFORMATIONAL_CODES:
            return cls.Severity.INFORMATIONAL
        return cls.Severity.REVIEW

    def __str__(self):
        return f"{self.code} ({self.severity})"
