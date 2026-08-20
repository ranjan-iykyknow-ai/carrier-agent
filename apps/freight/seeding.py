"""Dataset seed engine (spec 3A.2).

Preflight is read-only and strict about structure; import work runs in bounded
per-record transactions through the shared ingestion contract; activation is a
short final transaction; dispatch is an explicit last step. Intentional
business uncertainty (missing MCs, blank weights, contradictory annotations)
is accepted data, never an import failure.
"""

import csv
import hashlib
import io
import json
import logging
import re
import unicodedata
import wave
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection, transaction
from django.utils import timezone

from apps.comms.fingerprints import audio_fingerprint, canonical_email_address, email_fingerprint
from apps.comms.ingestion import (
    IngestionSubmissionService,
    SubmitCallCommand,
    SubmitEmailCommand,
)
from apps.comms.models import CallRecording, CommunicationEvent, IngestionJob
from apps.comms.recovery import dispatch_queued_jobs, sweep_stale_jobs
from apps.freight.models import (
    Carrier,
    CarrierContact,
    CarrierEquipment,
    CarrierPreferredLane,
    DatasetSnapshot,
    EquipmentAlias,
    EquipmentType,
    ImportBatch,
    Lane,
    Load,
    MarketRateHistory,
)

logger = logging.getLogger(__name__)

# Stable application-owned 64-bit advisory lock key for the seed operation.
SEED_ADVISORY_LOCK_KEY = 0x600D_1A4E_5EED_0001

EQUIPMENT_VOCABULARY = {
    "Box Truck": "box_truck",
    "Sprinter Van": "sprinter_van",
    "Flatbed": "flatbed",
    "Refrigerated": "refrigerated",
}

LOAD_COLUMNS = {
    "load_id",
    "origin_city",
    "origin_state",
    "origin_zip",
    "destination_city",
    "destination_state",
    "destination_zip",
    "distance_miles",
    "equipment_type",
    "weight_lbs",
    "pickup_date",
    "pickup_window",
    "delivery_date",
    "offered_rate_usd",
    "status",
    "shipper_name",
    "internal_notes",
}
RATE_COLUMNS = {
    "week_start",
    "origin_state",
    "destination_state",
    "equipment_type",
    "avg_rate_per_mile",
    "min_rate_per_mile",
    "max_rate_per_mile",
    "load_volume",
}
EMAIL_KEYS = {"email_id", "timestamp", "from_email", "to_email", "subject", "body"}

_WINDOW_RANGE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
_WINDOW_SINGLE = re.compile(r"^(\d{1,2}):(\d{2})$")


class SeedError(Exception):
    """Safe, structural seed failure; the command exits non-zero."""


@dataclass
class CallSource:
    filename: str
    stem: str
    path: Path
    byte_size: int
    checksum: str
    fingerprint: str
    duration_seconds: Decimal
    sample_rate: int


@dataclass
class Preflight:
    version: str
    manifest_checksum: str
    source_checksums: dict
    loads: list
    carriers: list
    rates: list
    emails: list
    calls: list


@dataclass
class BatchOutcome:
    source_type: str
    status: str
    records_seen: int = 0
    records_created: int = 0
    records_existing: int = 0
    records_failed: int = 0


