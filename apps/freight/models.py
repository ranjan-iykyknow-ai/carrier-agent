"""Step 2A — dataset, reference, load, carrier, and market models.

NULL means unknown throughout (spec §2): missing rates, weights, dates, compliance
facts, and identifiers are never coerced to zero, False, or an empty string.
Imported vocabulary fields (authority_status, safety_rating, payment_terms_preference)
are stored verbatim without choices; the versioned compliance policy interprets them,
treating unrecognized values as unknown.
"""

from django.db import models
from django.db.models import F, Q

from apps.base import AppendOnlyModel, TimeStampedModel


class DatasetSnapshot(TimeStampedModel):
    name = models.CharField(max_length=200)
    version = models.CharField(max_length=100)
    as_of_at = models.DateTimeField()
    display_timezone = models.CharField(max_length=64, default="America/New_York")
    manifest_checksum = models.CharField(max_length=64)
    is_active = models.BooleanField(default=False)
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=Q(is_active=True),
                name="freight_one_active_snapshot",
            ),
            models.UniqueConstraint(
                fields=["version", "manifest_checksum"],
                name="freight_snapshot_version_checksum_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.name} {self.version}"


class ImportBatch(TimeStampedModel):
    class SourceType(models.TextChoices):
        LOADS = "loads"
        CARRIERS = "carriers"
        EMAILS = "emails"
        CALLS = "calls"
        MARKET_RATES = "market_rates"

    class Status(models.TextChoices):
        QUEUED = "queued"
        PROCESSING = "processing"
        COMPLETED = "completed"
        PARTIALLY_FAILED = "partially_failed"
        FAILED = "failed"

    dataset_snapshot = models.ForeignKey(
        DatasetSnapshot, on_delete=models.PROTECT, related_name="import_batches"
    )
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    source_filename = models.CharField(max_length=255)
    content_checksum = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    records_seen = models.PositiveIntegerField(default=0)
    records_created = models.PositiveIntegerField(default=0)
    records_existing = models.PositiveIntegerField(default=0)
    records_failed = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_summary = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_snapshot", "source_type", "source_filename", "content_checksum"],
                name="freight_import_batch_idempotency_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.source_type}:{self.source_filename} ({self.status})"


class EquipmentType(TimeStampedModel):
    code = models.CharField(max_length=50, unique=True)
    display_name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.display_name


class EquipmentAlias(TimeStampedModel):
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, related_name="aliases"
    )
    source = models.CharField(max_length=50)
    raw_label = models.CharField(max_length=100)
    normalized_label = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "normalized_label"],
                name="freight_equipment_alias_uniq",
            ),
        ]
        verbose_name_plural = "equipment aliases"

    def __str__(self):
        return f"{self.raw_label} -> {self.equipment_type.code}"


class Lane(TimeStampedModel):
    """Directional state pair; intra-state lanes (PA->PA) are valid."""

    origin_state = models.CharField(max_length=2)
    destination_state = models.CharField(max_length=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["origin_state", "destination_state"],
                name="freight_lane_directional_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.origin_state}->{self.destination_state}"


class Load(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open"
        COVERED = "covered"
        DELIVERED = "delivered"
        CANCELLED = "cancelled"

    class PickupWindowStatus(models.TextChoices):
        RANGE = "range"
        SINGLE_TIME = "single_time"
        QUALITATIVE = "qualitative"
        MISSING = "missing"
        INVALID = "invalid"

    dataset_snapshot = models.ForeignKey(
        DatasetSnapshot, on_delete=models.PROTECT, related_name="loads"
    )
    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.PROTECT, null=True, blank=True, related_name="loads"
    )
    external_load_id = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=Status.choices)
    origin_city = models.CharField(max_length=100)
    origin_state = models.CharField(max_length=2)
    origin_zip = models.CharField(max_length=10, null=True, blank=True)
    destination_city = models.CharField(max_length=100)
    destination_state = models.CharField(max_length=2)
    destination_zip = models.CharField(max_length=10, null=True, blank=True)
    lane = models.ForeignKey(Lane, on_delete=models.PROTECT, related_name="loads")
    distance_miles = models.IntegerField(null=True, blank=True)
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, null=True, blank=True, related_name="loads"
    )
    equipment_type_raw = models.CharField(max_length=100, null=True, blank=True)
    weight_lbs = models.IntegerField(null=True, blank=True)
    pickup_date = models.DateField()
    pickup_window_raw = models.CharField(max_length=100, null=True, blank=True)
    pickup_start_at = models.DateTimeField(null=True, blank=True)
    pickup_end_at = models.DateTimeField(null=True, blank=True)
    pickup_window_status = models.CharField(max_length=20, choices=PickupWindowStatus.choices)
    delivery_date = models.DateField(null=True, blank=True)
    offered_rate_usd = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    shipper_name = models.CharField(max_length=200, blank=True, default="")
    internal_notes = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_snapshot", "external_load_id"],
                name="freight_load_external_id_uniq",
            ),
            models.CheckConstraint(
                condition=Q(distance_miles__isnull=True) | Q(distance_miles__gt=0),
                name="freight_load_distance_positive",
            ),
            models.CheckConstraint(
                condition=Q(weight_lbs__isnull=True) | Q(weight_lbs__gte=0),
                name="freight_load_weight_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(offered_rate_usd__isnull=True) | Q(offered_rate_usd__gte=0),
                name="freight_load_rate_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["pickup_date"]),
        ]

    def __str__(self):
        return f"Load {self.external_load_id}"


