"""Plain factory functions building minimal valid model instances for tests."""

import hashlib
import uuid
from datetime import UTC, date, datetime

from apps.freight.models import (
    Carrier,
    DatasetSnapshot,
    EquipmentType,
    ImportBatch,
    Lane,
    Load,
    MarketRateHistory,
)


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
