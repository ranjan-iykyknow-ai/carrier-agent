"""Langfuse observability (spec 3I): failure-isolated tracing and prompt resolution."""

import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from apps.aiops import observability
from apps.aiops.models import AIOperation, AIProviderCall
from apps.aiops.providers import OpenAIExtractor

pytestmark = pytest.mark.django_db


class FakeObservation:
    def __init__(self, trace_id):
        self.id = "obs-0001"
        self.trace_id = trace_id
        self.updates = []
        self.ended = False

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def end(self):
        self.ended = True


class FakeLangfuse:
    def __init__(self, prompt=None, prompt_error=None, start_error=None):
        self.prompt = prompt
        self.prompt_error = prompt_error
        self.start_error = start_error
        self.started = []
        self.observations = []
        self.flushed = 0

    def start_observation(self, **kwargs):
        if self.start_error:
            raise self.start_error
        self.started.append(kwargs)
        observation = FakeObservation(kwargs.get("trace_context", {}).get("trace_id"))
        self.observations.append(observation)
        return observation

    def get_prompt(self, name, **kwargs):
        if self.prompt_error:
            raise self.prompt_error
        return self.prompt

    def flush(self):
        self.flushed += 1


@pytest.fixture(autouse=True)
def _reset_observability():
    yield
    observability.reset_client()


class FakeOpenAI:
    """Just enough of the OpenAI client for one successful structured call."""

    def __init__(self, payload):
        message = SimpleNamespace(content=json.dumps(payload))
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
            id="resp-1",
        )
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))


class TestTraceIdentity:
    def test_trace_id_is_deterministic(self):
        seed = "job:abc:gen:0"
        expected = sha256(seed.encode()).digest()[:16].hex()
        assert observability.trace_id_for(seed) == expected
        assert observability.trace_id_for(seed) == observability.trace_id_for(seed)


class TestGenerationRecorder:
    def test_records_ids_usage_and_cost(self):
        fake = FakeLangfuse()
        observability.set_client(fake)

        recorder = observability.start_generation(
            trace_seed="job:x:gen:0", name="extraction", model="gpt-5.6-luna"
        )
        recorder.finish(output={"ok": True}, usage={"input": 10, "output": 5}, cost=0.001)

        observation = fake.observations[0]
        assert recorder.trace_id == observability.trace_id_for("job:x:gen:0")
        assert recorder.observation_id == "obs-0001"
        assert observation.ended
        update = observation.updates[-1]
        assert update["usage_details"] == {"input": 10, "output": 5}
        assert update["cost_details"] == {"total": 0.001}

    def test_payload_capture_can_be_disabled(self, settings):
        settings.LANGFUSE_CAPTURE_PAYLOADS = False
        fake = FakeLangfuse()
        observability.set_client(fake)

        recorder = observability.start_generation(
            trace_seed="s", name="extraction", model="m", input={"secret": "body"}
        )
        recorder.finish(output={"secret": "result"})

        assert fake.started[0].get("input") is None
        assert fake.observations[0].updates[-1].get("output") is None

    def test_survives_client_errors(self):
        observability.set_client(FakeLangfuse(start_error=RuntimeError("down")))

        recorder = observability.start_generation(trace_seed="s", name="n", model="m")
        recorder.finish(output="x")  # must not raise

        assert recorder.trace_id is None

    def test_disabled_without_configuration(self, settings):
        settings.LANGFUSE_PUBLIC_KEY = None
        observability.reset_client()

        assert observability.client() is None
        recorder = observability.start_generation(trace_seed="s", name="n", model="m")
        assert recorder.trace_id is None


class TestPromptResolution:
    def test_prefers_langfuse_production_prompt(self):
        observability.set_client(
            FakeLangfuse(prompt=SimpleNamespace(prompt="You extract things.", version=7, name="x"))
        )
        extractor = OpenAIExtractor(model="gpt-5.6-luna", client=FakeOpenAI({}))

        info = extractor.resolve_prompt("email")

        assert info.source == "langfuse"
        assert info.version == "7"
        assert info.text == "You extract things."
        assert info.name == "extraction-email"

    def test_falls_back_when_langfuse_fails(self):
        observability.set_client(FakeLangfuse(prompt_error=RuntimeError("api down")))
        extractor = OpenAIExtractor(model="gpt-5.6-luna", client=FakeOpenAI({}))

        info = extractor.resolve_prompt("call")

        assert info.source == "local_fallback"
        assert info.name == "extraction-call"

    def test_falls_back_when_unconfigured(self, settings):
        settings.LANGFUSE_PUBLIC_KEY = None
        observability.reset_client()
        extractor = OpenAIExtractor(model="gpt-5.6-luna", client=FakeOpenAI({}))

        assert extractor.resolve_prompt("email").source == "local_fallback"


class TestExtractionTracing:
    def test_extract_links_trace_ids_and_correlation(self):
        fake = FakeLangfuse()
        observability.set_client(fake)
        extractor = OpenAIExtractor(model="gpt-5.6-luna", client=FakeOpenAI({"inquiries": []}))
        prompt = extractor.resolve_prompt("email")

        _, operation = extractor.extract(
            prompt,
            {"channel": "email"},
            correlation_id="11111111-1111-1111-1111-111111111111",
            trace_seed="job:demo:gen:0",
        )

        operation.refresh_from_db()
        assert str(operation.correlation_id) == "11111111-1111-1111-1111-111111111111"
        call = AIProviderCall.objects.get(operation=operation)
        assert call.langfuse_trace_id == observability.trace_id_for("job:demo:gen:0")
        assert call.langfuse_observation_id == "obs-0001"
        # The generation carries real usage and cost for Langfuse analysis.
        update = fake.observations[0].updates[-1]
        assert update["usage_details"] == {"input": 100, "output": 20}
        assert update["cost_details"]["total"] > 0

    def test_extract_without_langfuse_still_completes(self, settings):
        settings.LANGFUSE_PUBLIC_KEY = None
        observability.reset_client()
        extractor = OpenAIExtractor(model="gpt-5.6-luna", client=FakeOpenAI({"inquiries": []}))
        prompt = extractor.resolve_prompt("email")

        _, operation = extractor.extract(prompt, {"channel": "email"})

        assert operation.status == AIOperation.Status.COMPLETED