class Carrier(TimeStampedModel):
    dataset_snapshot = models.ForeignKey(
        DatasetSnapshot, on_delete=models.PROTECT, related_name="carriers"
    )
    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.PROTECT, null=True, blank=True, related_name="carriers"
    )
    source_identifier = models.CharField(max_length=100)
    mc_number_raw = models.CharField(max_length=50, null=True, blank=True)
    mc_number_normalized = models.CharField(max_length=50, null=True, blank=True)
    dot_number_raw = models.CharField(max_length=50, null=True, blank=True)
    dot_number_normalized = models.CharField(max_length=50, null=True, blank=True)
    company_name = models.CharField(max_length=200)
    address = models.CharField(max_length=255, null=True, blank=True)
    home_base_zip = models.CharField(max_length=10, null=True, blank=True)
    factoring_company = models.CharField(max_length=200, null=True, blank=True)
    payment_terms_preference = models.CharField(max_length=50, null=True, blank=True)
    reliability_score = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    loads_completed_with_goodlane = models.IntegerField(null=True, blank=True)
    avg_response_time_hours = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    insurance_expiry = models.DateField(null=True, blank=True)
    authority_status = models.CharField(max_length=50, null=True, blank=True)
    safety_rating = models.CharField(max_length=50, null=True, blank=True)
    onboarded = models.BooleanField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_snapshot", "source_identifier"],
                name="freight_carrier_source_id_uniq",
            ),
            models.UniqueConstraint(
                fields=["dataset_snapshot", "mc_number_normalized"],
                condition=Q(mc_number_normalized__isnull=False),
                name="freight_carrier_mc_uniq_when_present",
            ),
            models.UniqueConstraint(
                fields=["dataset_snapshot", "dot_number_normalized"],
                condition=Q(dot_number_normalized__isnull=False),
                name="freight_carrier_dot_uniq_when_present",
            ),
            models.CheckConstraint(
                condition=Q(reliability_score__isnull=True)
                | (Q(reliability_score__gte=0) & Q(reliability_score__lte=5)),
                name="freight_carrier_reliability_scale",
            ),
            models.CheckConstraint(
                condition=Q(loads_completed_with_goodlane__isnull=True)
                | Q(loads_completed_with_goodlane__gte=0),
                name="freight_carrier_loads_completed_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["mc_number_normalized"]),
            models.Index(fields=["dot_number_normalized"]),
        ]

    def __str__(self):
        return self.company_name


class CarrierContact(TimeStampedModel):
    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=200, blank=True, default="")
    email_raw = models.CharField(max_length=254, null=True, blank=True)
    email_normalized = models.CharField(max_length=254, null=True, blank=True)
    phone_raw = models.CharField(max_length=50, null=True, blank=True)
    phone_normalized = models.CharField(max_length=50, null=True, blank=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["carrier"],
                condition=Q(is_primary=True),
                name="freight_one_primary_contact_per_carrier",
            ),
        ]
        indexes = [
            models.Index(fields=["email_normalized"]),
            models.Index(fields=["phone_normalized"]),
        ]

    def __str__(self):
        return f"{self.name} <{self.email_raw or self.phone_raw or 'unknown'}>"


class CarrierEquipment(TimeStampedModel):
    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="equipment")
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, related_name="carrier_equipment"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["carrier", "equipment_type"],
                name="freight_carrier_equipment_uniq",
            ),
        ]
        verbose_name_plural = "carrier equipment"


class CarrierPreferredLane(TimeStampedModel):
    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="preferred_lanes")
    lane = models.ForeignKey(Lane, on_delete=models.PROTECT, related_name="carrier_preferences")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["carrier", "lane"],
                name="freight_carrier_preferred_lane_uniq",
            ),
        ]


class MarketRateHistory(AppendOnlyModel):
    dataset_snapshot = models.ForeignKey(
        DatasetSnapshot, on_delete=models.PROTECT, related_name="market_rates"
    )
    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.PROTECT, null=True, blank=True, related_name="market_rates"
    )
    week_start = models.DateField()
    lane = models.ForeignKey(Lane, on_delete=models.PROTECT, related_name="market_rates")
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, related_name="market_rates"
    )
    average_rate_per_mile = models.DecimalField(max_digits=8, decimal_places=2)
    minimum_rate_per_mile = models.DecimalField(max_digits=8, decimal_places=2)
    maximum_rate_per_mile = models.DecimalField(max_digits=8, decimal_places=2)
    load_volume = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_snapshot", "week_start", "lane", "equipment_type"],
                name="freight_market_rate_week_uniq",
            ),
            models.CheckConstraint(
                condition=Q(minimum_rate_per_mile__lte=F("average_rate_per_mile"))
                & Q(average_rate_per_mile__lte=F("maximum_rate_per_mile")),
                name="freight_market_rate_band_ordering",
            ),
            models.CheckConstraint(
                condition=Q(load_volume__gte=0),
                name="freight_market_rate_volume_non_negative",
            ),
        ]
        verbose_name_plural = "market rate history"

    def __str__(self):
        return f"{self.lane} {self.equipment_type.code} wk {self.week_start}"
