"""Inquiry reconciliation and finalization (spec 3F).

Turns a validated extraction into evidence-backed inquiry cards, resolves
carrier/load candidates deterministically, runs the 3G assessments, and makes
the terminal job transition — all in one finalization transaction guarded by
the write-time generation fence. Everything here is local typed data; no
provider is ever called.
"""

import difflib
import logging
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.candidates.engine import assess_candidate
from apps.candidates.models import CandidateInquiry, CarrierLoadCandidate
from apps.comms.models import IngestionJob
from apps.freight.models import Carrier, CarrierContact, EquipmentAlias, Load
from apps.inquiries.evidence import (
    EvidenceGroundingError,
    ground_email_excerpt,
    ground_transcript_excerpt,
)
from apps.inquiries.extraction_schema import (
    EvidenceRef,
    InquiryProposal,
    parse_extraction,
)
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

logger = logging.getLogger(__name__)

_DIGITS = re.compile(r"\D")

STALE = "stale"


def _digits(value: str | None) -> str | None:
    if not value:
        return None
    normalized = _DIGITS.sub("", value)
    return normalized or None


def finalize_communication(extraction_run: ExtractionRun, *, generation: int) -> str:
    """Run the single finalization transaction; returns the terminal job status,
    or "stale" when the write fence rejects this execution."""
    event = extraction_run.communication_event
    proposal = parse_extraction(extraction_run.validated_output)

    with transaction.atomic():
        job = IngestionJob.objects.select_for_update().get(communication_event=event)
        if job.status != IngestionJob.Status.PROCESSING or job.retry_count != generation:
            return STALE

        existing = Inquiry.objects.filter(communication_event=event).exists()
        if not existing:
            for sequence, item in enumerate(proposal.inquiries, start=1):
                _reconcile_one(event, extraction_run, sequence, item)

        # The terminal transition recomputes the aggregate from persisted rows,
        # so needs_review can never exist without a reviewable inquiry.
        review_statuses = set(
            Inquiry.objects.filter(communication_event=event).values_list(
                "review_status", flat=True
            )
        )
        status = (
            IngestionJob.Status.NEEDS_REVIEW
            if Inquiry.ReviewStatus.NEEDS_REVIEW in review_statuses
            else IngestionJob.Status.COMPLETED
        )
        job.status = status
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at", "updated_at"])
    return status


# ---------------------------------------------------------------------------


def _reconcile_one(event, extraction_run, sequence: int, item: InquiryProposal) -> Inquiry:
    snapshot = event.dataset_snapshot
    reasons: list[tuple[str, str, str]] = []  # (code, severity, details)

    inquiry = Inquiry.objects.create(
        communication_event=event,
        sequence_number=sequence,
        current_extraction=extraction_run,
        primary_intent=item.intents[0],
        availability_status=item.availability,
        equipment_type=_resolve_equipment(item.equipment),
        equipment_raw=item.equipment,
        summary=item.summary,
        conditions_text=item.conditions or "",
    )
    for index, intent in enumerate(dict.fromkeys(item.intents)):
        InquiryIntent.objects.create(inquiry=inquiry, intent=intent, is_primary=index == 0)
    for question in item.questions:
        InquiryQuestion.objects.create(
            inquiry=inquiry, category=question.category, question_text=question.text
        )

    grounder = _Grounder(event, extraction_run)

    _assess_field(
        inquiry, grounder, "carrier_identity", item.carrier_name, item.carrier_name_evidence
    )
    _assess_field(
        inquiry, grounder, "load_reference", item.load_reference, item.load_reference_evidence
    )
    _assess_field(inquiry, grounder, "equipment", item.equipment, item.equipment_evidence)
    _assess_field(
        inquiry,
        grounder,
        "availability",
        None if item.availability == "not_stated" else item.availability,
        item.availability_evidence,
    )
    _assess_field(inquiry, grounder, "intent", item.intents[0], None)
    if item.conditions:
        _assess_field(inquiry, grounder, "conditions", item.conditions, item.conditions_evidence)

    _persist_quotes(inquiry, grounder, item, reasons)
    carrier = _match_carrier(inquiry, snapshot, item, reasons, event=event)
    load = _match_load(inquiry, snapshot, item, reasons)
    _compare_untrusted_metadata(event, inquiry, item, reasons)
    _flag_low_confidence_evidence(inquiry, grounder, item, reasons)

    for code, severity, details in reasons:
        InquiryReviewReason.objects.create(
            inquiry=inquiry, code=code, severity=severity, details=details
        )
    has_review = any(severity == "review" for _, severity, _ in reasons)
    inquiry.review_status = (
        Inquiry.ReviewStatus.NEEDS_REVIEW if has_review else Inquiry.ReviewStatus.UNREVIEWED
    )
    inquiry.save(update_fields=["review_status", "updated_at"])

    if carrier is not None and load is not None:
        candidate, _ = CarrierLoadCandidate.objects.get_or_create(
            carrier=carrier,
            load=load,
            defaults={
                "first_seen_at": event.occurred_at or event.received_at,
                "last_activity_at": event.occurred_at or event.received_at,
            },
        )
        CandidateInquiry.objects.get_or_create(
            candidate=candidate,
            inquiry=inquiry,
            defaults={"relationship": CandidateInquiry.Relationship.SUPPORTING},
        )
        assess_candidate(candidate, triggering_inquiry=inquiry)
    return inquiry