@dataclass
class SeedSummary:
    snapshot_id: str | None = None
    snapshot_version: str | None = None
    manifest_checksum: str | None = None
    activated: bool = False
    batch_results: list = field(default_factory=list)
    audio_copied: int = 0
    audio_existing: int = 0
    jobs_created: int = 0
    jobs_existing: int = 0
    jobs_dispatched: int = 0
    jobs_left_queued: int = 0
    job_status_counts: dict = field(default_factory=dict)
    stale_jobs_swept: int = 0
    processing_health: str = "processing"
    safe_errors: list = field(default_factory=list)

    def lines(self):
        yield f"snapshot: {self.snapshot_id} version={self.snapshot_version}"
        yield f"manifest: {self.manifest_checksum}"
        yield f"activated: {self.activated}"
        for b in self.batch_results:
            yield (
                f"  {b.source_type}: {b.status} seen={b.records_seen} "
                f"created={b.records_created} existing={b.records_existing} "
                f"failed={b.records_failed}"
            )
        yield f"audio: copied={self.audio_copied} existing={self.audio_existing}"
        yield f"jobs: created={self.jobs_created} existing={self.jobs_existing}"
        yield (f"dispatch: dispatched={self.jobs_dispatched} left_queued={self.jobs_left_queued}")
        yield f"job status counts: {self.job_status_counts}"
        yield f"stale jobs swept: {self.stale_jobs_swept}"
        yield f"processing health: {self.processing_health}"
        for err in self.safe_errors[:20]:
            yield f"  error: {err}"


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_aware_timestamp(raw: str, email_id: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SeedError(f"email {email_id}: unparseable timestamp") from exc
    if parsed.tzinfo is None:
        raise SeedError(
            f"email {email_id}: timezone-naive timestamp rejected; an explicit "
            "Z or numeric UTC offset is required"
        )
    return parsed


def run_preflight(root: Path) -> Preflight:
    root = root.resolve()
    if not root.is_dir():
        raise SeedError(f"dataset path is not a directory: {root}")

    required = ["loads.csv", "carrier_profiles.json", "rate_history.csv", "carrier_emails.json"]
    for name in required:
        if not (root / name).is_file():
            raise SeedError(f"required source missing: {name}")
    call_dir = root / "call_recordings"
    if not call_dir.is_dir():
        raise SeedError("required source missing: call_recordings/")

    # loads.csv
    with (root / "loads.csv").open(newline="") as f:
        reader = csv.DictReader(f)
        if not LOAD_COLUMNS.issubset(set(reader.fieldnames or [])):
            raise SeedError("loads.csv is missing required columns")
        loads = list(reader)
    load_ids = [row["load_id"] for row in loads]
    if len(load_ids) != len(set(load_ids)):
        raise SeedError("loads.csv contains duplicate load_id values")

    # carrier_profiles.json
    try:
        carriers = json.loads((root / "carrier_profiles.json").read_text())
    except json.JSONDecodeError as exc:
        raise SeedError("carrier_profiles.json is not valid JSON") from exc
    if not isinstance(carriers, list) or not all(
        isinstance(c, dict) and "company_name" in c for c in carriers
    ):
        raise SeedError("carrier_profiles.json has an invalid top-level shape")

    # rate_history.csv
    with (root / "rate_history.csv").open(newline="") as f:
        reader = csv.DictReader(f)
        if not RATE_COLUMNS.issubset(set(reader.fieldnames or [])):
            raise SeedError("rate_history.csv is missing required columns")
        rates = list(reader)

    # carrier_emails.json
    try:
        emails = json.loads((root / "carrier_emails.json").read_text())
    except json.JSONDecodeError as exc:
        raise SeedError("carrier_emails.json is not valid JSON") from exc
    if not isinstance(emails, list) or not all(
        isinstance(e, dict) and EMAIL_KEYS.issubset(e.keys()) for e in emails
    ):
        raise SeedError("carrier_emails.json has an invalid shape or missing keys")
    email_ids = [e["email_id"] for e in emails]
    if len(email_ids) != len(set(email_ids)):
        raise SeedError("carrier_emails.json contains duplicate email_id values")
    fingerprints: dict[str, str] = {}
    for record in emails:
        _parse_aware_timestamp(record["timestamp"], record["email_id"])
        fp = email_fingerprint(record["from_email"], record["subject"], record["body"])
        if fp in fingerprints:
            raise SeedError(
                "duplicate content fingerprint between dataset emails "
                f"{fingerprints[fp]} and {record['email_id']}"
            )
        fingerprints[fp] = record["email_id"]

    # call_recordings/
    calls: list[CallSource] = []
    call_fingerprints: dict[str, str] = {}
    max_bytes = settings.SEED_MAX_WAV_BYTES
    max_seconds = settings.SEED_MAX_WAV_SECONDS
    for path in sorted(call_dir.glob("*.wav")):
        data = path.read_bytes()
        if not data:
            raise SeedError(f"WAV is empty: {path.name}")
        if len(data) > max_bytes:
            raise SeedError(f"WAV exceeds the configured size limit: {path.name}")
        try:
            with wave.open(io.BytesIO(data)) as w:
                frames, rate = w.getnframes(), w.getframerate()
        except (wave.Error, EOFError) as exc:
            raise SeedError(f"invalid WAV container: {path.name}") from exc
        if frames == 0 or rate <= 0:
            raise SeedError(f"WAV has no audio frames: {path.name}")
        duration = Decimal(frames) / Decimal(rate)
        if duration > max_seconds:
            raise SeedError(f"WAV exceeds the configured duration limit: {path.name}")
        fp = audio_fingerprint(data)
        if fp in call_fingerprints:
            raise SeedError(
                "duplicate content fingerprint between dataset recordings "
                f"{call_fingerprints[fp]} and {path.name}"
            )
        call_fingerprints[fp] = path.name
        calls.append(
            CallSource(
                filename=path.name,
                stem=path.stem,
                path=path,
                byte_size=len(data),
                checksum=hashlib.sha256(data).hexdigest(),
                fingerprint=fp,
                duration_seconds=duration.quantize(Decimal("0.01")),
                sample_rate=rate,
            )
        )
    if not calls:
        raise SeedError("call_recordings/ contains no WAV files")
    stems = [c.stem for c in calls]
    if len(stems) != len(set(stems)):
        raise SeedError("call_recordings/ contains duplicate filename stems")

    # Manifest: dataset version + sorted relative paths + per-source checksums.
    source_checksums = {name: _sha256_file(root / name) for name in required}
    call_entry = hashlib.sha256(
        json.dumps([[c.filename, c.checksum] for c in calls]).encode()
    ).hexdigest()
    source_checksums["call_recordings"] = call_entry
    version = settings.DATASET_VERSION
    manifest = json.dumps(
        {"version": version, "sources": sorted(source_checksums.items())},
        sort_keys=True,
    )
    manifest_checksum = hashlib.sha256(manifest.encode()).hexdigest()

    # The demo clock and timezone must resolve during preflight.
    ZoneInfo(settings.DISPLAY_TIMEZONE)
    datetime.strptime(settings.DEMO_AS_OF_DATE, "%Y-%m-%d")

    return Preflight(
        version=version,
        manifest_checksum=manifest_checksum,
        source_checksums=source_checksums,
        loads=loads,
        carriers=carriers,
        rates=rates,
        emails=emails,
        calls=calls,
    )


# --------------------------------------------------------------------------
# Import helpers
# --------------------------------------------------------------------------


def _digits(value) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"\D", "", str(value))
    return normalized or None


