from django.contrib import admin

from apps.admin_base import ReadOnlyAdmin
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


@admin.register(DatasetSnapshot)
class DatasetSnapshotAdmin(ReadOnlyAdmin):
    list_display = ("name", "version", "is_active", "as_of_at", "imported_at")
    list_filter = ("is_active",)


@admin.register(ImportBatch)
class ImportBatchAdmin(ReadOnlyAdmin):
    list_display = (
        "source_type",
        "source_filename",
        "status",
        "records_created",
        "records_existing",
        "records_failed",
        "completed_at",
    )
    list_filter = ("status", "source_type")
    list_select_related = ("dataset_snapshot",)


@admin.register(EquipmentType)
class EquipmentTypeAdmin(ReadOnlyAdmin):
    list_display = ("code", "display_name", "is_active")


@admin.register(EquipmentAlias)
class EquipmentAliasAdmin(ReadOnlyAdmin):
    list_display = ("normalized_label", "raw_label", "equipment_type", "source")
    list_select_related = ("equipment_type",)
    search_fields = ("normalized_label",)


@admin.register(Lane)
class LaneAdmin(ReadOnlyAdmin):
    list_display = ("origin_state", "destination_state")


@admin.register(Load)
class LoadAdmin(ReadOnlyAdmin):
    list_display = (
        "external_load_id",
        "status",
        "origin_city",
        "destination_city",
        "equipment_type",
        "pickup_date",
        "offered_rate_usd",
    )
    list_filter = ("status", "equipment_type")
    search_fields = ("external_load_id", "origin_city", "destination_city")
    list_select_related = ("equipment_type",)
    ordering = ("pickup_date",)


@admin.register(Carrier)
class CarrierAdmin(ReadOnlyAdmin):
    list_display = (
        "company_name",
        "mc_number_raw",
        "dot_number_raw",
        "authority_status",
        "safety_rating",
        "insurance_expiry",
        "onboarded",
    )
    list_filter = ("authority_status", "safety_rating", "onboarded")
    search_fields = ("company_name", "mc_number_normalized", "dot_number_normalized")


@admin.register(CarrierContact)
class CarrierContactAdmin(ReadOnlyAdmin):
    list_display = ("carrier", "name", "email_raw", "phone_raw", "is_primary")
    search_fields = ("name", "email_normalized", "carrier__company_name")
    list_select_related = ("carrier",)


@admin.register(CarrierEquipment)
class CarrierEquipmentAdmin(ReadOnlyAdmin):
    list_display = ("carrier", "equipment_type")
    list_select_related = ("carrier", "equipment_type")


@admin.register(CarrierPreferredLane)
class CarrierPreferredLaneAdmin(ReadOnlyAdmin):
    list_display = ("carrier", "lane")
    list_select_related = ("carrier", "lane")


@admin.register(MarketRateHistory)
class MarketRateHistoryAdmin(ReadOnlyAdmin):
    list_display = (
        "lane",
        "equipment_type",
        "week_start",
        "minimum_rate_per_mile",
        "average_rate_per_mile",
        "maximum_rate_per_mile",
        "load_volume",
    )
    list_filter = ("equipment_type",)
    list_select_related = ("lane", "equipment_type")
    ordering = ("-week_start",)