class _Grounder:
    """Grounds evidence refs and creates idempotent EvidenceSpan rows."""

    def __init__(self, event, extraction_run):
        self.event = event
        self.email = getattr(event, "email_content", None) if event.channel == "email" else None
        self.transcript = extraction_run.transcript

    def span_for(self, ref: EvidenceRef | None) -> EvidenceSpan | None:
        if ref is None:
            return None
        try:
            if ref.source_part in ("subject", "body"):
                if self.email is None:
                    return None
                grounded = ground_email_excerpt(ref, self.email)
                stable_id = (
                    f"{self.event.stable_evidence_id}"
                    f"#{grounded.source_part}:{grounded.start}-{grounded.end}"
                )
                span, _ = EvidenceSpan.objects.get_or_create(
                    communication_event=self.event,
                    stable_evidence_id=stable_id,
                    defaults={
                        "source_part": grounded.source_part,
                        "excerpt": grounded.excerpt,
                        "start_offset": grounded.start,
                        "end_offset": grounded.end,
                    },
                )
                return span
            if self.transcript is None:
                return None
            grounded = ground_transcript_excerpt(ref, self.transcript)
            stable_id = (
                f"{self.event.stable_evidence_id}#{grounded.start_seconds}-{grounded.end_seconds}"
            )
            span, _ = EvidenceSpan.objects.get_or_create(
                communication_event=self.event,
                stable_evidence_id=stable_id,
                defaults={
                    "source_part": EvidenceSpan.SourcePart.TRANSCRIPT,
                    "excerpt": grounded.excerpt,
                    "start_seconds": grounded.start_seconds,
                    "end_seconds": grounded.end_seconds,
                    "transcript_segment": grounded.segment,
                },
            )
            return span
        except EvidenceGroundingError:
            logger.warning("ungrounded evidence for %s", self.event.stable_evidence_id)
            return None

    def segment_confidence(self, ref: EvidenceRef | None):
        if ref is None or ref.source_part != "transcript" or self.transcript is None:
            return None
        segment = self.transcript.segments.filter(sequence=ref.segment_sequence).first()
        return segment.confidence if segment else None


def _assess_field(inquiry, grounder, field_name, value, evidence_ref) -> InquiryFieldAssessment:
    if value is None:
        status = "missing"
        span = None
    else:
        span = grounder.span_for(evidence_ref)
        # A value with verifiable evidence is explicit; a value the model stated
        # without locatable evidence is inferred (spec 3B categorical states).
        status = "explicit" if span is not None else "inferred"
    assessment = InquiryFieldAssessment.objects.create(
        inquiry=inquiry,
        field_name=field_name,
        evidence_status=status,
        value_snapshot=value,
        is_current=True,
    )
    if span is not None:
        InquiryFieldEvidenceLink.objects.create(
            assessment=assessment, evidence_span=span, relationship="supporting"
        )
    return assessment


def _persist_quotes(inquiry, grounder, item: InquiryProposal, reasons) -> None:
    carrier_positions = []
    previous_position = None
    for mention in item.rates:
        span = grounder.span_for(mention.evidence)
        quote = CarrierQuote.objects.create(
            inquiry=inquiry,
            amount=mention.amount,
            currency=mention.currency,
            quote_type=mention.role,
            rate_basis=mention.basis,
            evidence_status="explicit" if span is not None else "inferred",
            is_current=False,
        )
        if span is not None:
            quote.evidence_spans.add(span)
        if mention.role in ("carrier_quote", "carrier_counteroffer"):
            if previous_position is not None:
                quote.supersedes = previous_position
                quote.save(update_fields=["supersedes", "updated_at"])
            previous_position = quote
            carrier_positions.append(quote)
    if carrier_positions:
        # Within one communication, the clearly later statement (document order)
        # is the carrier's current position.
        current = carrier_positions[-1]
        if current.evidence_status == "explicit":
            current.is_current = True
            current.save(update_fields=["is_current", "updated_at"])
    _assess_rate_field(inquiry, item, carrier_positions)