class TestOperationLinkage:
    """Evidence rows must point at the AIOperation that produced them (spec 3I)."""

    def test_pipeline_links_extraction_and_transcript_to_operations(self):
        from datetime import UTC, datetime

        from apps.comms.pipeline import process_job
        from apps.inquiries.models import ExtractionRun
        from tests.factories import (
            make_ai_operation,
            make_call_recording,
            make_communication_event,
            make_ingestion_job,
            make_snapshot,
        )
        from tests.test_pipeline import StubExtractor, StubTranscriber

        snapshot = make_snapshot(is_active=True)
        event = make_communication_event(
            snapshot=snapshot,
            channel="call",
            stable_evidence_id="call:obs_link.wav",
            occurred_at=datetime(2026, 5, 18, 14, 0, tzinfo=UTC),
        )
        make_call_recording(event=event)
        job = make_ingestion_job(event=event, status="queued")

        transcription_op = make_ai_operation(operation_type="transcription")
        extraction_op = make_ai_operation(operation_type="extraction")

        extractor = StubExtractor()
        extractor.output = {
            "inquiries": [
                dict(
                    extractor.output["inquiries"][0],
                    load_reference=None,
                    load_reference_evidence=None,
                    mc_number=None,
                    mc_number_evidence=None,
                )
            ]
        }

        class LinkedExtractor(StubExtractor):
            def extract(self, prompt, document, **kwargs):
                raw, _ = super().extract(prompt, document, **kwargs)
                return raw, extraction_op

        class LinkedTranscriber(StubTranscriber):
            def transcribe(self, recording, **kwargs):
                result, _ = super().transcribe(recording, **kwargs)
                return result, transcription_op

        linked = LinkedExtractor()
        linked.output = extractor.output
        process_job(str(job.id), extractor=linked, transcriber=LinkedTranscriber())

        run = ExtractionRun.objects.get(communication_event=event)
        assert run.ai_operation_id == extraction_op.id
        assert run.transcript.ai_operation_id == transcription_op.id


class TestTraceLinks:
    def test_review_page_links_the_langfuse_trace(self, client, settings):
        from django.contrib.auth.models import User

        from tests.factories import (
            make_ai_operation,
            make_communication_event,
            make_email_content,
            make_extraction_run,
            make_ingestion_job,
            make_inquiry,
            make_provider_call,
            make_snapshot,
        )

        settings.LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
        user = User.objects.create_user("broker@goodlanelogistics.com", password="x")
        client.force_login(user)

        snapshot = make_snapshot(is_active=True)
        event = make_communication_event(snapshot=snapshot)
        make_email_content(event=event)
        make_ingestion_job(event=event, status="completed")
        operation = make_ai_operation(operation_type="extraction")
        make_provider_call(operation=operation, langfuse_trace_id="deadbeef" * 4)
        run = make_extraction_run(event=event, ai_operation=operation, is_current=True)
        inquiry = make_inquiry(event=event, current_extraction=run)

        from django.urls import reverse

        response = client.get(reverse("inquiry_review", args=[inquiry.pk]))

        assert ("https://cloud.langfuse.com/trace/" + "deadbeef" * 4) in (response.content.decode())


class TestPublishPrompts:
    def test_publishes_bundled_prompts_with_production_label(self):
        from io import StringIO

        from django.core.management import call_command

        class PublishingFake(FakeLangfuse):
            def __init__(self):
                super().__init__(prompt_error=RuntimeError("not found"))
                self.created = []

            def create_prompt(self, **kwargs):
                self.created.append(kwargs)

        fake = PublishingFake()
        observability.set_client(fake)
        out = StringIO()

        call_command("publish_prompts", stdout=out)

        names = {entry["name"] for entry in fake.created}
        assert names == {
            "extraction-email",
            "extraction-call",
            "draft-response",
            "assistant-turn",
        }
        assert all(entry["labels"] == ["production"] for entry in fake.created)
        assert all(entry["type"] == "text" for entry in fake.created)

    def test_skips_prompts_already_current(self):
        from io import StringIO
        from types import SimpleNamespace

        from django.core.management import call_command

        from apps.aiops.prompts import fallback_prompt

        class CurrentFake(FakeLangfuse):
            def __init__(self):
                super().__init__()
                self.created = []

            def get_prompt(self, name, **kwargs):
                from apps.aiops.prompts import assistant_prompt, draft_prompt

                texts = {
                    "extraction-email": fallback_prompt("email").text,
                    "extraction-call": fallback_prompt("call").text,
                    "draft-response": draft_prompt().text,
                    "assistant-turn": assistant_prompt().text,
                }
                return SimpleNamespace(prompt=texts[name], version=3)

            def create_prompt(self, **kwargs):
                self.created.append(kwargs)

        fake = CurrentFake()
        observability.set_client(fake)
        out = StringIO()

        call_command("publish_prompts", stdout=out)

        assert fake.created == []
        assert "up to date" in out.getvalue()
