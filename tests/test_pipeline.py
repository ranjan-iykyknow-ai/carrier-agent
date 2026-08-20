"""Worker orchestration (spec 3C): claim, branch, reuse, fence, terminal outcomes."""

from dataclasses import dataclass, field
from datetime import date

import pytest

from apps.comms.models import IngestionJob, Transcript
from apps.comms.pipeline import PipelineError, process_job
from apps.inquiries.models import ExtractionRun, Inquiry
from tests.factories import (
    make_call_recording,
    make_carrier,
    make_communication_event,
    make_email_content,
    make_equipment,
    make_ingestion_job,
    make_load,
    make_snapshot,
)
from tests.test_reconciliation import BODY, valid_output


@dataclass
class StubPrompt:
    name: str = "extraction-email"
    version: str = "local-v1"
    source: str = "local_fallback"
    text: str = "extract the inquiry"


@dataclass
class StubExtractor:
    output: dict = field(default_factory=valid_output)
    prompt: StubPrompt = field(default_factory=StubPrompt)
    model: str = "gpt-5.6-luna"
    calls: int = 0
    error: Exception | None = None

    def resolve_prompt(self, channel):
        return self.prompt

    def extract(self, prompt, document):
        self.calls += 1
        if self.error:
            raise self.error
        return self.output, None  # (raw_output, ai_operation)


@dataclass
class StubSegment:
    sequence: int
    speaker_label: str
    start_seconds: str
    end_seconds: str
    text: str
    confidence: str | None = None


@dataclass
class StubTranscriber:
    text: str = "We can do the Philly load. MC 712843."
    calls: int = 0
    error: Exception | None = None

    requested_model: str = "nova-3"
    requested_options: dict = field(
        default_factory=lambda: {"smart_format": True, "utterances": True}
    )

    def transcribe(self, recording):
        self.calls += 1
        if self.error:
            raise self.error
        return {
            "raw_text": self.text,
            "normalized_text": self.text,
            "language": "en",
            "provider_request_id": "dg-req-1",
            "provider_metadata": {"diarizer": "latest-resolved-3"},
            "provider_response": {},
            "segments": [
                StubSegment(1, "0", "0.00", "4.20", "Goodlane dispatch."),
                StubSegment(2, "1", "4.20", "9.00", self.text, confidence="0.95"),
            ],
        }, None


pytestmark = pytest.mark.django_db


@pytest.fixture
def email_scenario():
    snapshot = make_snapshot(is_active=True)
    make_carrier(
        snapshot=snapshot,
        company_name="Blue Ridge Transport LLC",
        mc_number_normalized="712843",
        onboarded=True,
        authority_status="ACTIVE",
        safety_rating="Satisfactory",
        insurance_expiry=date(2027, 1, 1),
    )
    make_load(
        snapshot=snapshot,
        external_load_id="29372450",
        equipment_type=make_equipment("box_truck", "Box Truck"),
    )
    event = make_communication_event(snapshot=snapshot)
    make_email_content(event=event, subject="Re: Load 29372450", body_text=BODY)
    job = make_ingestion_job(event=event)
    return job


