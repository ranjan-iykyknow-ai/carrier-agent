"""Constraint behavior for the Step 2A models (snapshot, loads, carriers, market)."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from tests.factories import (
    make_carrier,
    make_equipment,
    make_import_batch,
    make_lane,
    make_load,
    make_market_rate,
    make_snapshot,
)

pytestmark = pytest.mark.django_db


def _rejects(fn):
    with pytest.raises(IntegrityError), transaction.atomic():
        fn()


class TestDatasetSnapshot:
    def test_only_one_snapshot_may_be_active(self):
        make_snapshot(is_active=True)
        _rejects(lambda: make_snapshot(is_active=True))

    def test_multiple_inactive_snapshots_allowed(self):
        make_snapshot()
        make_snapshot()

    def test_version_and_manifest_checksum_unique_together(self):
        first = make_snapshot()
        _rejects(
            lambda: make_snapshot(version=first.version, manifest_checksum=first.manifest_checksum)
        )


class TestImportBatch:
    def test_idempotency_key_unique(self):
        batch = make_import_batch()
        _rejects(
            lambda: make_import_batch(
                snapshot=batch.dataset_snapshot,
                source_type=batch.source_type,
                source_filename=batch.source_filename,
                content_checksum=batch.content_checksum,
            )
        )

    def test_same_file_new_checksum_is_a_new_batch(self):
        batch = make_import_batch()
        make_import_batch(
            snapshot=batch.dataset_snapshot,
            source_type=batch.source_type,
            source_filename=batch.source_filename,
        )


class TestLane:
    def test_ordered_pair_unique(self):
        make_lane("PA", "NJ")
        _rejects(
            lambda: type(make_lane("DE", "MD")).objects.create(
                origin_state="PA", destination_state="NJ"
            )
        )

    def test_lanes_are_directional_and_intrastate_is_valid(self):
        make_lane("PA", "NJ")
        make_lane("NJ", "PA")
        make_lane("PA", "PA")


class TestLoad:
    def test_external_load_id_unique_within_snapshot(self):
        load = make_load()
        _rejects(
            lambda: make_load(
                snapshot=load.dataset_snapshot, external_load_id=load.external_load_id
            )
        )

    def test_same_external_id_allowed_across_snapshots(self):
        load = make_load()
        make_load(external_load_id=load.external_load_id)

    def test_distance_must_be_positive_when_present(self):
        _rejects(lambda: make_load(distance_miles=0))

    def test_offered_rate_must_be_non_negative_when_present(self):
        _rejects(lambda: make_load(offered_rate_usd=Decimal("-1")))

    def test_unknown_values_stay_null_not_zero(self):
        load = make_load(distance_miles=None, weight_lbs=None, offered_rate_usd=None)
        assert load.distance_miles is None
        assert load.weight_lbs is None
        assert load.offered_rate_usd is None


class TestCarrier:
    def test_normalized_mc_unique_within_snapshot_when_present(self):
        carrier = make_carrier(mc_number_raw="712843", mc_number_normalized="712843")
        _rejects(
            lambda: make_carrier(snapshot=carrier.dataset_snapshot, mc_number_normalized="712843")
        )

    def test_carriers_without_mc_do_not_collide(self):
        snapshot = make_snapshot()
        make_carrier(snapshot=snapshot)
        make_carrier(snapshot=snapshot)

    def test_reliability_score_bounded_to_dataset_scale(self):
        _rejects(lambda: make_carrier(reliability_score=Decimal("5.5")))

    def test_source_identifier_unique_within_snapshot(self):
        carrier = make_carrier()
        _rejects(
            lambda: make_carrier(
                snapshot=carrier.dataset_snapshot,
                source_identifier=carrier.source_identifier,
            )
        )


class TestCarrierRelations:
    def test_carrier_equipment_pair_unique(self):
        from apps.freight.models import CarrierEquipment

        carrier = make_carrier()
        equipment = make_equipment()
        CarrierEquipment.objects.create(carrier=carrier, equipment_type=equipment)
        _rejects(lambda: CarrierEquipment.objects.create(carrier=carrier, equipment_type=equipment))

    def test_carrier_preferred_lane_pair_unique(self):
        from apps.freight.models import CarrierPreferredLane

        carrier = make_carrier()
        lane = make_lane("PA", "NJ")
        CarrierPreferredLane.objects.create(carrier=carrier, lane=lane)
        _rejects(lambda: CarrierPreferredLane.objects.create(carrier=carrier, lane=lane))


class TestMarketRateHistory:
    def test_snapshot_week_lane_equipment_unique(self):
        row = make_market_rate()
        _rejects(
            lambda: make_market_rate(
                snapshot=row.dataset_snapshot,
                week_start=row.week_start,
                lane=row.lane,
                equipment_type=row.equipment_type,
            )
        )

    def test_band_ordering_min_lte_avg_lte_max_enforced(self):
        _rejects(
            lambda: make_market_rate(
                minimum_rate_per_mile="3.00",
                average_rate_per_mile="2.50",
                maximum_rate_per_mile="3.50",
            )
        )

    def test_volume_must_be_non_negative(self):
        _rejects(lambda: make_market_rate(load_volume=-1))
