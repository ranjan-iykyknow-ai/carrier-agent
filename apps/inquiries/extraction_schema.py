"""The shared versioned extraction contract (spec 3B) at the AI boundary.

OpenAI returns strict JSON matching this schema; deterministic code validates it
here before anything can influence canonical records. Missing information stays
missing — the model may never invent a carrier, load, rate, equipment type,
availability statement, or evidence pointer.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = "extraction-v1"


class ExtractionValidationError(Exception):
    """Structurally invalid model output; safe to log, never canonical."""


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_part: Literal["subject", "body", "transcript"]
    excerpt: str = Field(min_length=1)
    # A repeated statement is evidence, never a rejection: a missing ordinal
    # deterministically selects the first occurrence.
    occurrence: int = Field(default=1, ge=1)
    segment_sequence: int | None = None


class RateMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0)
    currency: str = "USD"
    role: Literal[
        "carrier_quote",
        "carrier_counteroffer",
        "broker_rate_reference",
        "accepted_broker_rate",
        "ambiguous",
    ]
    basis: Literal["all_in", "per_mile", "hourly", "unknown"]
    evidence: EvidenceRef | None = None


class QuestionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "rate",
        "weight",
        "pickup_window",
        "delivery",
        "equipment",
        "load_details",
        "compliance",
        "next_steps",
        "other",
    ]
    text: str = Field(min_length=1)
    evidence: EvidenceRef | None = None


Intent = Literal[
    "availability",
    "rate_quote",
    "rate_negotiation",
    "load_detail_question",
    "compliance",
    "confirmation",
    "decline",
    "factoring_or_payment",
    "problem_or_exception",
    "general_inquiry",
    "other",
]


class InquiryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    carrier_name: str | None
    carrier_name_evidence: EvidenceRef | None = None
    mc_number: str | None
    mc_number_evidence: EvidenceRef | None = None
    dot_number: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    load_reference: str | None
    load_reference_evidence: EvidenceRef | None = None
    equipment: str | None
    equipment_evidence: EvidenceRef | None = None
    availability: Literal["confirmed", "conditional", "unavailable", "not_stated"]
    availability_evidence: EvidenceRef | None = None
    intents: list[Intent] = Field(min_length=1)
    rates: list[RateMention]
    questions: list[QuestionProposal]
    conditions: str | None = None
    conditions_evidence: EvidenceRef | None = None
    summary: str


class ExtractionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inquiries: list[InquiryProposal] = Field(min_length=1)


def parse_extraction(raw_output: dict) -> ExtractionProposal:
    try:
        return ExtractionProposal.model_validate(raw_output)
    except ValidationError as exc:
        # Compact, payload-free error summary: field paths and error types only.
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['type']}"
            for error in exc.errors()[:10]
        )
        raise ExtractionValidationError(details or "invalid extraction shape") from exc
