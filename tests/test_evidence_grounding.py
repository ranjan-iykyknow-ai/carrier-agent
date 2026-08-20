"""Deterministic evidence grounding (spec 3D/3E): exact matching, never invention."""

import pytest

from apps.inquiries.evidence import (
    EvidenceGroundingError,
    ground_email_excerpt,
    ground_transcript_excerpt,
)
from apps.inquiries.extraction_schema import EvidenceRef
from tests.factories import make_email_content, make_transcript

pytestmark = pytest.mark.django_db

BODY = "Not at $240. Our floor is $280. Can go $280 all-in if quick pay."


def ref(**kwargs):
    defaults = {"source_part": "body", "excerpt": "$280", "occurrence": 1}
    defaults.update(kwargs)
    return EvidenceRef(**defaults)


class TestEmailGrounding:
    def test_exact_excerpt_yields_character_offsets(self):
        email = make_email_content(body_text=BODY)
        span = ground_email_excerpt(ref(excerpt="Our floor is $280"), email)
        assert BODY[span.start : span.end] == "Our floor is $280"
        assert span.source_part == "body"

    def test_repeated_excerpt_uses_the_occurrence_ordinal(self):
        email = make_email_content(body_text=BODY)
        first = ground_email_excerpt(ref(occurrence=1), email)
        second = ground_email_excerpt(ref(occurrence=2), email)
        assert first.start < second.start
        assert BODY[second.start : second.end] == "$280"

    def test_missing_ordinal_defaults_to_first_occurrence(self):
        email = make_email_content(body_text=BODY)
        span = ground_email_excerpt(ref(), email)
        assert span.start == BODY.index("$280")

    def test_subject_block_is_matched_independently(self):
        email = make_email_content(subject="Re: Load 29372450", body_text=BODY)
        span = ground_email_excerpt(ref(source_part="subject", excerpt="29372450"), email)
        assert span.source_part == "subject"
        assert email.subject[span.start : span.end] == "29372450"

    def test_fabricated_excerpt_is_rejected(self):
        email = make_email_content(body_text=BODY)
        with pytest.raises(EvidenceGroundingError):
            ground_email_excerpt(ref(excerpt="refrigerated reefer unit"), email)

    def test_out_of_range_occurrence_is_rejected(self):
        email = make_email_content(body_text=BODY)
        with pytest.raises(EvidenceGroundingError):
            ground_email_excerpt(ref(occurrence=9), email)

    def test_excerpt_never_spans_blocks(self):
        email = make_email_content(subject="Load 29372450", body_text=BODY)
        with pytest.raises(EvidenceGroundingError):
            ground_email_excerpt(ref(source_part="body", excerpt="Load 29372450"), email)


class TestTranscriptGrounding:
    def test_excerpt_verified_against_the_named_segment(self):
        from apps.comms.models import TranscriptSegment

        transcript = make_transcript()
        segment = TranscriptSegment.objects.create(
            transcript=transcript,
            sequence=2,
            speaker_label="1",
            start_seconds="14.20",
            end_seconds="22.80",
            text="Can you do four hundred all-in on the Philly load?",
        )
        grounded = ground_transcript_excerpt(
            EvidenceRef(
                source_part="transcript",
                excerpt="four hundred all-in",
                occurrence=1,
                segment_sequence=2,
            ),
            transcript,
        )
        assert grounded.segment.id == segment.id
        assert str(grounded.start_seconds) == "14.20"

    def test_unknown_segment_or_wrong_text_is_rejected(self):
        transcript = make_transcript()
        with pytest.raises(EvidenceGroundingError):
            ground_transcript_excerpt(
                EvidenceRef(
                    source_part="transcript",
                    excerpt="anything",
                    occurrence=1,
                    segment_sequence=7,
                ),
                transcript,
            )