def _carrier_source_identifier(row: dict, ordinal: int) -> str:
    mc = _digits(row.get("mc_number"))
    if mc:
        return f"mc:{mc}"
    dot = _digits(row.get("dot_number"))
    if dot:
        return f"dot:{dot}"
    email = row.get("email")
    if email:
        return f"email:{canonical_email_address(email)}"
    phone = _digits(row.get("phone"))
    if phone:
        return f"phone:{phone}"
    canonical = unicodedata.normalize("NFKC", json.dumps(row, sort_keys=True))
    digest = hashlib.sha256(f"{canonical}\0{ordinal}".encode()).hexdigest()[:24]
    return f"rowhash:{digest}"


def _optional_decimal(value):
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _optional_int(value):
    if value in (None, ""):
        return None
    return int(str(value))


def _parse_pickup_window(raw: str, pickup_date, tz):
    raw = (raw or "").strip()
    if not raw:
        return Load.PickupWindowStatus.MISSING, None, None

    def at(hour, minute):
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("out of range")
        return datetime.combine(pickup_date, time(hour, minute), tzinfo=tz)

    match = _WINDOW_RANGE.match(raw)
    if match:
        h1, m1, h2, m2 = (int(g) for g in match.groups())
        try:
            start, end = at(h1, m1), at(h2, m2)
        except ValueError:
            return Load.PickupWindowStatus.INVALID, None, None
        if end < start:
            return Load.PickupWindowStatus.INVALID, None, None
        return Load.PickupWindowStatus.RANGE, start, end
    match = _WINDOW_SINGLE.match(raw)
    if match:
        try:
            start = at(int(match.group(1)), int(match.group(2)))
        except ValueError:
            return Load.PickupWindowStatus.INVALID, None, None
        return Load.PickupWindowStatus.SINGLE_TIME, start, start
    return Load.PickupWindowStatus.QUALITATIVE, None, None


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