def _assess_rate_field(inquiry, item, carrier_positions):
    if not item.rates:
        value, status = None, "missing"
    elif carrier_positions:
        value = str(carrier_positions[-1].amount)
        status = carrier_positions[-1].evidence_status
    else:
        value, status = None, "missing"  # only broker references / ambiguous amounts
    InquiryFieldAssessment.objects.create(
        inquiry=inquiry,
        field_name="rate",
        evidence_status=status,
        value_snapshot=value,
        is_current=True,
    )


def _match_carrier(
    inquiry, snapshot, item: InquiryProposal, reasons, *, event=None
) -> Carrier | None:
    exact: dict = {}  # carrier_id -> (carrier, method)

    def record(carrier, method):
        exact.setdefault(carrier.id, (carrier, method))

    mc = _digits(item.mc_number)
    if mc:
        for carrier in Carrier.objects.filter(dataset_snapshot=snapshot, mc_number_normalized=mc):
            record(carrier, "mc_number")
    dot = _digits(item.dot_number)
    if dot:
        for carrier in Carrier.objects.filter(dataset_snapshot=snapshot, dot_number_normalized=dot):
            record(carrier, "dot_number")
    # The envelope sender address is deterministic evidence, independent of what
    # the model extracted from the text; most dataset emails verify through it.
    email_signals = set()
    envelope = getattr(event, "email_content", None) if event is not None else None
    if envelope and envelope.sender_email_normalized:
        email_signals.add(envelope.sender_email_normalized)
    if item.contact_email:
        email_signals.add(item.contact_email.strip().casefold())
    for signal in email_signals:
        for contact in CarrierContact.objects.filter(
            carrier__dataset_snapshot=snapshot,
            email_normalized=signal,
        ).select_related("carrier"):
            record(contact.carrier, "email")
    if item.contact_phone:
        phone = _digits(item.contact_phone)
        if phone:
            for contact in CarrierContact.objects.filter(
                carrier__dataset_snapshot=snapshot, phone_normalized=phone
            ).select_related("carrier"):
                record(contact.carrier, "phone")

    if len(exact) == 1:
        carrier, method = next(iter(exact.values()))
        InquiryCarrierMatch.objects.create(
            inquiry=inquiry,
            carrier=carrier,
            match_tier="exact",
            primary_method=method,
            status="verified",
            is_selected=True,
            selection_source="deterministic",
        )
        inquiry.carrier = carrier
        inquiry.carrier_resolution_status = Inquiry.ResolutionStatus.VERIFIED
        inquiry.save(update_fields=["carrier", "carrier_resolution_status", "updated_at"])
        return carrier

    if len(exact) > 1:
        for carrier, method in exact.values():
            InquiryCarrierMatch.objects.create(
                inquiry=inquiry,
                carrier=carrier,
                match_tier="conflicting",
                primary_method=method,
            )
        reasons.append(
            (
                "conflicting_carrier_identity",
                "review",
                "exact identity signals point at different carriers",
            )
        )
        inquiry.carrier_resolution_status = Inquiry.ResolutionStatus.NEEDS_REVIEW
        inquiry.save(update_fields=["carrier_resolution_status", "updated_at"])
        return None

    # No exact signal: weak name similarity proposes review candidates only.
    name = (item.carrier_name or "").strip().casefold()
    weak_found = False
    if name:
        scored = []
        for carrier in Carrier.objects.filter(dataset_snapshot=snapshot).exclude(
            company_name__isnull=True
        ):
            company = carrier.company_name.casefold()
            ratio = difflib.SequenceMatcher(None, name, company).ratio()
            # Containment counts: "Blue Ridge" inside "Blue Ridge Transport LLC".
            if name in company or company in name:
                ratio = max(ratio, 0.75)
            if ratio >= settings.WEAK_NAME_SIMILARITY_THRESHOLD:
                scored.append((ratio, carrier))
        for _, carrier in sorted(scored, key=lambda pair: -pair[0])[:3]:
            weak_found = True
            InquiryCarrierMatch.objects.create(
                inquiry=inquiry,
                carrier=carrier,
                match_tier="weak",
                primary_method="name_similarity",
            )
    if weak_found:
        reasons.append(
            ("weak_carrier_match", "review", "name similarity alone cannot verify identity")
        )
        inquiry.carrier_resolution_status = Inquiry.ResolutionStatus.NEEDS_REVIEW
    else:
        code = "missing_mc" if not item.mc_number else "weak_carrier_match"
        reasons.append((code, "review", "no defensible carrier candidate"))
        inquiry.carrier_resolution_status = Inquiry.ResolutionStatus.UNMATCHED
    inquiry.save(update_fields=["carrier_resolution_status", "updated_at"])
    return None


