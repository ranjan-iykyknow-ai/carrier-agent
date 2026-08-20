"""Default provider adapters (spec 3D/3E) with cost accounting.

Every external request or retry is an AIProviderCall row under one AIOperation.
Adapters implement bounded transient retries themselves (one row per attempt)
and translate provider failures into coded PipelineErrors. No payloads, prompts,
or transcripts are written to logs.
"""

import json
import logging
from decimal import Decimal

import httpx
from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone

from apps.aiops import observability
from apps.aiops.models import AIOperation, AIProviderCall
from apps.aiops.prompts import PromptInfo, fallback_prompt

logger = logging.getLogger(__name__)

TRANSIENT_ATTEMPTS = 2

PRICING_VERSION = "pricing-2026-08"
PRICING = {
    "gpt-5.6-luna": {"input_per_million": "0.20", "output_per_million": "1.20"},
    "nova-3": {"per_minute": "0.0043"},
}


def _pricing_for(model: str) -> dict:
    return PRICING.get(model, {})


def _openai_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal | None:
    pricing = _pricing_for(model)
    if "input_per_million" not in pricing:
        return None
    million = Decimal(1_000_000)
    return (
        Decimal(prompt_tokens) * Decimal(pricing["input_per_million"]) / million
        + Decimal(completion_tokens) * Decimal(pricing["output_per_million"]) / million
    ).quantize(Decimal("0.000001"))


def _deepgram_cost(model: str, audio_seconds: Decimal | None) -> Decimal | None:
    pricing = _pricing_for(model)
    if audio_seconds is None or "per_minute" not in pricing:
        return None
    return (audio_seconds / Decimal(60) * Decimal(pricing["per_minute"])).quantize(
        Decimal("0.000001")
    )