class SeedRunner:
    def __init__(self, dataset_path: Path, *, validate_only=False, no_enqueue=False):
        self.dataset_path = Path(dataset_path)
        self.validate_only = validate_only
        self.no_enqueue = no_enqueue
        self.summary = SeedSummary()
        self.service = IngestionSubmissionService()

    def run(self) -> SeedSummary:
        preflight = run_preflight(self.dataset_path)
        self.summary.snapshot_version = preflight.version
        self.summary.manifest_checksum = preflight.manifest_checksum
        if self.validate_only:
            for b, rows in (
                ("loads", preflight.loads),
                ("carriers", preflight.carriers),
                ("market_rates", preflight.rates),
                ("emails", preflight.emails),
                ("calls", preflight.calls),
            ):
                self.summary.batch_results.append(
                    BatchOutcome(source_type=b, status="validated", records_seen=len(rows))
                )
            self.summary.processing_health = "ready"
            return self.summary

        # Everything past this point mutates state and runs under the seed lock.
        with self._advisory_lock():
            snapshot = self._resolve_snapshot(preflight)
            self.summary.snapshot_id = str(snapshot.id)
            tz = ZoneInfo(snapshot.display_timezone)

            self._bootstrap_reference()
            batches = {
                source: self._batch(snapshot, preflight, source, filename)
                for source, filename in [
                    ("carriers", "carrier_profiles.json"),
                    ("loads", "loads.csv"),
                    ("market_rates", "rate_history.csv"),
                    ("emails", "carrier_emails.json"),
                    ("calls", "call_recordings"),
                ]
            }
            self._import_carriers(snapshot, batches["carriers"], preflight.carriers)
            self._import_loads(snapshot, batches["loads"], preflight.loads, tz)
            self._import_rates(snapshot, batches["market_rates"], preflight.rates)
            self._import_emails(snapshot, batches["emails"], preflight.emails)
            self._import_calls(snapshot, batches["calls"], preflight.calls)

            activation_error = self._finalize(snapshot, batches)

            self.summary.stale_jobs_swept = sweep_stale_jobs()
            dispatch_error = None
            if snapshot.is_active and not self.no_enqueue:
                try:
                    self.summary.jobs_dispatched = dispatch_queued_jobs(snapshot)
                except Exception:
                    logger.exception("dispatch failed; queued jobs remain durable")
                    dispatch_error = "dispatch_unavailable: queued jobs remain; rerun seed later"

            self._aggregate(snapshot)

        if activation_error:
            raise SeedError(activation_error)
        if dispatch_error:
            raise SeedError(dispatch_error)
        return self.summary

    # ----- infrastructure -----

    def _advisory_lock(self):
        class _Lock:
            def __enter__(inner):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", [SEED_ADVISORY_LOCK_KEY])
                    acquired = cursor.fetchone()[0]
                if not acquired:
                    raise SeedError("another seed run holds the advisory lock")
                return inner

            def __exit__(inner, *exc):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [SEED_ADVISORY_LOCK_KEY])
                return False

        return _Lock()

    def _resolve_snapshot(self, preflight: Preflight) -> DatasetSnapshot:
        conflicting = (
            DatasetSnapshot.objects.filter(version=preflight.version, imported_at__isnull=False)
            .exclude(manifest_checksum=preflight.manifest_checksum)
            .exists()
        )
        if conflicting:
            raise SeedError(
                "version/checksum conflict: different content presented under a "
                "completed dataset version; introduce a new version instead"
            )
        tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
        as_of_date = datetime.strptime(settings.DEMO_AS_OF_DATE, "%Y-%m-%d").date()
        as_of_at = datetime.combine(as_of_date, time(12, 0), tzinfo=tz)
        snapshot, _ = DatasetSnapshot.objects.get_or_create(
            version=preflight.version,
            manifest_checksum=preflight.manifest_checksum,
            defaults={
                "name": "Goodlane interview dataset",
                "as_of_at": as_of_at,
                "display_timezone": settings.DISPLAY_TIMEZONE,
            },
        )
        return snapshot

    def _batch(self, snapshot, preflight, source_type, filename) -> ImportBatch:
        batch, _ = ImportBatch.objects.get_or_create(
            dataset_snapshot=snapshot,
            source_type=source_type,
            source_filename=filename,
            content_checksum=preflight.source_checksums[filename]
            if filename in preflight.source_checksums
            else preflight.source_checksums["call_recordings"],
        )
        return batch

    def _bootstrap_reference(self):
        for display, code in EQUIPMENT_VOCABULARY.items():
            equipment, _ = EquipmentType.objects.get_or_create(
                code=code, defaults={"display_name": display}
            )
            EquipmentAlias.objects.get_or_create(
                source="dataset",
                normalized_label=display.casefold(),
                defaults={"equipment_type": equipment, "raw_label": display},
            )

    def _resolve_equipment(self, raw_label) -> EquipmentType | None:
        if not raw_label:
            return None
        alias = (
            EquipmentAlias.objects.filter(
                source="dataset", normalized_label=str(raw_label).strip().casefold()
            )
            .select_related("equipment_type")
            .first()
        )
        return alias.equipment_type if alias else None

    def _lane(self, origin, destination) -> Lane:
        lane, _ = Lane.objects.get_or_create(
            origin_state=origin.strip().upper(), destination_state=destination.strip().upper()
        )
        return lane

    # ----- per-source imports -----

    def _run_batch(self, batch: ImportBatch, rows, import_row):
        if batch.status == ImportBatch.Status.COMPLETED:
            batch_result = BatchOutcome(
                source_type=batch.source_type,
                status=batch.status,
                records_seen=batch.records_seen,
                records_created=batch.records_created,
                records_existing=batch.records_existing,
                records_failed=batch.records_failed,
            )
            self.summary.batch_results.append(batch_result)
            return
        batch.status = ImportBatch.Status.PROCESSING
        batch.started_at = timezone.now()
        batch.records_seen = len(rows)
        created = existing = failed = 0
        errors = []
        for ordinal, row in enumerate(rows):
            try:
                with transaction.atomic():
                    outcome = import_row(ordinal, row)
            except Exception as exc:  # structural row failure, never business data
                failed += 1
                errors.append(f"row {ordinal}: {type(exc).__name__}")
                logger.warning("seed row failure in %s row %d", batch.source_type, ordinal)
            else:
                if outcome == "created":
                    created += 1
                else:
                    existing += 1
        batch.records_created = created
        batch.records_existing = existing
        batch.records_failed = failed
        batch.error_summary = "; ".join(errors[:20])
        batch.completed_at = timezone.now()
        batch.status = (
            ImportBatch.Status.PARTIALLY_FAILED if failed else ImportBatch.Status.COMPLETED
        )
        batch.save()
        self.summary.safe_errors.extend(f"{batch.source_type} {e}" for e in errors[:10])
        self.summary.batch_results.append(
            BatchOutcome(
                source_type=batch.source_type,
                status=batch.status,
                records_seen=batch.records_seen,
                records_created=created,
                records_existing=existing,
                records_failed=failed,
            )
        )

    def _import_carriers(self, snapshot, batch, rows):
        def import_row(ordinal, row):
            source_identifier = _carrier_source_identifier(row, ordinal)
            carrier, created = Carrier.objects.get_or_create(
                dataset_snapshot=snapshot,
                source_identifier=source_identifier,
                defaults={
                    "import_batch": batch,
                    "mc_number_raw": row.get("mc_number"),
                    "mc_number_normalized": _digits(row.get("mc_number")),
                    "dot_number_raw": row.get("dot_number"),
                    "dot_number_normalized": _digits(row.get("dot_number")),
                    "company_name": row["company_name"],
                    "address": row.get("address"),
                    "home_base_zip": row.get("home_base_zip"),
                    "factoring_company": row.get("factoring_company"),
                    "payment_terms_preference": row.get("payment_terms_preference"),
                    "reliability_score": _optional_decimal(row.get("reliability_score")),
                    "loads_completed_with_goodlane": _optional_int(
                        row.get("loads_completed_with_goodlane")
                    ),
                    "avg_response_time_hours": _optional_decimal(
                        row.get("avg_response_time_hours")
                    ),
                    "insurance_expiry": row.get("insurance_expiry") or None,
                    "authority_status": row.get("authority_status"),
                    "safety_rating": row.get("safety_rating"),
                    "onboarded": row.get("onboarded"),
                    "notes": row.get("notes") or "",
                },
            )
            if not created:
                return "existing"
            if row.get("email") or row.get("phone") or row.get("primary_contact"):
                CarrierContact.objects.create(
                    carrier=carrier,
                    name=row.get("primary_contact") or "",
                    email_raw=row.get("email"),
                    email_normalized=(
                        canonical_email_address(row["email"]) if row.get("email") else None
                    ),
                    phone_raw=row.get("phone"),
                    phone_normalized=_digits(row.get("phone")),
                    is_primary=True,
                )
            for label in row.get("equipment_types") or []:
                equipment = self._resolve_equipment(label)
                if equipment:
                    CarrierEquipment.objects.get_or_create(
                        carrier=carrier, equipment_type=equipment
                    )
            for lane_text in row.get("preferred_lanes") or []:
                parts = str(lane_text).split("-")
                if len(parts) == 2:
                    CarrierPreferredLane.objects.get_or_create(
                        carrier=carrier, lane=self._lane(parts[0], parts[1])
                    )
            return "created"

        self._run_batch(batch, rows, import_row)

    def _import_loads(self, snapshot, batch, rows, tz):
        def import_row(ordinal, row):
            pickup_date = datetime.strptime(row["pickup_date"], "%Y-%m-%d").date()
            status, start, end = _parse_pickup_window(row.get("pickup_window"), pickup_date, tz)
            _, created = Load.objects.get_or_create(
                dataset_snapshot=snapshot,
                external_load_id=row["load_id"],
                defaults={
                    "import_batch": batch,
                    "status": row["status"],
                    "origin_city": row["origin_city"],
                    "origin_state": row["origin_state"],
                    "origin_zip": row.get("origin_zip") or None,
                    "destination_city": row["destination_city"],
                    "destination_state": row["destination_state"],
                    "destination_zip": row.get("destination_zip") or None,
                    "lane": self._lane(row["origin_state"], row["destination_state"]),
                    "distance_miles": _optional_int(row.get("distance_miles")),
                    "equipment_type": self._resolve_equipment(row.get("equipment_type")),
                    "equipment_type_raw": row.get("equipment_type") or None,
                    "weight_lbs": _optional_int(row.get("weight_lbs")),
                    "pickup_date": pickup_date,
                    "pickup_window_raw": row.get("pickup_window") or None,
                    "pickup_start_at": start,
                    "pickup_end_at": end,
                    "pickup_window_status": status,
                    "delivery_date": (
                        datetime.strptime(row["delivery_date"], "%Y-%m-%d").date()
                        if row.get("delivery_date")
                        else None
                    ),
                    "offered_rate_usd": _optional_decimal(row.get("offered_rate_usd")),
                    "shipper_name": row.get("shipper_name") or None,
                    "internal_notes": row.get("internal_notes") or "",
                },
            )
            return "created" if created else "existing"

        self._run_batch(batch, rows, import_row)

    def _import_rates(self, snapshot, batch, rows):
        def import_row(ordinal, row):
            equipment = self._resolve_equipment(row["equipment_type"])
            if equipment is None:
                raise ValueError("unknown equipment vocabulary")
            _, created = MarketRateHistory.objects.get_or_create(
                dataset_snapshot=snapshot,
                week_start=datetime.strptime(row["week_start"], "%Y-%m-%d").date(),
                lane=self._lane(row["origin_state"], row["destination_state"]),
                equipment_type=equipment,
                defaults={
                    "import_batch": batch,
                    "average_rate_per_mile": Decimal(row["avg_rate_per_mile"]),
                    "minimum_rate_per_mile": Decimal(row["min_rate_per_mile"]),
                    "maximum_rate_per_mile": Decimal(row["max_rate_per_mile"]),
                    "load_volume": int(row["load_volume"]),
                },
            )
            return "created" if created else "existing"

        self._run_batch(batch, rows, import_row)

    def _import_emails(self, snapshot, batch, rows):
        def import_row(ordinal, row):
            occurred_at = _parse_aware_timestamp(row["timestamp"], row["email_id"])
            metadata_keys = (
                "mc_number",
                "load_reference",
                "equipment_mentioned",
                "rate_quoted_usd",
                "intent",
            )
            result = self.service.submit_email(
                SubmitEmailCommand(
                    origin="dataset",
                    sender_email=row["from_email"],
                    sender_name=row.get("from_name") or "",
                    recipient_emails=row.get("to_email"),
                    subject=row["subject"],
                    body_text=row["body"],
                    occurred_at=occurred_at,
                    source_timestamp_raw=row["timestamp"],
                    source_timezone="UTC",
                    source_metadata={k: row.get(k) for k in metadata_keys},
                    external_source_id=row["email_id"],
                    dataset_snapshot_id=snapshot.id,
                    import_batch_id=batch.id,
                    submitted_by="seed",
                    dispatch_mode="deferred",
                )
            )
            if result.outcome == "created":
                self.summary.jobs_created += 1
            else:
                self.summary.jobs_existing += 1
            return result.outcome

        self._run_batch(batch, rows, import_row)

    def _import_calls(self, snapshot, batch, sources):
        def import_row(ordinal, source: CallSource):
            content = source.path.read_bytes()
            storage_key = f"calls/dataset/{source.checksum}.wav"
            if default_storage.exists(storage_key):
                self.summary.audio_existing += 1
            else:
                default_storage.save(storage_key, ContentFile(content))
                self.summary.audio_copied += 1
            result = self.service.submit_call(
                SubmitCallCommand(
                    origin="dataset",
                    content=content,
                    storage_key=storage_key,
                    original_filename=source.filename,
                    mime_type="audio/wav",
                    byte_size=source.byte_size,
                    duration_seconds=source.duration_seconds,
                    sample_rate=source.sample_rate,
                    audio_format="wav",
                    external_source_id=source.stem,
                    dataset_snapshot_id=snapshot.id,
                    import_batch_id=batch.id,
                    submitted_by="seed",
                    dispatch_mode="deferred",
                )
            )
            if result.outcome == "created":
                self.summary.jobs_created += 1
            else:
                self.summary.jobs_existing += 1
            return result.outcome

        self._run_batch(batch, sources, import_row)

    # ----- finalize, aggregate -----

    def _finalize(self, snapshot, batches) -> str | None:
        incomplete = [
            b.source_type
            for b in ImportBatch.objects.filter(dataset_snapshot=snapshot)
            if b.status != ImportBatch.Status.COMPLETED
        ]
        if incomplete:
            return "activation blocked: batches not completed: " + ", ".join(sorted(incomplete))

        events = CommunicationEvent.objects.filter(dataset_snapshot=snapshot)
        for event in events.filter(channel="email"):
            if not hasattr(event, "email_content") or not hasattr(event, "ingestion_job"):
                return f"finalization check failed for {event.stable_evidence_id}"
        for event in events.filter(channel="call"):
            if not hasattr(event, "call_recording") or not hasattr(event, "ingestion_job"):
                return f"finalization check failed for {event.stable_evidence_id}"
            if not default_storage.exists(event.call_recording.storage_key):
                return f"missing audio object for {event.stable_evidence_id}"

        if not snapshot.is_active:
            with transaction.atomic():
                DatasetSnapshot.objects.filter(is_active=True).exclude(id=snapshot.id).update(
                    is_active=False
                )
                snapshot.is_active = True
                if snapshot.imported_at is None:
                    snapshot.imported_at = timezone.now()
                snapshot.save(update_fields=["is_active", "imported_at", "updated_at"])
        self.summary.activated = True
        return None

    def _aggregate(self, snapshot):
        counts = dict.fromkeys(["queued", "processing", "completed", "needs_review", "failed"], 0)
        rows = (
            IngestionJob.objects.filter(communication_event__dataset_snapshot=snapshot)
            .values_list("status")
            .order_by()
        )
        for (status,) in rows:
            counts[status] = counts.get(status, 0) + 1
        self.summary.job_status_counts = counts
        self.summary.jobs_left_queued = counts["queued"] - self.summary.jobs_dispatched
        if counts["failed"] or self.summary.stale_jobs_swept:
            self.summary.processing_health = "attention_required"
        elif counts["queued"] or counts["processing"]:
            self.summary.processing_health = "processing"
        else:
            self.summary.processing_health = "ready"


# Re-export for callers importing from this module.
__all__ = ["SeedError", "SeedRunner", "SeedSummary", "run_preflight", "CallRecording"]
