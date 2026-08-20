"""Manual Ingestion Lab (spec 3A.3/3A.4): same pipeline, preview safety, durable jobs."""

import io
import wave
from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.comms.models import CallRecording, CommunicationEvent, EmailContent, IngestionJob
from tests.factories import (
    make_call_recording,
    make_communication_event,
    make_email_content,
    make_ingestion_job,
    make_inquiry,
    make_snapshot,
)

pytestmark = pytest.mark.django_db

EMAIL_FORM = {
    "sender_name": "Desmond Okafor",
    "sender_email": "desmond@atlanticcarriersinc.com",
    "subject": "Truck available",
    "body": "MC 884411 here.\nWe can cover Allentown\ttomorrow.",
}


def wav_bytes(seconds=1.0, rate=8000, tone=7):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes([tone, 0]) * int(seconds * rate))
    return buffer.getvalue()


@pytest.fixture
def broker(client):
    user = User.objects.create_user("broker@goodlanelogistics.com", password="x")
    client.force_login(user)
    return user


@pytest.fixture
def snapshot():
    return make_snapshot(is_active=True, as_of_at=datetime(2026, 5, 25, 16, 0, tzinfo=UTC))


@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture(autouse=True)
def _eager_dispatch(monkeypatch):
    """Keep submissions durable without a live broker in tests."""
    monkeypatch.setattr("apps.comms.ingestion.process_ingestion_job.delay", lambda job_id: None)


class TestLabPage:
    def test_requires_login(self, client, snapshot):
        response = client.get(reverse("lab"))
        assert response.status_code == 302

    def test_renders_both_forms_and_demo_date(self, client, broker, snapshot):
        response = client.get(reverse("lab"))
        html = response.content.decode()
        assert "sender_email" in html
        assert "audio_file" in html
        assert "May 25, 2026" in html

    def test_without_snapshot_submission_is_disabled(self, client, broker):
        response = client.get(reverse("lab"))
        assert b"No active dataset snapshot" in response.content


class TestEmailPreview:
    def test_preview_escapes_and_creates_nothing(self, client, broker, snapshot):
        data = dict(EMAIL_FORM, body="<script>alert(1)</script> body text")

        response = client.post(reverse("lab_email_preview"), data)

        html = response.content.decode()
        assert response.status_code == 200
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
        assert CommunicationEvent.objects.count() == 0
        assert IngestionJob.objects.count() == 0

    def test_preview_labels_hints_untrusted(self, client, broker, snapshot):
        data = dict(EMAIL_FORM, suspected_load_reference="29372450")
        response = client.post(reverse("lab_email_preview"), data)
        assert b"untrusted" in response.content.lower()

    def test_invalid_preview_shows_field_errors(self, client, broker, snapshot):
        response = client.post(reverse("lab_email_preview"), {"sender_email": "not-an-email"})
        assert response.status_code == 200
        assert b"body" in response.content


class TestEmailValidation:
    def test_nul_bytes_rejected_newlines_preserved(self, client, broker, snapshot):
        bad = dict(EMAIL_FORM, body="line one\x00line two")
        response = client.post(reverse("lab_email_submit"), bad)
        assert response.status_code == 200  # re-rendered with errors
        assert CommunicationEvent.objects.count() == 0

        good = dict(EMAIL_FORM)
        response = client.post(reverse("lab_email_submit"), good)
        assert response.status_code == 303
        email = EmailContent.objects.get()
        assert "\n" in email.body_text and "\t" in email.body_text

    def test_whitespace_only_body_rejected(self, client, broker, snapshot):
        response = client.post(reverse("lab_email_submit"), dict(EMAIL_FORM, body="   \n\t "))
        assert response.status_code == 200
        assert CommunicationEvent.objects.count() == 0