def strict_schema(model_class) -> dict:
    """OpenAI strict Structured Outputs schema from a Pydantic model.

    Strict mode requires every property listed in `required` and
    additionalProperties=false on every object, including fields our validator
    treats as optional-with-default; the model must emit them explicitly.
    """

    def tighten(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"].keys())
                node["additionalProperties"] = False
            # Pydantic's Decimal pattern uses lookarounds OpenAI rejects; our
            # own parse_extraction re-validates numerics after the call.
            node.pop("pattern", None)
            for value in node.values():
                tighten(value)
        elif isinstance(node, list):
            for value in node:
                tighten(value)

    schema = model_class.model_json_schema()
    tighten(schema)
    return schema


class ProviderFailure(Exception):
    def __init__(self, code: str, summary: str, *, transient: bool):
        super().__init__(summary)
        self.code = code
        self.summary = summary
        self.transient = transient


class OpenAIExtractor:
    """Strict structured-output extraction through the OpenAI Chat Completions API."""

    def __init__(self, model: str, client=None):
        self.model = model
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                timeout=settings.PROVIDER_TIMEOUT_SECONDS,
                max_retries=0,  # retries are explicit AIProviderCall rows
            )
        return self._client

    def resolve_prompt(self, channel: str) -> PromptInfo:
        """Langfuse `production` label first (its SDK cache covers transient
        outages), then the bundled emergency fallback (spec 3I)."""
        fallback = fallback_prompt(channel)
        lf = observability.client()
        if lf is None:
            return fallback
        try:
            resolved = lf.get_prompt(fallback.name, label="production", cache_ttl_seconds=300)
        except Exception:
            logger.warning("prompt %s resolved from the bundled fallback", fallback.name)
            return fallback
        if getattr(resolved, "is_fallback", False):
            return fallback
        text = getattr(resolved, "prompt", None)
        if not isinstance(text, str) or not text.strip():
            return fallback
        return PromptInfo(
            name=fallback.name,
            version=str(resolved.version),
            source="langfuse",
            text=text,
        )

    def extract(self, prompt: PromptInfo, document: dict, *, correlation_id=None, trace_seed=None):
        from apps.comms.pipeline import PipelineError
        from apps.inquiries.extraction_schema import ExtractionProposal

        operation = AIOperation.objects.create(
            operation_type=AIOperation.OperationType.EXTRACTION,
            usage_category=AIOperation.UsageCategory.INGESTION,
            correlation_id=correlation_id,
            started_at=timezone.now(),
        )
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "extraction_proposal",
                "strict": True,
                "schema": strict_schema(ExtractionProposal),
            },
        }
        messages = [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": json.dumps(document)},
        ]

        last_failure = None
        for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
            recorder = observability.start_generation(
                trace_seed=trace_seed or f"operation:{operation.id}",
                name=f"extraction:{prompt.name}",
                model=self.model,
                input=document,
                metadata={
                    "prompt_version": prompt.version,
                    "prompt_source": prompt.source,
                    "attempt": attempt,
                },
            )
            call = AIProviderCall.objects.create(
                operation=operation,
                sequence=attempt,
                provider=AIProviderCall.Provider.OPENAI,
                operation_name=prompt.name,
                model=self.model,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                prompt_source=prompt.source,
                langfuse_trace_id=recorder.trace_id,
                langfuse_observation_id=recorder.observation_id,
                attempt_number=attempt,
                started_at=timezone.now(),
                pricing_version=PRICING_VERSION,
                pricing_snapshot=_pricing_for(self.model),
            )
            try:
                raw, usage = self._request(messages, response_format)
            except ProviderFailure as failure:
                recorder.finish(error=failure.code)
                self._finish_call(call, status="failed", error_code=failure.code)
                last_failure = failure
                if failure.transient and attempt < TRANSIENT_ATTEMPTS:
                    continue
                self._finish_operation(operation, status="failed", error_code=failure.code)
                raise PipelineError(
                    failure.code, failure.summary, transient=failure.transient
                ) from failure
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            cost = _openai_cost(self.model, prompt_tokens or 0, completion_tokens or 0)
            recorder.finish(
                output=raw,
                usage={"input": prompt_tokens or 0, "output": completion_tokens or 0},
                cost=cost,
            )
            self._finish_call(
                call,
                status="completed",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost=cost,
            )
            self._finish_operation(
                operation,
                status="completed",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost=cost,
            )
            return raw, operation
        raise PipelineError(last_failure.code, last_failure.summary)  # pragma: no cover

    def _request(self, messages, response_format):
        import openai

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format=response_format,
                reasoning_effort=settings.OPENAI_REASONING_EFFORT_EXTRACTION,
            )
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise ProviderFailure(
                "provider_timeout", "OpenAI did not respond in time.", transient=True
            ) from exc
        except openai.RateLimitError as exc:
            raise ProviderFailure(
                "provider_rate_limited", "OpenAI rate limit reached.", transient=True
            ) from exc
        except openai.InternalServerError as exc:
            raise ProviderFailure(
                "provider_unavailable", "OpenAI is temporarily unavailable.", transient=True
            ) from exc
        except openai.APIStatusError as exc:
            raise ProviderFailure(
                "provider_error", "OpenAI rejected the extraction request.", transient=False
            ) from exc

        choice = response.choices[0]
        content = choice.message.content
        if not content:
            raise ProviderFailure(
                "provider_error", "OpenAI returned an empty extraction.", transient=False
            )
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderFailure(
                "provider_error", "OpenAI returned non-JSON output.", transient=False
            ) from exc
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
            "completion_tokens": getattr(response.usage, "completion_tokens", None),
        }
        return raw, usage

    @staticmethod
    def _finish_call(call, *, status, error_code=None, **fields):
        for key, value in fields.items():
            setattr(call, key, value)
        call.status = status
        call.error_code = error_code
        call.completed_at = timezone.now()
        if call.started_at:
            call.latency_ms = int((call.completed_at - call.started_at).total_seconds() * 1000)
        call.save()

    @staticmethod
    def _finish_operation(operation, *, status, error_code=None, **fields):
        for key, value in fields.items():
            setattr(operation, key, value)
        if fields.get("prompt_tokens") is not None:
            operation.total_tokens = (fields.get("prompt_tokens") or 0) + (
                fields.get("completion_tokens") or 0
            )
        operation.provider_call_count = operation.provider_calls.count()
        operation.status = status
        operation.last_error_code = error_code
        operation.completed_at = timezone.now()
        if operation.started_at:
            operation.latency_ms = int(
                (operation.completed_at - operation.started_at).total_seconds() * 1000
            )
        operation.save()


