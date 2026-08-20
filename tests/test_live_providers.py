"""Live provider-contract tests (marked `live`): real API calls, real schemas.

These are the deploy gate against provider API drift. They cost real money
(cents) and run only via `make test-live` or the CI live-contract job.
"""

from pathlib import Path

import pytest
from django.conf import settings

from apps.aiops.models import AIProviderCall
from apps.aiops.providers import DeepgramTranscriber, OpenAIExtractor
from apps.inquiries.extraction_schema import parse_extraction
from tests.factories import make_call_recording

pytestmark = [pytest.mark.live, pytest.mark.django_db]

DATASET = Path(__file__).resolve().parent.parent / "goodlane-dataset"


class TestOpenAIContract:
    def test_real_extraction_returns_schema_valid_output(self):
        extractor = OpenAIExtractor(model=settings.OPENAI_MODEL_EXTRACTION)
        prompt = extractor.resolve_prompt("email")
        document = {
            "channel": "email",
            "sender_name": "Desmond Okafor",
            "sender_email": "desmond@atlanticcarriersinc.com",
            "subject": "Carrier inquiry - load #29372450",
            "body": "Can do. What's the all-in? MC 567234.",
        }

        raw, operation = extractor.extract(prompt, document)
        proposal = parse_extraction(raw)

        inquiry = proposal.inquiries[0]
        assert inquiry.load_reference and "29372450" in inquiry.load_reference
        assert inquiry.mc_number and "567234" in inquiry.mc_number
        assert operation.status == "completed"
        assert operation.estimated_cost is not None
        call = AIProviderCall.objects.get(operation=operation)
        assert call.prompt_tokens and call.completion_tokens

    def test_real_extraction_grounds_evidence_in_the_source(self):
        extractor = OpenAIExtractor(model=settings.OPENAI_MODEL_EXTRACTION)
        prompt = extractor.resolve_prompt("email")
        body = "We can take it but not at $240. Our floor is $280. - Priyanka"
        document = {
            "channel": "email",
            "sender_name": "Priyanka Mehta",
            "sender_email": "priyanka@northboundexpress.com",
            "subject": "Re: Load 29372399",
            "body": body,
        }

        raw, _ = extractor.extract(prompt, document)
        proposal = parse_extraction(raw)
        rates = proposal.inquiries[0].rates
        roles = {str(r.amount): r.role for r in rates}
        assert roles.get("240") == "broker_rate_reference"
        assert roles.get("280") in {"carrier_counteroffer", "carrier_quote"}
        for rate in rates:
            if rate.evidence is not None:
                assert rate.evidence.excerpt in body


class TestDeepgramContract:
    def test_real_transcription_of_a_dataset_wav(self):
        wav = sorted((DATASET / "call_recordings").glob("*.wav"))[0]
        content = wav.read_bytes()
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        key = default_storage.save(f"calls/live-test/{wav.name}", ContentFile(content))
        try:
            recording = make_call_recording(
                storage_key=key, byte_size=len(content), original_filename=wav.name
            )
            transcriber = DeepgramTranscriber(model=settings.DEEPGRAM_MODEL)
            result, operation = transcriber.transcribe(recording)

            assert result["raw_text"].strip()
            assert result["segments"], "utterance segmentation returned nothing"
            first = result["segments"][0]
            assert first.end_seconds > first.start_seconds
            assert operation.status == "completed"
            assert operation.estimated_cost is not None
        finally:
            default_storage.delete(key)
