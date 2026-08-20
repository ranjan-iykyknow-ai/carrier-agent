"""Django admin (spec 3J): full inspection coverage, strictly read-only."""

import pytest
from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.auth.models import User
from django.urls import reverse

from tests.factories import (
    make_ai_operation,
    make_call_recording,
    make_candidate,
    make_communication_event,
    make_eligibility_assessment,
    make_email_content,
    make_evaluation_case,
    make_evaluation_run,
    make_extraction_run,
    make_ingestion_job,
    make_inquiry,
    make_load,
    make_market_rate,
    make_provider_call,
    make_review_action,
    make_snapshot,
    make_transcript,
)

pytestmark = pytest.mark.django_db

PROJECT_APPS = ("freight", "comms", "aiops", "inquiries", "candidates", "workspace")


def project_models():
    for app_label in PROJECT_APPS:
        yield from django_apps.get_app_config(app_label).get_models()


@pytest.fixture
def admin_client(client):
    user = User.objects.create_superuser("inspector", password="x")
    client.force_login(user)
    return client


@pytest.fixture
def world():
    snapshot = make_snapshot(is_active=True)
    load = make_load(snapshot=snapshot)
    event = make_communication_event(snapshot=snapshot)
    make_email_content(event=event)
    make_ingestion_job(event=event, status="completed")
    call_event = make_communication_event(
        snapshot=snapshot, channel="call", stable_evidence_id="call:admin_test.wav"
    )
    recording = make_call_recording(event=call_event)
    make_transcript(recording=recording, is_current=True)
    make_extraction_run(event=event, is_current=True)
    inquiry = make_inquiry(event=event, load=load)
    make_review_action(inquiry=inquiry)
    candidate = make_candidate(snapshot=snapshot)
    make_eligibility_assessment(candidate=candidate)
    operation = make_ai_operation()
    make_provider_call(operation=operation)
    make_evaluation_case(run=make_evaluation_run())
    make_market_rate(snapshot=snapshot)
    return snapshot


class TestCoverage:
    def test_every_project_model_is_registered(self):
        missing = [
            model.__name__ for model in project_models() if model not in admin.site._registry
        ]
        assert missing == []

    def test_every_changelist_renders(self, admin_client, world):
        for model in project_models():
            url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
            response = admin_client.get(url)
            assert response.status_code == 200, model.__name__

    def test_detail_pages_render_for_populated_models(self, admin_client, world):
        for model in project_models():
            obj = model.objects.first()
            if obj is None:
                continue
            url = reverse(
                f"admin:{model._meta.app_label}_{model._meta.model_name}_change",
                args=[obj.pk],
            )
            response = admin_client.get(url)
            assert response.status_code == 200, model.__name__


class TestReadOnly:
    def test_no_project_model_allows_add_change_or_delete(self, admin_client):
        request = type("Request", (), {"user": User.objects.get(username="inspector")})()
        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label not in PROJECT_APPS:
                continue
            assert model_admin.has_add_permission(request) is False, model.__name__
            assert model_admin.has_change_permission(request) is False, model.__name__
            assert model_admin.has_delete_permission(request) is False, model.__name__

    def test_add_view_is_forbidden(self, admin_client):
        response = admin_client.get(reverse("admin:freight_equipmenttype_add"))
        assert response.status_code == 403


class TestSafety:
    def test_storage_keys_never_rendered(self, admin_client, world):
        from apps.comms.models import CallRecording

        recording = CallRecording.objects.first()
        url = reverse("admin:comms_callrecording_change", args=[recording.pk])
        html = admin_client.get(url).content.decode()
        assert recording.storage_key not in html
        # Playback goes through the authenticated application endpoint instead.
        assert reverse("call_audio", args=[recording.pk]) in html

    def test_raw_provider_payloads_never_rendered(self, admin_client, world):
        from apps.comms.models import Transcript

        transcript = Transcript.objects.first()
        Transcript.objects.filter(pk=transcript.pk).update(
            provider_response={"secret_payload_marker": "dg-raw"}
        )
        url = reverse("admin:comms_transcript_change", args=[transcript.pk])
        html = admin_client.get(url).content.decode()
        assert "secret_payload_marker" not in html

    def test_anonymous_users_are_sent_to_admin_login(self, client, world):
        response = client.get(reverse("admin:comms_ingestionjob_changelist"))
        assert response.status_code == 302
        assert "/admin/login" in response.url
