"""Plain factory functions building minimal valid model instances for tests."""

import hashlib
import uuid
from datetime import UTC, date, datetime

from apps.aiops.models import (
    AIOperation,
    AIProviderCall,
    EvaluationCaseResult,
    EvaluationRun,
)
from apps.comms.models import (
    CallRecording,
    CommunicationEvent,
    EmailContent,
    IngestionJob,
    Transcript,
)
from apps.freight.models import (
    Carrier,
    DatasetSnapshot,
    EquipmentType,
    ImportBatch,
    Lane,
    Load,
    MarketRateHistory,
)
from apps.inquiries.models import EvidenceSpan, ExtractionRun, Inquiry


def _checksum(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def make_snapshot(**kwargs) -> DatasetSnapshot:
    defaults = {
        "name": "Goodlane interview dataset",
        "version": f"v-{uuid.uuid4().hex[:8]}",
        "as_of_at": datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
        "manifest_checksum": _checksum(uuid.uuid4().hex),
        "is_active": False,
    }
    defaults.update(kwargs)
    return DatasetSnapshot.objects.create(**defaults)


def make_import_batch(snapshot=None, **kwargs) -> ImportBatch:
    snapshot = snapshot or make_snapshot()
    defaults = {
        "dataset_snapshot": snapshot,
        "source_type": ImportBatch.SourceType.LOADS,
        "source_filename": "loads.csv",
        "content_checksum": _checksum(uuid.uuid4().hex),
    }
    defaults.update(kwargs)
    return ImportBatch.objects.create(**defaults)


def make_equipment(code="box_truck", display_name="Box Truck", **kwargs) -> EquipmentType:
    return EquipmentType.objects.get_or_create(
        code=code, defaults={"display_name": display_name, **kwargs}
    )[0]


def make_lane(origin="PA", destination="NJ") -> Lane:
    return Lane.objects.get_or_create(origin_state=origin, destination_state=destination)[0]


def make_load(snapshot=None, **kwargs) -> Load:
    snapshot = snapshot or make_snapshot()
    defaults = {
        "dataset_snapshot": snapshot,
        "external_load_id": f"29{uuid.uuid4().int % 10**6:06d}",
        "status": Load.Status.OPEN,
        "origin_city": "Philadelphia",
        "origin_state": "PA",
        "destination_city": "New York",
        "destination_state": "NY",
        "lane": make_lane("PA", "NY"),
        "equipment_type": make_equipment(),
        "equipment_type_raw": "Box Truck",
        "pickup_date": date(2026, 5, 23),
        "pickup_window_status": Load.PickupWindowStatus.MISSING,
    }
    defaults.update(kwargs)
    return Load.objects.create(**defaults)


def make_carrier(snapshot=None, **kwargs) -> Carrier:
    snapshot = snapshot or make_snapshot()
    defaults = {
        "dataset_snapshot": snapshot,
        "source_identifier": f"carrier-{uuid.uuid4().hex[:8]}",
        "company_name": "Atlantic Carriers Inc",
    }
    defaults.update(kwargs)
    return Carrier.objects.create(**defaults)


def make_communication_event(snapshot=None, **kwargs) -> CommunicationEvent:
    snapshot = snapshot or make_snapshot()
    unique = uuid.uuid4().hex
    defaults = {
        "dataset_snapshot": snapshot,
        "channel": "email",
        "origin": "dataset",
        "external_source_id": f"CE{unique[:6]}",
        "stable_evidence_id": f"email:CE{unique[:6]}",
        "occurred_at": datetime(2026, 5, 18, 14, 0, tzinfo=UTC),
        "content_fingerprint": _checksum(unique),
    }
    defaults.update(kwargs)
    return CommunicationEvent.objects.create(**defaults)


def make_email_content(event=None, **kwargs) -> EmailContent:
    event = event or make_communication_event(channel="email")
    defaults = {
        "communication_event": event,
        "sender_email_raw": "desmond@atlanticcarriersinc.com",
        "sender_email_normalized": "desmond@atlanticcarriersinc.com",
        "subject": "Carrier inquiry - load #29372450",
        "body_text": "Can do. What's the all-in?",
    }
    defaults.update(kwargs)
    return EmailContent.objects.create(**defaults)


def make_call_recording(event=None, **kwargs) -> CallRecording:
    event = event or make_communication_event(
        channel="call", stable_evidence_id=f"call:{uuid.uuid4().hex[:10]}.wav"
    )
    defaults = {
        "communication_event": event,
        "original_filename": "call_012_rate_negotiation.wav",
        "storage_key": f"calls/{uuid.uuid4()}.wav",
        "mime_type": "audio/wav",
        "byte_size": 1_024_000,
        "sha256_checksum": _checksum(uuid.uuid4().hex),
        "audio_format": "wav",
    }
    defaults.update(kwargs)
    return CallRecording.objects.create(**defaults)


def make_ingestion_job(event=None, **kwargs) -> IngestionJob:
    event = event or make_communication_event()
    defaults = {
        "origin": event.origin,
        "source_type": event.channel,
        "communication_event": event,
        "content_fingerprint": event.content_fingerprint,
    }
    defaults.update(kwargs)
    return IngestionJob.objects.create(**defaults)


def make_transcript(recording=None, job=None, **kwargs) -> Transcript:
    recording = recording or make_call_recording()
    job = (
        job
        or IngestionJob.objects.filter(communication_event=recording.communication_event).first()
        or make_ingestion_job(event=recording.communication_event)
    )
    defaults = {
        "call_recording": recording,
        "ingestion_job": job,
        "retry_generation": 0,
        "provider": "deepgram",
        "requested_model": "nova-3",
        "raw_text": "Hey Sam, this is Carlos over at Blue Ridge.",
        "normalized_text": "Hey Sam, this is Carlos over at Blue Ridge.",
        "is_current": False,
    }
    defaults.update(kwargs)
    return Transcript.objects.create(**defaults)


def make_extraction_run(event=None, job=None, **kwargs) -> ExtractionRun:
    event = event or make_communication_event()
    job = (
        job
        or IngestionJob.objects.filter(communication_event=event).first()
        or make_ingestion_job(event=event)
    )
    defaults = {
        "communication_event": event,
        "ingestion_job": job,
        "retry_generation": 0,
        "schema_version": "extraction-v1",
        "prompt_name": "extraction-email",
        "prompt_version": "v1",
        "model": "gpt-5.6-luna",
        "is_current": False,
    }
    defaults.update(kwargs)
    return ExtractionRun.objects.create(**defaults)


def make_inquiry(event=None, **kwargs) -> Inquiry:
    event = event or make_communication_event()
    defaults = {
        "communication_event": event,
        "sequence_number": 1,
        "primary_intent": Inquiry.Intent.AVAILABILITY,
    }
    defaults.update(kwargs)
    return Inquiry.objects.create(**defaults)


def make_evidence_span(event=None, **kwargs) -> EvidenceSpan:
    event = event or make_communication_event()
    defaults = {
        "communication_event": event,
        "source_part": EvidenceSpan.SourcePart.BODY,
        "stable_evidence_id": f"email:{event.external_source_id or event.id}#body:0-43",
        "excerpt": "Can do. What's the all-in?",
    }
    defaults.update(kwargs)
    return EvidenceSpan.objects.create(**defaults)


def make_ai_operation(**kwargs) -> AIOperation:
    defaults = {
        "operation_type": AIOperation.OperationType.EXTRACTION,
        "usage_category": AIOperation.UsageCategory.INGESTION,
    }
    defaults.update(kwargs)
    return AIOperation.objects.create(**defaults)


def make_provider_call(operation=None, **kwargs) -> AIProviderCall:
    operation = operation or make_ai_operation()
    defaults = {
        "operation": operation,
        "sequence": 1,
        "provider": AIProviderCall.Provider.OPENAI,
        "operation_name": "extraction-email",
        "model": "gpt-5.6-luna",
    }
    defaults.update(kwargs)
    return AIProviderCall.objects.create(**defaults)


def make_evaluation_run(**kwargs) -> EvaluationRun:
    defaults = {
        "name": f"eval-{uuid.uuid4().hex[:8]}",
        "dataset_version": "v1",
        "dataset_checksum": _checksum("gold-v1"),
        "git_commit_sha": "0" * 40,
    }
    defaults.update(kwargs)
    return EvaluationRun.objects.create(**defaults)


def make_evaluation_case(run=None, **kwargs) -> EvaluationCaseResult:
    run = run or make_evaluation_run()
    defaults = {
        "run": run,
        "case_id": f"case-{uuid.uuid4().hex[:8]}",
        "stable_source_id": "email:CE0058",
        "case_type": EvaluationCaseResult.CaseType.EMAIL,
        "status": EvaluationCaseResult.Status.PASSED,
        "expected_label_ref": "evaluations/gold/emails/CE0058.json",
    }
    defaults.update(kwargs)
    return EvaluationCaseResult.objects.create(**defaults)


def make_market_rate(snapshot=None, **kwargs) -> MarketRateHistory:
    snapshot = snapshot or make_snapshot()
    defaults = {
        "dataset_snapshot": snapshot,
        "week_start": date(2026, 5, 11),
        "lane": make_lane("PA", "NY"),
        "equipment_type": make_equipment(),
        "average_rate_per_mile": "2.94",
        "minimum_rate_per_mile": "2.59",
        "maximum_rate_per_mile": "3.47",
        "load_volume": 3,
    }
    defaults.update(kwargs)
    return MarketRateHistory.objects.create(**defaults)