class DeepgramTranscriber:
    """Prerecorded transcription through Deepgram's REST API (utterances on)."""

    def __init__(self, model: str, http_client=None):
        self.model = model
        self._http = http_client
        self.requested_options = {
            "smart_format": True,
            "utterances": True,
            "diarize_model": "latest",
        }

    @property
    def requested_model(self) -> str:
        return self.model

    @property
    def http(self):
        if self._http is None:
            self._http = httpx.Client(timeout=settings.PROVIDER_TIMEOUT_SECONDS)
        return self._http

    def transcribe(self, recording, *, correlation_id=None, trace_seed=None):
        from apps.comms.pipeline import PipelineError

        operation = AIOperation.objects.create(
            operation_type=AIOperation.OperationType.TRANSCRIPTION,
            usage_category=AIOperation.UsageCategory.INGESTION,
            correlation_id=correlation_id,
            started_at=timezone.now(),
        )
        with default_storage.open(recording.storage_key, "rb") as stored:
            audio = stored.read()

        params = {
            "model": self.model,
            **{k: str(v).lower() for k, v in self.requested_options.items()},
        }
        last_failure = None
        for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
            recorder = observability.start_generation(
                trace_seed=trace_seed or f"operation:{operation.id}",
                name="transcription",
                model=self.model,
                metadata={"attempt": attempt, **self.requested_options},
            )
            call = AIProviderCall.objects.create(
                operation=operation,
                sequence=attempt,
                provider=AIProviderCall.Provider.DEEPGRAM,
                operation_name="transcription",
                model=self.model,
                langfuse_trace_id=recorder.trace_id,
                langfuse_observation_id=recorder.observation_id,
                attempt_number=attempt,
                started_at=timezone.now(),
                audio_seconds=recording.duration_seconds,
                pricing_version=PRICING_VERSION,
                pricing_snapshot=_pricing_for(self.model),
            )
            try:
                payload = self._request(audio, params, recording.mime_type)
            except ProviderFailure as failure:
                recorder.finish(error=failure.code)
                self._finish(call, operation, status="failed", error_code=failure.code)
                last_failure = failure
                if failure.transient and attempt < TRANSIENT_ATTEMPTS:
                    continue
                raise PipelineError(
                    failure.code, failure.summary, transient=failure.transient
                ) from failure
            try:
                result = self._parse(payload)
            except PipelineError as exc:
                recorder.finish(error=exc.code)
                raise
            cost = _deepgram_cost(self.model, result["_duration"])
            recorder.finish(
                output=result.get("normalized_text"),
                usage={"audio_seconds": int(result["_duration"] or 0)},
                cost=cost,
            )
            call.audio_seconds = result["_duration"]
            self._finish(call, operation, status="completed", estimated_cost=cost)
            operation.audio_seconds = result["_duration"]
            operation.estimated_cost = cost
            operation.save(update_fields=["audio_seconds", "estimated_cost", "updated_at"])
            result.pop("_duration")
            return result, operation
        from apps.comms.pipeline import PipelineError as PE  # pragma: no cover

        raise PE(last_failure.code, last_failure.summary)  # pragma: no cover

    def _request(self, audio: bytes, params: dict, mime_type: str) -> dict:
        try:
            response = self.http.post(
                "https://api.deepgram.com/v1/listen",
                params=params,
                content=audio,
                headers={
                    "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
                    "Content-Type": mime_type or "audio/wav",
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderFailure(
                "provider_timeout", "Deepgram did not respond in time.", transient=True
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure(
                "provider_unavailable", "Deepgram could not be reached.", transient=True
            ) from exc
        if response.status_code == 429:
            raise ProviderFailure(
                "provider_rate_limited", "Deepgram rate limit reached.", transient=True
            )
        if response.status_code >= 500:
            raise ProviderFailure(
                "provider_unavailable", "Deepgram is temporarily unavailable.", transient=True
            )
        if response.status_code >= 400:
            raise ProviderFailure(
                "transcription_failed", "Deepgram rejected the audio.", transient=False
            )
        return response.json()

    @staticmethod
    def _parse(payload: dict) -> dict:
        from apps.comms.pipeline import PipelineError

        try:
            channel = payload["results"]["channels"][0]["alternatives"][0]
            raw_text = channel["transcript"]
            utterances = payload["results"].get("utterances") or []
            duration = Decimal(str(payload["metadata"]["duration"]))
            request_id = payload["metadata"].get("request_id")
            models_info = payload["metadata"].get("model_info", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise PipelineError(
                "transcription_failed", "Deepgram returned an unsupported response shape."
            ) from exc

        class Segment:
            def __init__(self, sequence, utterance):
                self.sequence = sequence
                self.speaker_label = str(utterance.get("speaker", ""))
                self.start_seconds = Decimal(str(utterance["start"])).quantize(Decimal("0.01"))
                self.end_seconds = Decimal(str(utterance["end"])).quantize(Decimal("0.01"))
                self.text = utterance["transcript"]
                confidence = utterance.get("confidence")
                self.confidence = (
                    Decimal(str(confidence)).quantize(Decimal("0.0001"))
                    if confidence is not None
                    else None
                )

        return {
            "raw_text": raw_text,
            "normalized_text": raw_text,
            "language": None,
            "provider_request_id": request_id,
            "provider_metadata": {"model_info": models_info},
            "provider_response": {},
            "segments": [Segment(i, u) for i, u in enumerate(utterances, start=1)],
            "_duration": duration,
        }

    @staticmethod
    def _finish(call, operation, *, status, error_code=None, estimated_cost=None):
        call.status = status
        call.error_code = error_code
        call.estimated_cost = estimated_cost
        call.completed_at = timezone.now()
        if call.started_at:
            call.latency_ms = int((call.completed_at - call.started_at).total_seconds() * 1000)
        call.save()
        operation.status = status
        operation.last_error_code = error_code
        operation.provider_call_count = operation.provider_calls.count()
        operation.completed_at = timezone.now()
        if operation.started_at:
            operation.latency_ms = int(
                (operation.completed_at - operation.started_at).total_seconds() * 1000
            )
        operation.save()