class TestEmailPath:
    def test_happy_path_completes_the_job(self, email_scenario):
        extractor = StubExtractor()
        process_job(str(email_scenario.id), extractor=extractor)

        email_scenario.refresh_from_db()
        assert email_scenario.status == "completed"
        assert extractor.calls == 1
        run = ExtractionRun.objects.get()
        assert run.is_current and run.validation_status == "valid"
        assert run.prompt_version == "local-v1"
        assert run.retry_generation == 0
        assert Inquiry.objects.count() == 1

    def test_duplicate_delivery_never_repeats_paid_work(self, email_scenario):
        extractor = StubExtractor()
        process_job(str(email_scenario.id), extractor=extractor)
        process_job(str(email_scenario.id), extractor=extractor)
        assert extractor.calls == 1

    def test_invalid_schema_fails_the_job_without_canonical_records(self, email_scenario):
        extractor = StubExtractor(output={"inquiries": [{"bogus": True}]})
        process_job(str(email_scenario.id), extractor=extractor)

        email_scenario.refresh_from_db()
        assert email_scenario.status == "failed"
        assert email_scenario.last_error_code == "extraction_schema_failure"
        run = ExtractionRun.objects.get()
        assert run.validation_status == "invalid"
        assert not run.is_current
        assert Inquiry.objects.count() == 0

    def test_provider_failure_fails_the_job_safely(self, email_scenario):
        extractor = StubExtractor(
            error=PipelineError("provider_unavailable", "OpenAI unavailable", transient=True)
        )
        process_job(str(email_scenario.id), extractor=extractor)
        email_scenario.refresh_from_db()
        assert email_scenario.status == "failed"
        assert email_scenario.last_error_code == "provider_unavailable"

    def test_retry_reuses_the_compatible_current_extraction(self, email_scenario):
        extractor = StubExtractor()
        process_job(str(email_scenario.id), extractor=extractor)

        IngestionJob.objects.filter(id=email_scenario.id).update(status="queued", retry_count=1)
        process_job(str(email_scenario.id), extractor=extractor)

        assert extractor.calls == 1  # deterministic reuse, no second paid call
        assert ExtractionRun.objects.count() == 1
        email_scenario.refresh_from_db()
        assert email_scenario.status == "completed"

    def test_changed_prompt_version_creates_a_new_attempt(self, email_scenario):
        extractor = StubExtractor()
        process_job(str(email_scenario.id), extractor=extractor)

        IngestionJob.objects.filter(id=email_scenario.id).update(status="queued", retry_count=1)
        changed = StubExtractor(prompt=StubPrompt(version="local-v2"))
        process_job(str(email_scenario.id), extractor=changed)

        assert changed.calls == 1
        assert ExtractionRun.objects.count() == 2
        current = ExtractionRun.objects.get(is_current=True)
        assert current.prompt_version == "local-v2"


@pytest.fixture
def call_scenario():
    snapshot = make_snapshot(is_active=True)
    make_carrier(
        snapshot=snapshot,
        company_name="Blue Ridge Transport LLC",
        mc_number_normalized="712843",
        onboarded=True,
        authority_status="ACTIVE",
        safety_rating="Satisfactory",
        insurance_expiry=date(2027, 1, 1),
    )
    make_load(
        snapshot=snapshot,
        external_load_id="29372450",
        equipment_type=make_equipment("box_truck", "Box Truck"),
    )
    event = make_communication_event(
        snapshot=snapshot,
        channel="call",
        stable_evidence_id="call:call_012_rate_negotiation",
        occurred_at=None,
    )
    recording = make_call_recording(event=event)
    job = make_ingestion_job(event=event, source_type="call")
    return job, recording


def call_output():
    output = valid_output(
        carrier_name_evidence={
            "source_part": "transcript",
            "excerpt": "MC 712843",
            "segment_sequence": 2,
        },
        mc_number_evidence={
            "source_part": "transcript",
            "excerpt": "MC 712843",
            "segment_sequence": 2,
        },
        load_reference_evidence=None,
        equipment_evidence=None,
        availability_evidence=None,
        rates=[],
    )
    return output


class TestCallPath:
    def test_call_transcribes_then_extracts_then_finalizes(self, call_scenario):
        job, recording = call_scenario
        transcriber = StubTranscriber(text="We can do the Philly load. MC 712843.")
        extractor = StubExtractor(output=call_output())
        process_job(str(job.id), transcriber=transcriber, extractor=extractor)

        job.refresh_from_db()
        assert job.status == "completed"
        transcript = Transcript.objects.get()
        assert transcript.is_current
        assert transcript.segments.count() == 2
        run = ExtractionRun.objects.get()
        assert run.transcript_id == transcript.id

    def test_retry_reuses_transcript_and_extraction(self, call_scenario):
        job, recording = call_scenario
        transcriber = StubTranscriber(text="We can do the Philly load. MC 712843.")
        extractor = StubExtractor(output=call_output())
        process_job(str(job.id), transcriber=transcriber, extractor=extractor)

        IngestionJob.objects.filter(id=job.id).update(status="queued", retry_count=1)
        process_job(str(job.id), transcriber=transcriber, extractor=extractor)

        assert transcriber.calls == 1
        assert extractor.calls == 1
        assert Transcript.objects.count() == 1
        job.refresh_from_db()
        assert job.status == "completed"

    def test_transcription_failure_preserves_the_wav_and_fails_the_job(self, call_scenario):
        job, recording = call_scenario
        transcriber = StubTranscriber(
            error=PipelineError("transcription_failed", "Deepgram failure")
        )
        process_job(str(job.id), transcriber=transcriber, extractor=StubExtractor())

        job.refresh_from_db()
        assert job.status == "failed"
        assert job.last_error_code == "transcription_failed"
        assert Transcript.objects.count() == 0
