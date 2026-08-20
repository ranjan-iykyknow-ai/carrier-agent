"""Deterministic evidence grounding (spec 3D/3E).

The model proposes a source part and short exact excerpt; this module locates
it — or refuses. The matching surface is byte-identical to the stored subject,
body, or segment text (no normalization), matching is an exact code-point
substring search within the named block only, and a repeated excerpt is
disambiguated by an occurrence ordinal (missing ordinal = first occurrence).
Fabricated, altered, or out-of-range evidence raises; it is never accepted.
"""

from dataclasses import dataclass
from decimal import Decimal

from apps.comms.models import EmailContent, Transcript, TranscriptSegment
from apps.inquiries.extraction_schema import EvidenceRef


class EvidenceGroundingError(Exception):
    """The proposed evidence does not exist in the stored source."""


@dataclass(frozen=True)
class GroundedEmailSpan:
    source_part: str  # subject | body
    start: int
    end: int
    excerpt: str


@dataclass(frozen=True)
class GroundedTranscriptSpan:
    segment: TranscriptSegment
    start_seconds: Decimal
    end_seconds: Decimal
    excerpt: str


def _find_occurrence(haystack: str, needle: str, occurrence: int) -> int:
    position = -1
    for _ in range(occurrence):
        position = haystack.find(needle, position + 1)
        if position == -1:
            raise EvidenceGroundingError(
                f"excerpt occurrence {occurrence} not found in the named block"
            )
    return position


def ground_email_excerpt(ref: EvidenceRef, email: EmailContent) -> GroundedEmailSpan:
    if ref.source_part == "subject":
        block = email.subject
    elif ref.source_part == "body":
        block = email.body_text
    else:
        raise EvidenceGroundingError("transcript evidence offered for an email source")

    start = _find_occurrence(block, ref.excerpt, ref.occurrence)
    return GroundedEmailSpan(
        source_part=ref.source_part,
        start=start,
        end=start + len(ref.excerpt),
        excerpt=ref.excerpt,
    )


def ground_transcript_excerpt(ref: EvidenceRef, transcript: Transcript) -> GroundedTranscriptSpan:
    if ref.source_part != "transcript" or ref.segment_sequence is None:
        raise EvidenceGroundingError("transcript evidence requires a segment identity")
    segment = transcript.segments.filter(sequence=ref.segment_sequence).first()
    if segment is None:
        raise EvidenceGroundingError(f"transcript segment {ref.segment_sequence} does not exist")
    _find_occurrence(segment.text, ref.excerpt, ref.occurrence)
    return GroundedTranscriptSpan(
        segment=segment,
        start_seconds=segment.start_seconds,
        end_seconds=segment.end_seconds,
        excerpt=ref.excerpt,
    )