def _match_load(inquiry, snapshot, item: InquiryProposal, reasons) -> Load | None:
    reference = _digits(item.load_reference)
    if reference:
        load = Load.objects.filter(dataset_snapshot=snapshot, external_load_id=reference).first()
        if load is not None:
            InquiryLoadMatch.objects.create(
                inquiry=inquiry,
                load=load,
                match_tier="exact",
                primary_method="external_load_id",
                status="verified",
                is_selected=True,
                selection_source="deterministic",
            )
            inquiry.load = load
            inquiry.load_resolution_status = Inquiry.ResolutionStatus.VERIFIED
            inquiry.save(update_fields=["load", "load_resolution_status", "updated_at"])
            return load
    if item.load_reference:
        # A stated reference (numeric or descriptive) that resolves to nothing
        # is a broker-review case: the carrier meant a specific load.
        reasons.append(
            (
                "ambiguous_load_reference",
                "review",
                "the stated load reference matches nothing in the active snapshot",
            )
        )
    # No reference at all (a cold availability announcement) stays visibly
    # unmatched without flooding the review queue.
    inquiry.load_resolution_status = Inquiry.ResolutionStatus.UNMATCHED
    inquiry.save(update_fields=["load_resolution_status", "updated_at"])
    return None


def _compare_untrusted_metadata(event, inquiry, item: InquiryProposal, reasons) -> None:
    """Untrusted annotations vs extracted content: divergence is a warning, not truth."""
    email = getattr(event, "email_content", None)
    metadata = email.source_metadata if email else {}
    if not metadata:
        return
    conflicts = []
    annotated_equipment = metadata.get("equipment_mentioned")
    if annotated_equipment and item.equipment:
        if annotated_equipment.strip().casefold() != item.equipment.strip().casefold():
            conflicts.append("equipment")
    annotated_mc = _digits(metadata.get("mc_number"))
    extracted_mc = _digits(item.mc_number)
    if annotated_mc and extracted_mc and annotated_mc != extracted_mc:
        conflicts.append("mc_number")
    annotated_load = _digits(metadata.get("load_reference"))
    extracted_load = _digits(item.load_reference)
    if annotated_load and extracted_load and annotated_load != extracted_load:
        conflicts.append("load_reference")
    if conflicts:
        reasons.append(
            (
                "metadata_content_conflict",
                InquiryReviewReason.default_severity(
                    InquiryReviewReason.Code.METADATA_CONTENT_CONFLICT
                ),
                f"dataset annotation diverges from content: {', '.join(conflicts)}",
            )
        )


def _flag_low_confidence_evidence(inquiry, grounder, item: InquiryProposal, reasons) -> None:
    """A critical fact supported only by uncertain transcript audio needs review."""
    critical = [
        item.carrier_name_evidence,
        item.load_reference_evidence,
        item.equipment_evidence,
        item.availability_evidence,
    ]
    threshold = settings.TRANSCRIPT_LOW_CONFIDENCE_THRESHOLD
    for ref in critical:
        confidence = grounder.segment_confidence(ref)
        if confidence is not None and float(confidence) < threshold:
            reasons.append(
                (
                    "low_confidence_transcript_evidence",
                    "review",
                    "a critical field relies on an uncertain transcript region",
                )
            )
            return


def _resolve_equipment(raw: str | None):
    if not raw:
        return None
    normalized = raw.strip().casefold()
    alias = (
        EquipmentAlias.objects.filter(normalized_label=normalized)
        .select_related("equipment_type")
        .first()
    )
    if alias:
        return alias.equipment_type
    from apps.freight.models import EquipmentType

    for equipment in EquipmentType.objects.filter(is_active=True):
        if normalized in (equipment.code.casefold(), equipment.display_name.casefold()):
            return equipment
    return None