class TestEmailSubmit:
    def test_submit_creates_records_and_redirects_to_job(self, client, broker, snapshot):
        data = dict(
            EMAIL_FORM,
            suspected_load_reference="29372450",
            suspected_carrier_identity="Atlantic Carriers",
        )

        response = client.post(reverse("lab_email_submit"), data)

        assert response.status_code == 303
        job = IngestionJob.objects.get()
        assert response.url == reverse("lab_job", args=[job.pk])
        assert job.origin == "manual"
        assert job.status == IngestionJob.Status.QUEUED
        assert job.suspected_load_reference_raw == "29372450"
        assert job.suspected_carrier_identity_raw == "Atlantic Carriers"
        event = job.communication_event
        # Manual demo clock: snapshot's local date, real local time of day.
        assert event.occurred_at.astimezone(UTC).date() in (
            datetime(2026, 5, 25, tzinfo=UTC).date(),
            datetime(2026, 5, 26, tzinfo=UTC).date(),
        )
        assert event.origin == "manual"

    def test_htmx_submit_uses_hx_redirect(self, client, broker, snapshot):
        response = client.post(reverse("lab_email_submit"), EMAIL_FORM, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        job = IngestionJob.objects.get()
        assert response.headers["HX-Redirect"] == reverse("lab_job", args=[job.pk])

    def test_duplicate_returns_existing_job_without_new_records(self, client, broker, snapshot):
        client.post(reverse("lab_email_submit"), EMAIL_FORM)
        job = IngestionJob.objects.get()

        second = client.post(reverse("lab_email_submit"), EMAIL_FORM)

        assert second.status_code == 303
        assert reverse("lab_job", args=[job.pk]) in second.url
        assert "existing=1" in second.url
        assert IngestionJob.objects.count() == 1
        assert CommunicationEvent.objects.count() == 1

    def test_no_active_snapshot_is_service_unavailable(self, client, broker):
        response = client.post(reverse("lab_email_submit"), EMAIL_FORM)
        assert response.status_code == 503
        assert CommunicationEvent.objects.count() == 0


class TestJobPage:
    def test_queued_job_page_polls(self, client, broker, snapshot):
        client.post(reverse("lab_email_submit"), EMAIL_FORM)
        job = IngestionJob.objects.get()

        response = client.get(reverse("lab_job", args=[job.pk]))

        html = response.content.decode()
        assert "Queued" in html
        assert reverse("lab_job_status", args=[job.pk]) in html
        assert "hx-trigger" in html
        assert str(job.correlation_id) in html

    def test_terminal_job_stops_polling_and_links_review(self, client, broker, snapshot):
        event = make_communication_event(snapshot=snapshot, origin="manual")
        make_email_content(event=event)
        job = make_ingestion_job(event=event, status=IngestionJob.Status.NEEDS_REVIEW)
        inquiry = make_inquiry(event=event, review_status="needs_review")

        response = client.get(reverse("lab_job_status", args=[job.pk]))

        html = response.content.decode()
        assert "hx-trigger" not in html
        assert reverse("inquiry_review", args=[inquiry.pk]) in html

    def test_status_poll_runs_the_stale_sweep(self, client, broker, snapshot, settings):
        event = make_communication_event(snapshot=snapshot, origin="manual")
        make_email_content(event=event)
        stale_since = datetime.now(UTC) - timedelta(
            seconds=settings.STALE_QUEUED_SWEEP_SECONDS + 60
        )
        job = make_ingestion_job(
            event=event, status=IngestionJob.Status.QUEUED, submitted_at=stale_since
        )
        IngestionJob.objects.filter(pk=job.pk).update(created_at=stale_since)

        client.get(reverse("lab_job_status", args=[job.pk]))

        job.refresh_from_db()
        assert job.status == IngestionJob.Status.FAILED
        assert job.last_error_code == "queue_unavailable"

    def test_failed_job_offers_retry_and_retry_requeues(self, client, broker, snapshot):
        event = make_communication_event(snapshot=snapshot, origin="manual")
        make_email_content(event=event)
        job = make_ingestion_job(
            event=event,
            status=IngestionJob.Status.FAILED,
            last_error_code="provider_unavailable",
        )

        page = client.get(reverse("lab_job", args=[job.pk]))
        assert b"Retry" in page.content

        response = client.post(reverse("lab_job_retry", args=[job.pk]))

        assert response.status_code == 302
        job.refresh_from_db()
        assert job.status == IngestionJob.Status.QUEUED
        assert job.retry_count == 1


class TestAudioSubmit:
    def test_valid_wav_creates_recording_and_job(self, client, broker, snapshot):
        upload = SimpleUploadedFile("Carrier Call.WAV", wav_bytes(), content_type="audio/wav")

        response = client.post(reverse("lab_audio_submit"), {"audio_file": upload})

        assert response.status_code == 303
        job = IngestionJob.objects.get()
        assert job.source_type == "call"
        recording = CallRecording.objects.get()
        assert recording.original_filename == "Carrier Call.WAV"
        assert "Carrier Call" not in recording.storage_key  # never user-controlled
        assert recording.duration_seconds is not None

    def test_garbage_with_wav_extension_rejected(self, client, broker, snapshot):
        upload = SimpleUploadedFile("fake.wav", b"MZ not audio", content_type="audio/wav")
        response = client.post(reverse("lab_audio_submit"), {"audio_file": upload})
        assert response.status_code == 200
        assert CallRecording.objects.count() == 0

    def test_wrong_extension_rejected(self, client, broker, snapshot):
        upload = SimpleUploadedFile("call.mp3", wav_bytes(), content_type="audio/mpeg")
        response = client.post(reverse("lab_audio_submit"), {"audio_file": upload})
        assert response.status_code == 200
        assert CallRecording.objects.count() == 0

    def test_duration_limit_enforced(self, client, broker, snapshot, settings):
        settings.SEED_MAX_WAV_SECONDS = 2
        upload = SimpleUploadedFile("long.wav", wav_bytes(seconds=3.5), content_type="audio/wav")
        response = client.post(reverse("lab_audio_submit"), {"audio_file": upload})
        assert response.status_code == 200
        assert CallRecording.objects.count() == 0

    def test_duplicate_audio_returns_existing(self, client, broker, snapshot):
        payload = wav_bytes(tone=9)
        first = SimpleUploadedFile("a.wav", payload, content_type="audio/wav")
        client.post(reverse("lab_audio_submit"), {"audio_file": first})
        job = IngestionJob.objects.get()

        second = SimpleUploadedFile("b.wav", payload, content_type="audio/wav")
        response = client.post(reverse("lab_audio_submit"), {"audio_file": second})

        assert response.status_code == 303
        assert reverse("lab_job", args=[job.pk]) in response.url
        assert CallRecording.objects.count() == 1


class TestAudioStreamingRange:
    def test_single_range_request_returns_206(self, client, broker, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        recording = make_call_recording(storage_key="calls/test/range.wav")
        default_storage.save("calls/test/range.wav", ContentFile(b"0123456789"))

        response = client.get(reverse("call_audio", args=[recording.pk]), HTTP_RANGE="bytes=2-5")

        assert response.status_code == 206
        assert response.headers["Content-Range"] == "bytes 2-5/10"
        assert response.content == b"2345"


class TestHintSignals:
    """Hints are weak entity-resolution signals: propose, never verify (spec 3A.3)."""

    def _finalize(self, snapshot, *, hints=None, output_overrides=None):
        from apps.inquiries.reconciliation import finalize_communication
        from tests.factories import make_extraction_run

        event = make_communication_event(snapshot=snapshot, origin="manual")
        make_email_content(event=event, body_text="We have a truck available this week.")
        job = make_ingestion_job(event=event, status="processing", **(hints or {}))
        output = {
            "carrier_name": None,
            "carrier_name_evidence": None,
            "mc_number": None,
            "mc_number_evidence": None,
            "dot_number": None,
            "contact_email": None,
            "contact_phone": None,
            "load_reference": None,
            "load_reference_evidence": None,
            "equipment": None,
            "equipment_evidence": None,
            "availability": "confirmed",
            "availability_evidence": None,
            "intents": ["availability"],
            "rates": [],
            "questions": [],
            "conditions": None,
            "conditions_evidence": None,
            "summary": "Truck available.",
        }
        output["inquiries"] = None
        payload = {"inquiries": [dict(output, **(output_overrides or {}))]}
        payload["inquiries"][0].pop("inquiries", None)
        run = make_extraction_run(
            event=event,
            job=job,
            validated_output=payload,
            validation_status="valid",
            is_current=True,
        )
        finalize_communication(run, generation=0)
        from apps.inquiries.models import Inquiry

        return Inquiry.objects.get(communication_event=event)

    def test_carrier_hint_proposes_weak_match_only(self, broker, snapshot):
        from tests.factories import make_carrier

        carrier = make_carrier(snapshot=snapshot, company_name="Blue Ridge Transport LLC")
        inquiry = self._finalize(
            snapshot, hints={"suspected_carrier_identity_raw": "Blue Ridge Transport"}
        )

        match = inquiry.carrier_matches.get(carrier=carrier)
        assert match.match_tier == "weak"
        assert match.is_selected is False
        assert "manual_hint" in match.signal_codes
        assert inquiry.carrier is None  # a hint can never verify
        assert inquiry.review_reasons.filter(code="weak_carrier_match").exists()

    def test_load_hint_proposes_match_for_review(self, broker, snapshot):
        from tests.factories import make_load

        load = make_load(snapshot=snapshot, external_load_id="29372450")
        inquiry = self._finalize(snapshot, hints={"suspected_load_reference_raw": "29372450"})

        match = inquiry.load_matches.get(load=load)
        assert match.is_selected is False
        assert "manual_hint" in match.signal_codes
        assert inquiry.load is None
        assert inquiry.review_reasons.filter(code="ambiguous_load_reference").exists()

    def test_conflicting_load_hint_adds_review_reason_without_override(self, broker, snapshot):
        from tests.factories import make_load

        load = make_load(snapshot=snapshot, external_load_id="29372450")
        make_load(snapshot=snapshot, external_load_id="29999999")
        inquiry = self._finalize(
            snapshot,
            hints={"suspected_load_reference_raw": "29999999"},
            output_overrides={
                "load_reference": "29372450",
                "load_reference_evidence": None,
            },
        )

        # The evidence-verified selection stands; the divergent hint is flagged.
        assert inquiry.load == load
        assert inquiry.load_resolution_status == "verified"
        assert inquiry.review_reasons.filter(code="conflicting_load_reference").exists()
