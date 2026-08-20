"""Langfuse observability (spec 3I).

Langfuse is infrastructure, never the system of record: every call here is
failure-isolated, so a Langfuse outage cannot break ingestion, review,
drafting, or assistant work. Trace ids are deterministic per unit of work
(seeded from the job correlation id and retry generation), which keeps retry
cost and trace shape testable.
"""

import logging
from hashlib import sha256

from django.conf import settings

logger = logging.getLogger(__name__)

_client = None
_initialized = False


def configured() -> bool:
    return bool(
        settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY and settings.LANGFUSE_BASE_URL
    )


def client():
    """Lazy singleton; None when unconfigured or initialization fails."""
    global _client, _initialized
    if not _initialized:
        _initialized = True
        if configured():
            try:
                from langfuse import Langfuse

                _client = Langfuse(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    host=settings.LANGFUSE_BASE_URL,
                )
            except Exception:
                logger.warning("Langfuse client initialization failed; tracing disabled")
                _client = None
    return _client


def set_client(fake) -> None:
    """Test seam: inject a stand-in client."""
    global _client, _initialized
    _client = fake
    _initialized = True


def reset_client() -> None:
    global _client, _initialized
    _client = None
    _initialized = False


def trace_id_for(seed: str) -> str:
    """Deterministic 32-hex trace id — same derivation as Langfuse.create_trace_id."""
    return sha256(seed.encode("utf-8")).digest()[:16].hex()


def trace_url(trace_id: str | None) -> str | None:
    if not trace_id or not settings.LANGFUSE_BASE_URL:
        return None
    return f"{settings.LANGFUSE_BASE_URL.rstrip('/')}/trace/{trace_id}"


def _payload(value):
    return value if settings.LANGFUSE_CAPTURE_PAYLOADS else None


def flush_safely() -> None:
    """Best-effort flush before exposing a trace link; never raises."""
    lf = client()
    if lf is None:
        return
    try:
        lf.flush()
    except Exception:
        logger.warning("Langfuse flush failed")


class _NullRecorder:
    trace_id = None
    observation_id = None

    def finish(self, **kwargs) -> None:
        return


_NULL = _NullRecorder()


class GenerationRecorder:
    """One provider generation observation; finish() is safe to call always."""

    def __init__(self, observation, trace_id):
        self._observation = observation
        self.trace_id = trace_id
        self.observation_id = getattr(observation, "id", None)

    def finish(self, *, output=None, usage=None, cost=None, error=None) -> None:
        try:
            update = {"output": _payload(output)}
            if usage is not None:
                update["usage_details"] = usage
            if cost is not None:
                update["cost_details"] = {"total": float(cost)}
            if error is not None:
                update["level"] = "ERROR"
                update["status_message"] = str(error)
            self._observation.update(**update)
            self._observation.end()
        except Exception:
            logger.warning("Langfuse generation finish failed")


def start_generation(
    *, trace_seed: str, name: str, model: str, input=None, metadata=None
) -> GenerationRecorder | _NullRecorder:
    """Open a generation observation on the deterministic trace; never raises."""
    lf = client()
    if lf is None:
        return _NULL
    try:
        trace_id = trace_id_for(trace_seed)
        observation = lf.start_observation(
            trace_context={"trace_id": trace_id},
            name=name,
            as_type="generation",
            model=model,
            input=_payload(input),
            metadata=metadata,
        )
        return GenerationRecorder(observation, trace_id)
    except Exception:
        logger.warning("Langfuse generation start failed")
        return _NULL
