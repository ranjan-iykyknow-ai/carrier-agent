from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.admin_base import ReadOnlyAdmin, change_link, pretty_json
from apps.comms.models import (
    CallRecording,
    CommunicationEvent,
    EmailContent,
    IngestionJob,
    Transcript,
    TranscriptSegment,
)


@admin.register(CommunicationEvent)
class CommunicationEventAdmin(ReadOnlyAdmin):
    list_display = (
        "stable_evidence_id",
        "channel",
        "origin",
        "external_source_id",
        "occurred_at",
        "received_at",
    )
    list_filter = ("channel", "origin")
    search_fields = ("stable_evidence_id", "external_source_id", "content_fingerprint")
    ordering = ("-received_at",)


@admin.register(EmailContent)
class EmailContentAdmin(ReadOnlyAdmin):
    list_display = ("communication_event", "sender_email_raw", "sender_name", "subject")
    search_fields = ("sender_email_normalized", "sender_name", "subject")
    list_select_related = ("communication_event",)


@admin.register(CallRecording)
class CallRecordingAdmin(ReadOnlyAdmin):
    list_display = (
        "original_filename",
        "communication_event",
        "duration_seconds",
        "byte_size",
        "audio_format",
    )
    search_fields = ("original_filename", "sha256_checksum")
    list_select_related = ("communication_event",)
    # The private object key is never rendered; playback uses the
    # authenticated application endpoint (spec 3J).
    exclude = ("storage_key",)
    readonly_fields = ("playback",)

    @admin.display(description="Playback")
    def playback(self, obj):
        return format_html(
            '<audio controls preload="none" src="{}"></audio>',
            reverse("call_audio", args=[obj.pk]),
        )


@admin.register(IngestionJob)
class IngestionJobAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "status",
        "origin",
        "source_type",
        "retry_count",
        "last_error_code",
        "event",
        "submitted_at",
        "completed_at",
    )
    list_filter = ("status", "origin", "source_type", "last_error_code")
    search_fields = ("id", "correlation_id", "content_fingerprint")
    list_select_related = ("communication_event",)
    ordering = ("-submitted_at",)

    @admin.display(description="Communication")
    def event(self, obj):
        return change_link(obj.communication_event, obj.communication_event.stable_evidence_id)


@admin.register(Transcript)
class TranscriptAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "call_recording",
        "provider",
        "requested_model",
        "is_current",
        "retry_generation",
        "language",
    )
    list_filter = ("provider", "is_current")
    list_select_related = ("call_recording",)
    # The unrestricted raw provider payload is never rendered (spec 3J).
    exclude = ("provider_response",)
    readonly_fields = ("provider_metadata_pretty",)

    @admin.display(description="Provider metadata")
    def provider_metadata_pretty(self, obj):
        return pretty_json(obj.provider_metadata)


@admin.register(TranscriptSegment)
class TranscriptSegmentAdmin(ReadOnlyAdmin):
    list_display = (
        "transcript",
        "sequence",
        "speaker_label",
        "start_seconds",
        "end_seconds",
        "confidence",
    )
    list_select_related = ("transcript",)
    search_fields = ("text",)
